# -*- coding: utf-8 -*-
"""當日交易明細：以「日期」為主體顯示全部原始買賣與交割加總。"""
import io
import streamlit as st
import pandas as pd
import sys
import os
from collections import defaultdict
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.stock_list_loader import ensure_google_sheet_loaded
ensure_google_sheet_loaded()
from sqlalchemy.exc import OperationalError
from db.database import get_session, USE_GOOGLE_SHEET
from db.models import Trade, StockMaster, CustomMatchRule
from services.pnl_engine import Lot, compute_matches, net_pnl_for_match
from services.auth_service import ensure_bootstrap_admin, login_guard, render_auth_sidebar, filter_trades_by_permission, is_admin
from services.prefs import resolve_default_trader, DEFAULT_POLICY
from services.report_ui import render_report_filter, trader_multiselect, render_page_notes
from services.manual_figures import load_manual_figures, save_manual_figures
from reports.daily_detail_report import build_daily_stock_summary, STOCK_SUMMARY_COLUMNS
from reports.realized_report import build_realized_ledger, monthly_series
from reports.period_profit import build_period_blocks, manual_period_keys, SYSTEM_START
from services.trader_service import all_trader_names

st.set_page_config(page_title="當日交易明細", layout="wide")
from services.mobile_ui import inject_mobile_css
inject_mobile_css()
ensure_bootstrap_admin()
login_guard()
render_auth_sidebar()
st.title("當日交易明細")

try:
    sess = get_session()
    trades = sess.query(Trade).order_by(Trade.id).all()
    masters = {m.stock_id: m for m in sess.query(StockMaster).all()}
    custom_rules = [(r.sell_trade_id, r.buy_trade_id, r.matched_qty) for r in sess.query(CustomMatchRule).order_by(CustomMatchRule.sell_trade_id, CustomMatchRule.buy_trade_id).all()]
    sess.close()
    trades = filter_trades_by_permission(trades)
except OperationalError:
    st.warning("資料庫無法使用，請重新整理；仍無法使用請通知 Jonathan。")
    st.stop()
except Exception:
    st.warning("無法載入交易資料。")
    st.stop()

if not trades:
    st.info("尚無交易資料。")
    st.stop()

stock_ids = sorted(set(str(t.stock_id).strip() for t in trades if getattr(t, "stock_id", None)))
_used = sorted(set((getattr(t, "user", None) or "").strip() for t in trades if getattr(t, "user", None)))
# 管理者：補上主檔中「還沒交易」的買賣人；一般帳號維持只看被權限限縮後出現的
users = sorted(set(_used) | set(all_trader_names())) if is_admin() else _used
dates = sorted({t.trade_date for t in trades if getattr(t, "trade_date", None)})

def _stock_label(sid: str) -> str:
    sid = str(sid).strip()
    m = masters.get(sid)
    name = (getattr(m, "name", None) or "").strip() if m else ""
    return f"{sid} {name}".strip() if name else sid

stock_opts = ["全部"] + [_stock_label(sid) for sid in stock_ids]

def _label_to_sid(lbl: str):
    if lbl == "全部":
        return None
    return str(lbl).split(" ")[0].strip()

def _style_signed(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    try:
        x = float(v)
        if x > 0:
            return "color: #c00; font-weight: 600;"
        if x < 0:
            return "color: #0d7a0d; font-weight: 600;"
    except Exception:
        return ""
    return ""


def _compute_pnl_by_sell_ids(all_trades, sell_ids: set[int]) -> dict:
    trade_by_id = {t.id: t for t in all_trades}
    pnl_by_sell_id = defaultdict(float)
    keyed = defaultdict(list)
    for t in all_trades:
        k = (str(getattr(t, "stock_id", "")).strip(), (getattr(t, "user", "") or "").strip())
        keyed[k].append(t)
    for (_sid, _u), ts in keyed.items():
        buys = [
            Lot(t.id, int(t.quantity or 0), float(t.price or 0), str(t.trade_date))
            for t in ts if (getattr(t, "side", "") or "").strip().upper() in ("BUY", "配股")
        ]
        sells = [
            Lot(t.id, int(t.quantity or 0), float(t.price or 0), str(t.trade_date))
            for t in ts if (getattr(t, "side", "") or "").strip().upper() == "SELL"
        ]
        if not buys or not sells:
            continue
        matches = compute_matches(buys, sells, policy, custom_rules=custom_rules)
        for m in matches:
            _buy_id, sell_id, _qty, _bp, _sp, _ = m
            if sell_id in sell_ids:
                pnl_by_sell_id[sell_id] += net_pnl_for_match(m, trade_by_id)
    return pnl_by_sell_id


def _build_df(scope_trades):
    sell_ids = {t.id for t in scope_trades if (getattr(t, "side", "") or "").strip().upper() == "SELL"}
    pnl_by_sell_id = _compute_pnl_by_sell_ids(trades, sell_ids)
    rows = []
    for t in sorted(scope_trades, key=lambda x: (x.trade_date, str(x.stock_id), (x.user or ""), x.id)):
        side = (getattr(t, "side", "") or "").upper()
        qty = int(getattr(t, "quantity", 0) or 0)
        price = float(getattr(t, "price", 0) or 0)
        fee = float(getattr(t, "fee", 0) or 0)
        tax = float(getattr(t, "tax", 0) or 0)
        gross = qty * price
        if side == "BUY":
            settle_in = 0.0
            settle_out = gross + fee
        else:
            settle_in = gross - fee - tax
            settle_out = 0.0
        rows.append({
            "日期": str(getattr(t, "trade_date", "")),
            "買賣人": getattr(t, "user", "") or "",
            "股票": _stock_label(getattr(t, "stock_id", "")),
            "買/賣": side,
            "股數": qty,
            "價格": price,
            "成交金額": gross,
            "手續費": fee,
            "證交稅": tax,
            "交割應收": settle_in,
            "交割應付": settle_out,
            "單筆損益": round(float(pnl_by_sell_id.get(t.id, 0)), 0) if side == "SELL" else None,
            # 原始未湊整的每筆損益，供「當日已實現損益」總額只在最後 round 一次（與已實現損益頁一致）
            "_pnl_raw": float(pnl_by_sell_id.get(t.id, 0)) if side == "SELL" else None,
            "當沖": bool(getattr(t, "is_daytrade", False)),
            "備註": (getattr(t, "note", "") or "")[:40],
        })
    df = pd.DataFrame(rows)
    df["_rank"] = (df["交割應收"] + df["交割應付"]).apply(lambda v: 0 if v else 1)
    df = df.sort_values(by=["日期", "_rank", "股票", "買賣人", "買/賣"]).drop(columns=["_rank"])
    cum = 0.0
    cum_vals = []
    for _, r in df.iterrows():
        v = r.get("單筆損益")
        if pd.notna(v):
            cum += float(v)
            cum_vals.append(round(cum, 0))
        else:
            cum_vals.append(None)
    df["累計損益"] = cum_vals
    return df


def _period_table_html(rows: list) -> str:
    """一塊期間獲利表（像 Excel：期間｜獲利）；小計、累計列固定深黃。"""
    def _amt(r):
        v = r["獲利"]
        if v is None:
            return ""
        color = "#c62828" if v > 0 else ("#2e7d32" if v < 0 else "#374151")
        tag = '<span class="pp-tag">系統</span>' if r["來源"] == "系統" else ""
        return f'{tag}<span style="color:{color}">{v:,.0f}</span>'

    body = "".join(
        f'<tr class="pp-{r["kind"]}"><td>{r["期間"]}</td><td class="num">{_amt(r)}</td></tr>' for r in rows
    )
    return f'<table class="pp-tbl"><tr><th>期間</th><th>獲利</th></tr>{body}</table>'


_PERIOD_CSS = """<style>
table.pp-tbl{width:100%;border-collapse:collapse;font-size:0.98rem;margin-bottom:8px}
table.pp-tbl th{background:#f3f4f6;color:#374151;padding:6px 10px;border:1px solid #d1d5db;text-align:center}
table.pp-tbl td{padding:5px 10px;border:1px solid #e5e7eb}
table.pp-tbl td.num{text-align:right;font-variant-numeric:tabular-nums;font-weight:600}
table.pp-tbl tr.pp-subtotal td, table.pp-tbl tr.pp-total td{background:#ffc000;font-weight:700;border-color:#e0a800}
table.pp-tbl tr.pp-balance td{font-weight:700;border-top:2px solid #6b7280}
.pp-tag{font-size:0.72rem;color:#1d4ed8;background:#e0ecff;border-radius:4px;padding:1px 5px;margin-right:6px;font-weight:500}
</style>"""


def _render_period_tab():
    """期間獲利表（Peggy Excel 版）：115/9 起用系統數字，之前與帳戶結餘手動輸入。規格見 docs/specs/Peggy回饋_2026-10-14.md §8。"""
    from db import models as _models
    if not hasattr(_models, "ManualFigure"):   # Cloud 沒重載 db.models：要 Reboot 才會建新表
        st.error("系統剛更新，資料表需要重新載入。請管理者到 Streamlit Cloud「Manage app → Reboot app」重開一次。")
        return
    if not users:
        st.info("尚無買賣人。")
        return
    st.markdown("#### 篩選條件")
    _def = resolve_default_trader(users) or users[0]
    c1, _c2 = st.columns([1.4, 3])
    trader = c1.selectbox("買賣人", users, index=users.index(_def), key="pp_trader")

    sess = get_session()
    try:
        manual = load_manual_figures(sess, trader)
    finally:
        sess.close()
    t_trades = [t for t in trades if (getattr(t, "user", None) or "").strip() == trader]
    mdf = monthly_series(build_realized_ledger(t_trades, masters, policy, custom_rules=custom_rules))
    system_monthly = dict(zip(mdf["月份"], mdf["當月已實現"])) if not mdf.empty else {}
    blocks = build_period_blocks(manual, system_monthly, date.today())

    st.markdown(_PERIOD_CSS, unsafe_allow_html=True)
    for i in range(0, len(blocks), 3):
        chunk = blocks[i:i + 3]
        cols = st.columns(max(2, len(chunk)))
        for col, rows in zip(cols, chunk):
            col.markdown(_period_table_html(rows), unsafe_allow_html=True)
    st.caption(f"標「系統」的月份＝系統已實現損益（{SYSTEM_START:%Y/%m} 起，依賣出月份）；其他為手動輸入，空白＝還沒填。")

    with st.expander("✏️ 輸入／修改手動數字", expanded=not any(v is not None for v in manual.values())):
        if USE_GOOGLE_SHEET:
            st.warning("目前是試算表模式：手動數字不會寫進試算表，重開 app 後會不見。")
        st.caption("點格子直接打數字（灰色 None＝還沒填），全部改完按「💾 儲存」。")
        keys = manual_period_keys(date.today())
        edit_df = pd.DataFrame({
            "key": [k for k, _ in keys],
            "期間": [lab for _, lab in keys],
            "獲利": [manual.get(k) for k, _ in keys],
        })
        with st.form(f"pp_form_{trader}"):
            edited = st.data_editor(
                edit_df, key=f"pp_editor_{trader}", hide_index=True, use_container_width=True,
                disabled=["期間"], num_rows="fixed",
                column_config={
                    "key": None,
                    "獲利": st.column_config.NumberColumn("獲利（帳戶結餘填金額）", format="localized", step=1),
                },
                height=35 * (len(edit_df) + 1) + 3,
            )
            saved = st.form_submit_button("💾 儲存", type="primary")
        if saved:
            values = {k: (None if pd.isna(v) else float(v)) for k, v in zip(edited["key"], edited["獲利"])}
            sess = get_session()
            try:
                n = save_manual_figures(sess, trader, values)
                sess.commit()
            except Exception as e:
                sess.rollback()
                st.error(f"儲存失敗：{e}")
                n = None
            finally:
                sess.close()
            if n is not None:
                st.toast(f"已儲存 {n} 筆變動。", icon="✅")
                st.rerun()


def _render_result(df: pd.DataFrame, pnl_label: str, dl_name: str, dl_key: str, scope_word: str):
    # 「當日已實現損益」以原始未湊整值加總、最後 round 一次，與已實現損益頁口徑一致
    realized = round(float(df["_pnl_raw"].fillna(0).sum())) if "_pnl_raw" in df.columns else (
        float(df["單筆損益"].fillna(0).sum()) if "單筆損益" in df.columns else 0.0
    )
    stock_summary = build_daily_stock_summary(df)
    df = df.drop(columns=["_pnl_raw"]) if "_pnl_raw" in df.columns else df
    fmt = {
        "股數": "{:,.0f}",
        "價格": "{:,.2f}",
        "成交金額": "{:,.0f}",
        "手續費": "{:,.0f}",
        "證交稅": "{:,.0f}",
        "交割應收": "{:,.0f}",
        "交割應付": "{:,.0f}",
        "單筆損益": "{:,.0f}",
        "累計損益": "{:,.0f}",
    }
    total_in = float(df["交割應收"].sum())
    total_out = float(df["交割應付"].sum())
    net = total_in - total_out

    # 交割加總放在明細上方：一進來先看到總數（同事回報要往下捲才看得到）
    st.subheader("交割加總")
    c1, c2, c34 = st.columns([1, 1, 2])
    c1.metric("交割應收（賣出淨入帳）", f"{total_in:,.0f}")
    c2.metric("交割應付（買進含手續費）", f"{total_out:,.0f}")
    # 淨交割、當日已實現是每天最常看的兩個數字：淺黃底標出來（Peggy 2026-10-08）
    st.markdown(
        "<style>[class*=\"st-key-dt_key_totals_\"]{background:#fff6cc;border:1px solid #f3e1a0;"
        "border-radius:10px;padding:10px 16px;}</style>",
        unsafe_allow_html=True,
    )
    with c34.container(key=f"dt_key_totals_{dl_key}"):   # 當日、區間兩個分頁都會呼叫，key 要分開
        c3, c4 = st.columns(2)
    c3.metric("淨交割（應收－應付）", f"{net:,.0f}")
    realized_cls = "#c62828" if realized >= 0 else "#2e7d32"
    c4.markdown(
        f"""
        <div style="font-size:0.95rem;color:#6b7280;margin-bottom:0.25rem;">{pnl_label}</div>
        <div style="font-size:1.9rem;font-weight:700;color:{realized_cls};">{realized:+,.0f}</div>
        """,
        unsafe_allow_html=True,
    )

    # 每股損益簡表（像 Peggy Excel 的每股合計列；Peggy 2026-10-14：先看每股，再看每筆）
    st.subheader(f"每股{scope_word}損益簡表")
    _money = {c: "{:,.0f}" for c in STOCK_SUMMARY_COLUMNS[1:]}
    n_last = len(stock_summary) - 1

    def _total_row(r):
        # 只有合計列固定深黃（Peggy：加總固定深黃色，其他不用照 Excel 上色）
        return ["background-color:#ffc000;font-weight:700"] * len(r) if r.name == n_last else [""] * len(r)

    st.dataframe(
        stock_summary.style.format(_money, na_rep="").map(_style_signed, subset=["損益"]).apply(_total_row, axis=1),
        use_container_width=True, hide_index=True, height=35 * (len(stock_summary) + 1) + 3,
    )

    st.subheader("每筆明細")
    sty = df.style.format(fmt, na_rep="—")
    for c in ["單筆損益", "累計損益"]:
        if c in df.columns:
            sty = sty.map(_style_signed, subset=[c])
    # 全部展開（每列約 35px + 表頭），不在小框內上下捲動
    st.dataframe(sty, use_container_width=True, hide_index=True, height=35 * (len(df) + 1) + 3)

    csv_buf = io.BytesIO()
    df.to_csv(csv_buf, index=False, encoding="utf-8-sig")
    st.download_button(
        f"下載{dl_name} CSV",
        data=csv_buf.getvalue(),
        file_name=f"{dl_key}.csv",
        mime="text/csv",
        key=f"dl_{dl_key}",
    )


# 沖銷口徑固定（與「已實現損益」頁相同），說明在頁尾
policy = DEFAULT_POLICY

tab_day, tab_range, tab_period = st.tabs(["當日交易明細", "區間交易明細", "期間獲利表"])

with tab_day:
    # 篩選條件：與其他報表同格式（買賣人放最右）
    st.markdown("#### 篩選條件")
    f1, f2, f3 = st.columns([1, 1.2, 1.4])
    with f1:
        day = st.date_input("日期", value=dates[-1], key="daily_page_date")
    with f2:
        stock_label_day = st.selectbox("股票", options=stock_opts, key="daily_page_stock")
    with f3:
        selected_users_day = trader_multiselect(users, "daily_page_user_multi")
    sid_filter_day = _label_to_sid(stock_label_day)
    daily = [t for t in trades if getattr(t, "trade_date", None) == day]
    if selected_users_day:
        daily = [t for t in daily if (getattr(t, "user", None) or "").strip() in selected_users_day]
    if sid_filter_day:
        daily = [t for t in daily if str(getattr(t, "stock_id", "")).strip() == sid_filter_day]
    st.caption(f"當日筆數：**{len(daily)}**")
    if not daily:
        st.info("此日期下沒有符合篩選條件的交易。")
    else:
        _render_result(_build_df(daily), "當日已實現損益", f"當日交易明細_{day}", f"daily_trades_{day}", "當日")

with tab_range:
    # 預設：最近一個有交易的日期（同舊版）；快速鈕可切當週／當月／當年…
    if "dtr_start" not in st.session_state:
        st.session_state["dtr_start"] = st.session_state["dtr_end"] = dates[-1]

    def _stock_box():
        return st.selectbox("股票", options=stock_opts, key="range_page_stock")

    start_day, end_day, selected_users_range, stock_label_range = render_report_filter(
        "dtr", users, extra=_stock_box,
    )
    sid_filter_range = _label_to_sid(stock_label_range)
    ranged = [t for t in trades if start_day <= getattr(t, "trade_date", start_day) <= end_day]
    if selected_users_range:
        ranged = [t for t in ranged if (getattr(t, "user", None) or "").strip() in selected_users_range]
    if sid_filter_range:
        ranged = [t for t in ranged if str(getattr(t, "stock_id", "")).strip() == sid_filter_range]
    st.caption(f"區間筆數：**{len(ranged)}**（{start_day} ～ {end_day}）")
    if not ranged:
        st.info("此區間下沒有符合篩選條件的交易。")
    else:
        _render_result(_build_df(ranged), "區間已實現損益", f"區間交易明細_{start_day}_{end_day}",
                       f"range_trades_{start_day}_{end_day}", "區間")

with tab_period:
    _render_period_tab()

render_page_notes(
    "以「每日」為主體列出當日全部原始買賣，並加總交割應收／應付，協助核對交割金額。",
    "**計算口徑**：賣出損益已扣買進手續費、賣出手續費、證交稅；交割加總依當日收付金額（含手續費／證交稅）估算。"
    "若券商另有其他費用／利息，請以帳單為準。",
    "**沖銷方式**：自定沖銷 ＋ 其餘先進先出（固定；所有賣出都計入），與「已實現損益」頁相同。",
    "**每股損益簡表**：買進支出＝成交金額＋手續費；賣出收入＝成交金額－手續費－證交稅；損益＝該檔在這段期間賣出的已實現損益。",
    "**期間獲利表**：115/9 月起用系統的已實現損益（依賣出月份）；之前的月份與帳戶結餘是手動輸入（在表格下方「✏️ 輸入／修改手動數字」）。",
)
