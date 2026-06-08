# -*- coding: utf-8 -*-
"""
v2 分析報告對話 + AI 回饋蒐集

功能：
    1. 載入 v2 PipelineResult 報告（iframe 嵌 HTML）
    2. 與報告對話（Claude，prompt caching）
    3. 訊息級 👍👎 + 報告級 ⭐ 評分回饋蒐集（本地 jsonl + Google Sheet）

報告來源優先：
    1. 環境變數 V2_REPORTS_PATH（外部 taiwan-stock-analyzer/reports）
    2. stockanalysis/reports/
    3. 使用者上傳 HTML/JSON
"""
import os
import sys
import uuid
from dataclasses import asdict
from pathlib import Path

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Secrets → env
try:
    if hasattr(st, "secrets"):
        for _k in ("ANTHROPIC_API_KEY", "V2_REPORTS_PATH",
                   "WEB_FEEDBACK_SHEET_ID", "GOOGLE_SHEET_ID",
                   "GOOGLE_SHEET_CREDENTIALS", "GOOGLE_SHEET_CREDENTIALS_B64"):
            if st.secrets.get(_k):
                os.environ.setdefault(_k, str(st.secrets[_k]).strip())
except Exception:
    pass

from services.auth_service import (
    ensure_bootstrap_admin, login_guard, render_auth_sidebar,
)
from services.v2_report_context import (
    ReportEntry,
    list_available_reports,
    load_context,
    load_context_from_uploaded_json,
)
from services.v2_chat_service import ChatMessage, send_message
from services.v2_feedback_store import (
    gen_msg_id,
    record_chat_feedback,
    record_report_feedback,
)


st.set_page_config(page_title="v2 報告對話", layout="wide")
ensure_bootstrap_admin()
login_guard()
render_auth_sidebar()

st.title("📊 v2 分析報告對話")
st.caption("看完整 v2 報告 + 跟 AI 對話問問題 · 訊息級 👍👎 + 報告 ⭐ 評分都會記錄供日後優化")

# ═══════════════════════════════════════════════════════════════
# session_state
# ═══════════════════════════════════════════════════════════════

if "v2chat_history" not in st.session_state:
    st.session_state.v2chat_history: list[ChatMessage] = []
if "v2chat_session_id" not in st.session_state:
    st.session_state.v2chat_session_id = uuid.uuid4().hex[:12]
if "v2chat_selected_key" not in st.session_state:
    st.session_state.v2chat_selected_key = None
if "v2chat_uploaded_ctx" not in st.session_state:
    st.session_state.v2chat_uploaded_ctx = None
if "v2chat_uploaded_html" not in st.session_state:
    st.session_state.v2chat_uploaded_html = None


# ═══════════════════════════════════════════════════════════════
# 報告選擇
# ═══════════════════════════════════════════════════════════════

reports = list_available_reports()

with st.sidebar:
    st.markdown("---")
    st.markdown("**📊 v2 報告**")
    st.caption(f"session：`{st.session_state.v2chat_session_id}`")

    # ── 報告路徑提示 ──
    v2_path = os.environ.get("V2_REPORTS_PATH", "")
    if v2_path:
        st.caption(f"📁 報告路徑：`{v2_path}`")
    else:
        st.caption("📁 預設掃 `reports/`，可設 `V2_REPORTS_PATH` 環境變數指到 taiwan-stock-analyzer")

    if reports:
        st.success(f"找到 {len(reports)} 份報告")
    else:
        st.info("找不到本機報告，請用下方上傳功能")

    if st.button("🗑️ 清除對話", use_container_width=True, key="clr_v2"):
        st.session_state.v2chat_history = []
        st.rerun()


# ── 主區選擇方式 ──
selected_entry: ReportEntry | None = None
html_content: str | None = None
ctx_dict: dict | None = None

if reports:
    labels = {f"{r.label} · {(r.html_path or r.json_path).name}": r for r in reports}
    chosen_label = st.selectbox(
        "選擇本機報告",
        options=["（不選 / 改用上傳）"] + list(labels.keys()),
        index=1 if labels else 0,
    )
    if chosen_label != "（不選 / 改用上傳）":
        selected_entry = labels[chosen_label]

# ── 上傳區（永遠提供，當作 fallback）──
with st.expander("📤 或直接上傳 HTML / JSON 報告檔", expanded=not reports):
    col_u1, col_u2 = st.columns(2)
    with col_u1:
        up_html = st.file_uploader(
            "上傳 v2 報告 HTML（顯示用）",
            type=["html", "htm"],
            key="up_html_v2",
        )
        if up_html is not None:
            st.session_state.v2chat_uploaded_html = up_html.read().decode("utf-8", errors="replace")
            st.success(f"已載入 HTML ({len(st.session_state.v2chat_uploaded_html):,} 字)")
    with col_u2:
        up_json = st.file_uploader(
            "上傳 v2_result_*.json（對話用 context）",
            type=["json"],
            key="up_json_v2",
        )
        if up_json is not None:
            txt = up_json.read().decode("utf-8", errors="replace")
            st.session_state.v2chat_uploaded_ctx = load_context_from_uploaded_json(txt)
            st.success(f"已載入 JSON context（{st.session_state.v2chat_uploaded_ctx.date}）")


# ── 切換來源時清對話 ──
key = ""
if selected_entry:
    key = f"local_{selected_entry.date}_{selected_entry.session}"
elif st.session_state.v2chat_uploaded_ctx:
    key = f"upload_{st.session_state.v2chat_uploaded_ctx.date}"
if key and key != st.session_state.v2chat_selected_key:
    st.session_state.v2chat_history = []
    st.session_state.v2chat_selected_key = key


# ── 載入 HTML / context ──
if selected_entry and selected_entry.html_path and selected_entry.html_path.exists():
    html_content = selected_entry.html_path.read_text(encoding="utf-8")
elif st.session_state.v2chat_uploaded_html:
    html_content = st.session_state.v2chat_uploaded_html

if selected_entry:
    ctx_obj = load_context(selected_entry)
    ctx_dict = asdict(ctx_obj)
elif st.session_state.v2chat_uploaded_ctx:
    ctx_dict = asdict(st.session_state.v2chat_uploaded_ctx)


# ═══════════════════════════════════════════════════════════════
# 主畫面：tabs（報告 / 對話）
# ═══════════════════════════════════════════════════════════════

if not html_content and not ctx_dict:
    st.info("👆 請先選擇本機報告 或 上傳 HTML / JSON 檔。")
    st.stop()

tab_report, tab_chat = st.tabs(["📊 v2 報告", "💬 跟報告對話"])

with tab_report:
    if html_content:
        st.components.v1.html(html_content, height=900, scrolling=True)
    else:
        st.warning("沒有 HTML 報告可顯示。只能用 JSON context 對話。")

    st.markdown("---")
    st.markdown("### ⭐ 本份報告整體評分")
    col_r1, col_r2 = st.columns([1, 3])
    with col_r1:
        rating = st.select_slider(
            "評分", options=[1, 2, 3, 4, 5], value=4,
            help="1 = 沒幫助 / 5 = 很有幫助",
            key=f"v2_rating_{key}",
        )
    with col_r2:
        rnote = st.text_input(
            "回饋（可選）",
            placeholder="哪裡好、哪裡不夠用、希望加什麼",
            key=f"v2_rnote_{key}",
        )
    if st.button("送出報告評分", key=f"v2_rsubmit_{key}"):
        date_val = (ctx_dict or {}).get("date", "")
        sess_val = (ctx_dict or {}).get("session", "")
        try:
            user_name = st.session_state.get("user", {}).get("username", "anonymous")
        except Exception:
            user_name = "anonymous"
        record_report_feedback(
            date=date_val, session=sess_val,
            rating=int(rating), note=rnote, user_name=user_name,
        )
        st.success(f"已記錄 ⭐{rating} 評分（本地 + 嘗試同步 Google Sheet）")


with tab_chat:
    if not ctx_dict:
        st.warning("沒有 JSON context，對話功能受限。建議同時上傳 v2_result_*.json 以提供 AI 內容。")
        ctx_dict = {"date": "unknown", "note": "context 缺失"}

    # 顯示對話歷史
    for i, msg in enumerate(st.session_state.v2chat_history):
        with st.chat_message(msg.role):
            st.markdown(msg.content)
            if msg.role == "assistant":
                col_v1, col_v2, col_v3 = st.columns([1, 1, 5])
                vote_key = f"v2_voted_{msg.msg_id}"
                already = st.session_state.get(vote_key)
                with col_v1:
                    if st.button("👍", key=f"v2_up_{msg.msg_id}",
                                 disabled=already is not None):
                        try:
                            user_name = st.session_state.get("user", {}).get("username", "anonymous")
                        except Exception:
                            user_name = "anonymous"
                        record_chat_feedback(
                            msg_id=msg.msg_id, vote="up",
                            user_question=(st.session_state.v2chat_history[i-1].content
                                           if i > 0 else ""),
                            ai_answer=msg.content, model=msg.model,
                            report_date=(ctx_dict or {}).get("date", ""),
                            report_session=(ctx_dict or {}).get("session", ""),
                            session_id=st.session_state.v2chat_session_id,
                            cost_usd=msg.cost_usd, user_name=user_name,
                        )
                        st.session_state[vote_key] = "up"
                        st.rerun()
                with col_v2:
                    if st.button("👎", key=f"v2_dn_{msg.msg_id}",
                                 disabled=already is not None):
                        st.session_state[f"v2_note_show_{msg.msg_id}"] = True
                with col_v3:
                    if already:
                        st.caption(f"已記錄 {'👍' if already == 'up' else '👎'}"
                                   f" · {msg.model} · ${msg.cost_usd:.4f}")
                    else:
                        st.caption(f"{msg.model} · ${msg.cost_usd:.4f}")

                # 👎 開放回饋
                if st.session_state.get(f"v2_note_show_{msg.msg_id}") and not already:
                    n = st.text_input(
                        "可補充：哪裡沒抓到重點？",
                        key=f"v2_note_input_{msg.msg_id}",
                    )
                    if st.button("送出回饋", key=f"v2_note_submit_{msg.msg_id}"):
                        try:
                            user_name = st.session_state.get("user", {}).get("username", "anonymous")
                        except Exception:
                            user_name = "anonymous"
                        record_chat_feedback(
                            msg_id=msg.msg_id, vote="down", note=n,
                            user_question=(st.session_state.v2chat_history[i-1].content
                                           if i > 0 else ""),
                            ai_answer=msg.content, model=msg.model,
                            report_date=(ctx_dict or {}).get("date", ""),
                            report_session=(ctx_dict or {}).get("session", ""),
                            session_id=st.session_state.v2chat_session_id,
                            cost_usd=msg.cost_usd, user_name=user_name,
                        )
                        st.session_state[vote_key] = "down"
                        st.session_state[f"v2_note_show_{msg.msg_id}"] = False
                        st.rerun()

    # 快速問題
    if not st.session_state.v2chat_history:
        st.markdown("##### 💡 快速提問（直接點選）")
        quick_qs = [
            "今日大盤情境是什麼？為什麼是這個判斷？",
            "可執行清單上的個股，最值得關注哪一檔？",
            "持股有哪些急事需要處理？",
            "為什麼這檔被擋下不能新進場？",
            "比較今日觀察名單跟可執行的差別",
            "深入分析持股組合的風險",
        ]
        cols = st.columns(2)
        for i, q in enumerate(quick_qs):
            with cols[i % 2]:
                if st.button(q, use_container_width=True, key=f"v2_qq_{i}"):
                    st.session_state._v2_pending_q = q
                    st.rerun()

    # 輸入處理
    pending_q = st.session_state.pop("_v2_pending_q", None)
    user_input = pending_q or st.chat_input(
        "輸入問題（含「互審/比較/深入」會自動升級 Opus）"
    )
    if user_input:
        st.session_state.v2chat_history.append(ChatMessage(
            role="user", content=user_input, msg_id=gen_msg_id(),
        ))
        with st.chat_message("user"):
            st.markdown(user_input)
        with st.chat_message("assistant"):
            with st.spinner("AI 思考中..."):
                history_before = st.session_state.v2chat_history[:-1]
                reply = send_message(
                    user_msg=user_input,
                    context=ctx_dict,
                    history=history_before,
                )
            reply.msg_id = gen_msg_id()
            st.markdown(reply.content)
        st.session_state.v2chat_history.append(reply)
        st.rerun()
