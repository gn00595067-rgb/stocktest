# -*- coding: utf-8 -*-
"""AI 問答助理 — 整合實際持倉與即時股價，用 Claude 回答投資問題"""
import os
import sys

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 同步 Streamlit Cloud Secrets → os.environ（與其他頁面相同做法）
try:
    if hasattr(st, "secrets"):
        for _key in ("FINMIND_TOKEN", "ANTHROPIC_API_KEY"):
            if st.secrets.get(_key):
                os.environ.setdefault(_key, str(st.secrets[_key]).strip())
except Exception:
    pass

from services.stock_list_loader import ensure_google_sheet_loaded
from services.auth_service import (
    ensure_bootstrap_admin,
    login_guard,
    render_auth_sidebar,
    filter_trades_by_permission,
)
from services.position_cost import compute_position_and_cost_by_stock
from services.price_service import get_quote_cached
from db.database import get_session
from db.models import Trade, StockMaster, CustomMatchRule

ensure_google_sheet_loaded()

st.set_page_config(page_title="AI 問答助理", layout="wide")
ensure_bootstrap_admin()
login_guard()
render_auth_sidebar()

st.title("🤖 AI 問答助理")
st.caption("根據你的實際持倉與即時股價，由 Claude AI 回答投資問題。")

# ─── 確認 API Key ────────────────────────────────────────────────
_api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
if not _api_key:
    st.error(
        "❌ 尚未設定 **ANTHROPIC_API_KEY**。\n\n"
        "請至 Streamlit Cloud → 你的 App → **Settings → Secrets**，"
        "新增以下內容後儲存並重新部署：\n```\nANTHROPIC_API_KEY = \"sk-ant-...\"\n```"
    )
    st.stop()

try:
    from anthropic import Anthropic
    _client = Anthropic(api_key=_api_key)
except ImportError:
    st.error("❌ 找不到 `anthropic` 套件，請確認 `requirements.txt` 已加入 `anthropic` 並重新部署。")
    st.stop()


# ─── 讀取持倉並抓報價 ────────────────────────────────────────────
@st.cache_data(ttl=120, show_spinner=False)
def _load_holdings() -> list[dict]:
    session = get_session()
    try:
        trades = session.query(Trade).all()
        masters = {m.stock_id: m.name for m in session.query(StockMaster).all()}
        rules = [
            (r.sell_trade_id, r.buy_trade_id, r.matched_qty)
            for r in session.query(CustomMatchRule).all()
        ]
    finally:
        session.close()

    filtered = filter_trades_by_permission(trades)
    positions = compute_position_and_cost_by_stock(filtered, rules)

    rows = []
    for stock_id, pos in positions.items():
        qty = pos["qty"]
        if qty <= 0:
            continue
        avg_cost = pos["cost"] / qty
        name = masters.get(stock_id, stock_id)
        quote = get_quote_cached(stock_id)
        current_price = quote["price"] if quote else None
        unrealized_pnl_pct = (
            (current_price - avg_cost) / avg_cost * 100
            if current_price and avg_cost
            else None
        )
        market_value = current_price * qty if current_price else None
        rows.append({
            "stock_id":         stock_id,
            "name":             name,
            "qty":              qty,
            "avg_cost":         round(avg_cost, 2),
            "current_price":    current_price,
            "unrealized_pct":   round(unrealized_pnl_pct, 1) if unrealized_pnl_pct is not None else None,
            "market_value":     round(market_value, 0) if market_value else None,
        })

    rows.sort(key=lambda x: -(x["market_value"] or 0))
    return rows


def _build_system_prompt(holdings: list[dict]) -> str:
    lines = [
        "你是一位專業的台股投資助理，使用者已登入並授權你存取他的實際持倉資料。",
        "請用繁體中文回答，語氣專業但親切，數字要精確，盡量簡潔有重點。",
        "回答涉及未來走勢時，請明確說明為分析觀點，非投資建議。",
        "",
        "【使用者目前持倉（含即時報價）】",
    ]
    if holdings:
        total_mv = sum(h["market_value"] for h in holdings if h["market_value"])
        for h in holdings:
            price_str = f"現價 ${h['current_price']}" if h["current_price"] else "現價未知"
            pct_str   = f"，損益 {h['unrealized_pct']:+.1f}%" if h["unrealized_pct"] is not None else ""
            mv_str    = f"，市值 ${h['market_value']:,.0f}" if h["market_value"] else ""
            lines.append(
                f"- {h['name']}（{h['stock_id']}）：{h['qty']} 股，"
                f"均成本 ${h['avg_cost']:.2f}，{price_str}{pct_str}{mv_str}"
            )
        lines.append(f"\n總市值估算：${total_mv:,.0f} 元")
    else:
        lines.append("- 目前無持倉資料（可能尚未輸入任何交易）")
    return "\n".join(lines)


# ─── 側欄：持倉摘要 ──────────────────────────────────────────────
with st.spinner("載入持倉資料..."):
    try:
        holdings = _load_holdings()
    except Exception as _e:
        holdings = []
        st.warning(f"持倉資料載入失敗：{_e}")

with st.sidebar:
    st.markdown("---")
    st.markdown("**📋 目前持倉**")
    if holdings:
        for h in holdings:
            pct   = f"{h['unrealized_pct']:+.1f}%" if h["unrealized_pct"] is not None else "—"
            emoji = "🟢" if (h["unrealized_pct"] or 0) >= 0 else "🔴"
            st.markdown(f"{emoji} **{h['name']}**（{h['stock_id']}）`{pct}`")
    else:
        st.caption("無持倉資料")
    st.markdown("")
    if st.button("🔄 重新整理報價", use_container_width=True):
        st.cache_data.clear()
        st.rerun()


# ─── 對話區 ──────────────────────────────────────────────────────
if "ai_chat_history" not in st.session_state:
    st.session_state.ai_chat_history = []

# 快速提問按鈕（只在對話空白時顯示）
if not st.session_state.ai_chat_history:
    st.markdown("##### 💡 快速提問")
    quick_questions = [
        "目前持倉整體損益狀況如何？",
        "哪一檔虧損最多？該怎麼處理？",
        "目前持倉有哪些需要注意的風險？",
        "哪幾檔表現最好？原因可能是什麼？",
    ]
    cols = st.columns(2)
    for i, q in enumerate(quick_questions):
        with cols[i % 2]:
            if st.button(q, use_container_width=True, key=f"quick_{i}"):
                st.session_state.ai_chat_history.append({"role": "user", "content": q})
                st.rerun()
    st.markdown("---")

# 顯示歷史對話
for msg in st.session_state.ai_chat_history:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# 輸入框
if prompt := st.chat_input("問任何關於持倉的投資問題..."):
    st.session_state.ai_chat_history.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    system_prompt = _build_system_prompt(holdings)

    with st.chat_message("assistant"):
        try:
            with _client.messages.stream(
                model="claude-sonnet-4-6",
                max_tokens=1500,
                system=system_prompt,
                messages=st.session_state.ai_chat_history,
            ) as stream:
                response_text = st.write_stream(stream.text_stream)
        except Exception as e:
            response_text = f"⚠️ 呼叫 AI 時發生錯誤：{e}"
            st.error(response_text)

    st.session_state.ai_chat_history.append({"role": "assistant", "content": response_text})

# 清除對話按鈕
if st.session_state.ai_chat_history:
    st.markdown("")
    if st.button("🗑️ 清除對話記錄", use_container_width=False):
        st.session_state.ai_chat_history = []
        st.rerun()
