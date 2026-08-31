# -*- coding: utf-8 -*-
"""已實現損益：專頁彙整所有「買→賣」沖銷後的實現損益，含 KPI、勝率/盈虧比、
每月趨勢、依股票／買賣人／產業彙總、當沖 vs 波段、完整明細與匯出。"""
import io
import sys
import os
from datetime import date

import streamlit as st
import pandas as pd
import altair as alt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.stock_list_loader import ensure_google_sheet_loaded
ensure_google_sheet_loaded()
from sqlalchemy.exc import OperationalError
from db.database import get_session
from db.models import Trade, StockMaster, CustomMatchRule
from services.auth_service import ensure_bootstrap_admin, login_guard, render_auth_sidebar, filter_trades_by_permission
from reports.realized_report import (
    build_realized_ledger, summarize_ledger, aggregate_by, monthly_series, LEDGER_COLUMNS,
)

st.set_page_config(page_title="已實現損益", layout="wide")
from services.mobile_ui import inject_mobile_css
inject_mobile_css()
ensure_bootstrap_admin()
login_guard()
render_auth_sidebar()

st.title("已實現損益")
st.caption(
    "只統計**已沖銷（買進後已賣出）**的實現損益；淨損益已扣買進手續費、賣出手續費、證交稅。"
    "尚未賣出的庫存（未實現）請看「庫存損益」頁。"
)

# ---------- 讀資料 ----------
try:
    sess = get_session()
    all_trades = sess.query(Trade).all()
    masters = {m.stock_id: m for m in sess.query(StockMaster).all()}
    custom_rules = [(r.sell_trade_id, r.buy_trade_id, r.matched_qty) for r in sess.query(CustomMatchRule).all()]
    sess.close()
    all_trades = filter_trades_by_permission(all_trades)
except OperationalError:
    st.warning("資料庫無法使用（雲端請在 Secrets 設定 USE_GOOGLE_SHEET、GOOGLE_SHEET_ID、GOOGLE_SHEET_CREDENTIALS_B64）。")
    st.stop()
except Exception:
    st.warning("無法載入交易資料。")
    st.stop()

if not all_trades:
    st.info("尚無交易資料。請先至「交易輸入」或「交易匯入」建立資料。")
    st.stop()

# ---------- 篩選 ----------
st.markdown("#### 篩選條件")
policy_labels = {
    "CUSTOM_ONLY": "僅自定沖銷",
    "CUSTOM_PLUS_FIFO": "先進先出（未定部分）",
    "CUSTOM_PLUS_CONSERVATIVE": "保守（未定部分）",
    "CUSTOM_PLUS_OPTIMISTIC": "樂觀（未定部分）",
    "CUSTOM_PLUS_MEAN": "均值配對（未定部分）",
}
users = sorted({(t.user or "").strip() for t in all_trades if (t.user or "").strip()})

fc1, fc2, fc3 = st.columns([1.2, 1.4, 1.4])
with fc1:
    policy = st.selectbox("沖銷方式", list(policy_labels.keys()), format_func=lambda x: policy_labels.get(x, x))
with fc2:
    picked_users = st.multiselect("買賣人（可多選，空=全部）", options=users, default=[])
with fc3:
    # 預設「今天」，其他區間需要時再自行切換
    range_mode = st.selectbox("賣出日期區間", ["今天", "全部", "今年", "近 12 個月", "本月", "自訂"], index=0)

today = date.today()
custom_start = custom_end = None
if range_mode == "自訂":
    dcc1, dcc2 = st.columns(2)
    with dcc1:
        custom_start = st.date_input("起（賣出日）", value=date(today.year, 1, 1), key="rz_start")
    with dcc2:
        custom_end = st.date_input("迄（賣出日）", value=today, key="rz_end")

# ---------- 建立總帳 ----------
trades_for_ledger = all_trades if not picked_users else [t for t in all_trades if (t.user or "").strip() in picked_users]
ledger = build_realized_ledger(trades_for_ledger, masters, policy, custom_rules=custom_rules)

if ledger.empty:
    st.info("目前沒有『已實現（已沖銷）』的交易。當你把某檔買進後賣出，這裡就會出現實現損益。")
    st.stop()

# 依賣出日期區間過濾
def _in_range(d):
    if d is None or (isinstance(d, float) and pd.isna(d)):
        return False
    if range_mode == "今天":
        return d == today
    if range_mode == "全部":
        return True
    if range_mode == "今年":
        return d >= date(today.year, 1, 1)
    if range_mode == "本月":
        return d >= date(today.year, today.month, 1)
    if range_mode == "近 12 個月":
        y, mth = today.year, today.month - 11
        while mth <= 0:
            mth += 12
            y -= 1
        return d >= date(y, mth, 1)
    if range_mode == "自訂":
        return (custom_start or date.min) <= d <= (custom_end or date.max)
    return True

ledger = ledger[ledger["賣出日"].map(_in_range)].reset_index(drop=True)

# 當沖 / 波段 過濾
seg = st.radio("交易類型", ["全部", "只看當沖", "只看波段（非當沖）"], horizontal=True, key="rz_seg")
if seg == "只看當沖":
    ledger = ledger[ledger["當沖"]].reset_index(drop=True)
elif seg == "只看波段（非當沖）":
    ledger = ledger[~ledger["當沖"]].reset_index(drop=True)

if ledger.empty:
    st.info("此篩選條件下沒有已實現損益。放寬日期或交易類型再看看。")
    st.stop()

s = summarize_ledger(ledger)

# ---------- KPI ----------
st.markdown("---")


def _c(v):
    """正紅負綠。"""
    return "#c00000" if v > 0 else ("#0d7a0d" if v < 0 else "#333")


k1, k2, k3, k4 = st.columns(4)
k1.markdown(
    f"<div style='font-size:13px;color:#666'>總已實現損益</div>"
    f"<div style='font-size:26px;font-weight:700;color:{_c(s['總淨損益'])}'>{s['總淨損益']:,.0f}</div>"
    f"<div style='font-size:12px;color:#888'>已實現報酬率 {s['已實現報酬率%']:.2f}%</div>",
    unsafe_allow_html=True,
)
k2.markdown(
    f"<div style='font-size:13px;color:#666'>勝率</div>"
    f"<div style='font-size:26px;font-weight:700'>{s['勝率%']:.1f}%</div>"
    f"<div style='font-size:12px;color:#888'>{s['獲利筆數']} 勝 / {s['虧損筆數']} 敗 / {s['打平筆數']} 平（共 {s['筆數']} 筆）</div>",
    unsafe_allow_html=True,
)
k3.markdown(
    f"<div style='font-size:13px;color:#666'>盈虧比（賺賠比）</div>"
    f"<div style='font-size:26px;font-weight:700'>{s['盈虧比']:.2f}</div>"
    f"<div style='font-size:12px;color:#888'>平均賺 {s['平均獲利']:,.0f} / 平均賠 {s['平均虧損']:,.0f}</div>",
    unsafe_allow_html=True,
)
k4.markdown(
    f"<div style='font-size:13px;color:#666'>每筆期望值</div>"
    f"<div style='font-size:26px;font-weight:700;color:{_c(s['期望值'])}'>{s['期望值']:,.0f}</div>"
    f"<div style='font-size:12px;color:#888'>平均每筆 {s['平均每筆']:,.0f}</div>",
    unsafe_allow_html=True,
)

k5, k6, k7, k8 = st.columns(4)
k5.metric("總賣出金額", f"{s['總賣出金額']:,.0f}")
k6.metric("總買進成本", f"{s['總買進成本']:,.0f}")
k7.metric("總費用（手續費＋稅）", f"{s['總費用']:,.0f}", help=f"手續費 {s['總手續費']:,.0f}／證交稅 {s['總證交稅']:,.0f}")
k8.metric("平均持有天數", f"{s['平均持有天數']:.1f} 天")

with st.expander("最大單筆 / 當沖 vs 波段"):
    e1, e2, e3, e4 = st.columns(4)
    e1.metric("最大單筆獲利", f"{s['最大單筆獲利']:,.0f}")
    e2.metric("最大單筆虧損", f"{s['最大單筆虧損']:,.0f}")
    e3.metric("當沖", f"{s['當沖淨損益']:,.0f}", help=f"{s['當沖筆數']} 筆")
    e4.metric("波段", f"{s['波段淨損益']:,.0f}", help=f"{s['波段筆數']} 筆")

# ---------- 每月趨勢 ----------
st.markdown("---")
st.subheader("每月已實現損益趨勢")
mdf = monthly_series(ledger)
if not mdf.empty:
    base = alt.Chart(mdf).encode(x=alt.X("月份:N", title="月份（依賣出日）"))
    bars = base.mark_bar().encode(
        y=alt.Y("當月已實現:Q", title="當月已實現"),
        color=alt.condition(alt.datum["當月已實現"] >= 0, alt.value("#e06666"), alt.value("#57bb8a")),
        tooltip=["月份", alt.Tooltip("當月已實現:Q", format=",.0f"), "筆數"],
    )
    line = base.mark_line(point=True, color="#1f77b4").encode(
        y=alt.Y("累積已實現:Q", title="累積已實現"),
        tooltip=["月份", alt.Tooltip("累積已實現:Q", format=",.0f")],
    )
    st.altair_chart(alt.layer(bars, line).resolve_scale(y="independent").properties(height=320), use_container_width=True)
else:
    st.caption("無足夠日期資料繪製月趨勢。")

# ---------- 依股票彙總 ----------
st.markdown("---")
st.subheader("依股票彙總（由賺到賠）")
by_stock = aggregate_by(ledger, "代號")
if not by_stock.empty:
    name_map = {sid: (getattr(masters.get(sid), "name", None) or "") for sid in by_stock["代號"]}
    by_stock.insert(1, "名稱", by_stock["代號"].map(name_map))
    show_cols = ["代號", "名稱", "淨損益", "報酬率%", "筆數", "獲利筆數", "勝率%", "賣出金額", "買進成本", "總費用"]
    st.dataframe(
        by_stock[show_cols].style.format({
            "淨損益": "{:,.0f}", "賣出金額": "{:,.0f}", "買進成本": "{:,.0f}", "總費用": "{:,.0f}",
            "報酬率%": "{:.2f}", "勝率%": "{:.1f}",
        }).map(lambda v: f"color:{_c(v)}" if isinstance(v, (int, float)) else "", subset=["淨損益"]),
        use_container_width=True, hide_index=True,
    )
    cwin, close = st.columns(2)
    with cwin:
        st.caption("🔴 獲利前 5")
        top = by_stock.nlargest(5, "淨損益")[["代號", "名稱", "淨損益", "報酬率%"]]
        st.dataframe(top.style.format({"淨損益": "{:,.0f}", "報酬率%": "{:.2f}"}), use_container_width=True, hide_index=True)
    with close:
        st.caption("🟢 虧損前 5")
        bot = by_stock.nsmallest(5, "淨損益")[["代號", "名稱", "淨損益", "報酬率%"]]
        st.dataframe(bot.style.format({"淨損益": "{:,.0f}", "報酬率%": "{:.2f}"}), use_container_width=True, hide_index=True)

# ---------- 依買賣人 / 產業 ----------
st.markdown("---")
cA, cB = st.columns(2)
with cA:
    st.subheader("依買賣人彙總")
    by_user = aggregate_by(ledger, "買賣人")
    if not by_user.empty:
        st.dataframe(
            by_user[["買賣人", "淨損益", "報酬率%", "筆數", "勝率%"]].style.format(
                {"淨損益": "{:,.0f}", "報酬率%": "{:.2f}", "勝率%": "{:.1f}"}),
            use_container_width=True, hide_index=True,
        )
with cB:
    st.subheader("依產業彙總")
    by_ind = aggregate_by(ledger, "產業")
    if not by_ind.empty:
        st.dataframe(
            by_ind[["產業", "淨損益", "報酬率%", "筆數", "勝率%"]].style.format(
                {"淨損益": "{:,.0f}", "報酬率%": "{:.2f}", "勝率%": "{:.1f}"}),
            use_container_width=True, hide_index=True,
        )

# ---------- 完整明細 ----------
st.markdown("---")
st.subheader("完整已實現明細（每一筆買→賣沖銷）")
disp = ledger.copy()
disp["當沖"] = disp["當沖"].map(lambda b: "當沖" if b else "")
st.dataframe(
    disp[LEDGER_COLUMNS].style.format({
        "股數": "{:,.0f}", "買價": "{:,.2f}", "賣價": "{:,.2f}",
        "買進成本": "{:,.0f}", "賣出金額": "{:,.0f}",
        "買手續費": "{:,.0f}", "賣手續費": "{:,.0f}", "證交稅": "{:,.0f}", "總費用": "{:,.0f}",
        "淨損益": "{:,.0f}", "報酬率%": "{:.2f}",
    }).map(lambda v: f"color:{_c(v)}" if isinstance(v, (int, float)) else "", subset=["淨損益"]),
    use_container_width=True, hide_index=True,
)

# ---------- 匯出 ----------
csv = ledger[LEDGER_COLUMNS].to_csv(index=False).encode("utf-8-sig")
xbuf = io.BytesIO()
with pd.ExcelWriter(xbuf, engine="openpyxl") as w:
    ledger[LEDGER_COLUMNS].to_excel(w, sheet_name="已實現明細", index=False)
    if not by_stock.empty:
        by_stock.to_excel(w, sheet_name="依股票", index=False)
    if not mdf.empty:
        mdf.to_excel(w, sheet_name="每月", index=False)
ex1, ex2 = st.columns(2)
with ex1:
    st.download_button("匯出 CSV（明細）", data=csv, file_name="realized_pnl.csv", mime="text/csv", use_container_width=True)
with ex2:
    st.download_button(
        "匯出 Excel（明細＋依股票＋每月）", data=xbuf.getvalue(),
        file_name="realized_pnl.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )
