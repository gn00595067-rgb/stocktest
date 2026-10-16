# -*- coding: utf-8 -*-
"""已實現損益：專頁彙整所有「買→賣」沖銷後的實現損益，含 KPI、勝率/盈虧比、
每月趨勢、依股票／買賣人／產業彙總、當沖 vs 波段、完整明細與匯出。"""
import io
import sys
import os

import streamlit as st
import pandas as pd
import altair as alt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.stock_list_loader import ensure_google_sheet_loaded
ensure_google_sheet_loaded()
from sqlalchemy.exc import OperationalError
from db.database import get_session
from db.models import Trade, StockMaster, CustomMatchRule
from services.auth_service import ensure_bootstrap_admin, login_guard, render_auth_sidebar, filter_trades_by_permission, is_admin
from services.prefs import DEFAULT_POLICY
from services.report_ui import render_report_filter, render_page_notes
from services.trader_service import all_trader_names
from reports.realized_report import (
    build_realized_ledger, summarize_ledger, aggregate_by, monthly_series, rank_with_share, LEDGER_COLUMNS,
)

st.set_page_config(page_title="已實現損益", layout="wide")
from services.mobile_ui import inject_mobile_css
inject_mobile_css()
ensure_bootstrap_admin()
login_guard()
render_auth_sidebar()

st.title("已實現損益")

# ---------- 讀資料 ----------
try:
    sess = get_session()
    all_trades = sess.query(Trade).order_by(Trade.id).all()
    masters = {m.stock_id: m for m in sess.query(StockMaster).all()}
    custom_rules = [(r.sell_trade_id, r.buy_trade_id, r.matched_qty) for r in sess.query(CustomMatchRule).order_by(CustomMatchRule.sell_trade_id, CustomMatchRule.buy_trade_id).all()]
    sess.close()
    all_trades = filter_trades_by_permission(all_trades)
except OperationalError:
    st.warning("資料庫無法使用，請重新整理；仍無法使用請通知 Jonathan。")
    st.stop()
except Exception:
    st.warning("無法載入交易資料。")
    st.stop()

if not all_trades:
    st.info("尚無交易資料。請先至「交易輸入」或「交易匯入」建立資料。")
    st.stop()

# ---------- 篩選（與各報表頁同一格式；日期＝賣出日） ----------
_used = sorted({(t.user or "").strip() for t in all_trades if (t.user or "").strip()})
# 管理者：補上主檔中「還沒交易」的買賣人；一般帳號維持只看被權限限縮後出現的
users = sorted(set(_used) | set(all_trader_names())) if is_admin() else _used
# 沖銷口徑固定：自定沖銷＋其餘先進先出
policy = DEFAULT_POLICY


def _seg_box():
    return st.selectbox("交易類型", ["全部", "只看當沖", "只看波段（非當沖）"], key="rz_seg")


# 預設「當日」（原本設計：其他區間需要時再自行切換）
start_d, end_d, picked_users, seg = render_report_filter("rz", users, default_range="當日", extra=_seg_box)

# ---------- 建立總帳 ----------
trades_for_ledger = all_trades if not picked_users else [t for t in all_trades if (t.user or "").strip() in picked_users]
ledger = build_realized_ledger(trades_for_ledger, masters, policy, custom_rules=custom_rules)

if ledger.empty:
    st.info("目前沒有『已實現（已沖銷）』的交易。當你把某檔買進後賣出，這裡就會出現實現損益。")
    st.stop()


def _in_range(d):
    if d is None or (isinstance(d, float) and pd.isna(d)):
        return False
    return start_d <= d <= end_d


ledger = ledger[ledger["賣出日"].map(_in_range)].reset_index(drop=True)
if seg == "只看當沖":
    ledger = ledger[ledger["當沖"]].reset_index(drop=True)
elif seg == "只看波段（非當沖）":
    ledger = ledger[~ledger["當沖"]].reset_index(drop=True)

if ledger.empty:
    st.info(f"{start_d} ～ {end_d} 沒有已實現損益。可點上方「當月」「當年」或改日期再看看。")
    st.stop()

s = summarize_ledger(ledger)


def _c(v):
    """正紅負綠。"""
    return "#c00000" if v > 0 else ("#0d7a0d" if v < 0 else "#333")


# ---------- 1. 重點：總已實現損益（大字）＋ 其他數字表格 ----------
# Peggy 2026-10-14：「總已實現損益」才是重點卻字最小；其他數字用表格呈現、字不要太多
st.markdown("---")
st.markdown(
    f"""
<div style="display:flex;flex-wrap:wrap;gap:12px 32px;align-items:flex-end;background:#eef4ff;border:1px solid #bfd3fb;
            border-radius:12px;padding:14px 20px;margin-bottom:12px;">
  <div><div style="font-size:1rem;color:#1e3a8a;font-weight:600">總已實現損益（{start_d} ～ {end_d}）</div>
       <div style="font-size:2.4rem;font-weight:700;line-height:1.15;color:{_c(s['總淨損益'])};font-variant-numeric:tabular-nums">{s['總淨損益']:,.0f}</div></div>
  <div style="font-size:1.15rem;color:#374151;padding-bottom:6px">報酬率 <b style="color:{_c(s['已實現報酬率%'])}">{s['已實現報酬率%']:+.2f}%</b></div>
</div>
""",
    unsafe_allow_html=True,
)


def _kv_table(pairs):
    """兩列一組：上列名稱、下列數字（像 Excel 表頭＋數值）。"""
    th = "".join(f"<th>{k}</th>" for k, _ in pairs)
    td = "".join(f"<td>{v}</td>" for _, v in pairs)
    return f'<table class="rz-kv"><tr>{th}</tr><tr>{td}</tr></table>'


st.markdown(
    """<style>
table.rz-kv{width:100%;border-collapse:collapse;margin:4px 0 6px;table-layout:fixed}
table.rz-kv th{background:#f3f4f6;color:#4b5563;font-weight:600;font-size:0.92rem;padding:6px 8px;border:1px solid #e5e7eb;text-align:center}
table.rz-kv td{font-size:1.2rem;font-weight:600;padding:8px;border:1px solid #e5e7eb;text-align:center;font-variant-numeric:tabular-nums}
</style>""",
    unsafe_allow_html=True,
)
st.markdown(_kv_table([
    ("勝率", f"{s['勝率%']:.1f}%"),
    ("勝／敗／平（筆）", f"{s['獲利筆數']} ／ {s['虧損筆數']} ／ {s['打平筆數']}"),
    ("盈虧比", f"{s['盈虧比']:.2f}"),
    ("每筆期望值", f"<span style='color:{_c(s['期望值'])}'>{s['期望值']:,.0f}</span>"),
    ("平均持有天數", f"{s['平均持有天數']:.1f} 天"),
]), unsafe_allow_html=True)
st.markdown(_kv_table([
    ("總賣出金額", f"{s['總賣出金額']:,.0f}"),
    ("總買進成本（含買費）", f"{s['總買進成本']:,.0f}"),
    ("賣手續費＋證交稅", f"{s['總賣費及稅']:,.0f}"),
    ("當沖損益", f"<span style='color:{_c(s['當沖淨損益'])}'>{s['當沖淨損益']:,.0f}</span>"),
    ("波段損益", f"<span style='color:{_c(s['波段淨損益'])}'>{s['波段淨損益']:,.0f}</span>"),
]), unsafe_allow_html=True)


def _rank_style(df: pd.DataFrame):
    return df.style.format({
        "淨損益": "{:,.0f}", "佔比%": "{:.1f}%", "報酬率%": "{:.2f}", "勝率%": "{:.1f}",
    }, na_rep="—").map(lambda v: f"color:{_c(v)}" if isinstance(v, (int, float)) else "", subset=["淨損益"])


def _full_height(n: int) -> int:
    return 35 * (n + 1) + 3


# ---------- 2. 產業損益排名及佔比 ----------
st.subheader("產業損益排名及佔比")
by_ind = aggregate_by(ledger, "產業")
if not by_ind.empty:
    st.dataframe(_rank_style(rank_with_share(by_ind, ["產業"])), use_container_width=True, hide_index=True,
                 height=_full_height(len(by_ind)))

# ---------- 3. 個股損益排名及佔比 ----------
st.subheader("個股損益排名及佔比")
by_stock = aggregate_by(ledger, "代號")
if not by_stock.empty:
    name_map = {sid: (getattr(masters.get(sid), "name", None) or "") for sid in by_stock["代號"]}
    by_stock.insert(1, "名稱", by_stock["代號"].map(name_map))
    st.dataframe(_rank_style(rank_with_share(by_stock, ["代號", "名稱"])), use_container_width=True, hide_index=True,
                 height=_full_height(min(len(by_stock), 30)))

# ---------- 4. 其他：每月趨勢、依買賣人、完整明細 ----------
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

st.subheader("依買賣人彙總")
by_user = aggregate_by(ledger, "買賣人")
if not by_user.empty:
    st.dataframe(
        by_user[["買賣人", "淨損益", "報酬率%", "筆數", "勝率%"]].style.format(
            {"淨損益": "{:,.0f}", "報酬率%": "{:.2f}", "勝率%": "{:.1f}"}),
        use_container_width=True, hide_index=True,
    )

with st.expander("個股彙總（含賣出金額、買進成本、費用）"):
    if not by_stock.empty:
        show_cols = ["代號", "名稱", "淨損益", "報酬率%", "筆數", "獲利筆數", "勝率%", "賣出金額", "買進成本", "總費用"]
        st.dataframe(
            by_stock[show_cols].style.format({
                "淨損益": "{:,.0f}", "賣出金額": "{:,.0f}", "買進成本": "{:,.0f}", "總費用": "{:,.0f}",
                "報酬率%": "{:.2f}", "勝率%": "{:.1f}",
            }, na_rep="—").map(lambda v: f"color:{_c(v)}" if isinstance(v, (int, float)) else "", subset=["淨損益"]),
            use_container_width=True, hide_index=True,
        )

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
    if not by_ind.empty:
        by_ind.to_excel(w, sheet_name="依產業", index=False)
    if not mdf.empty:
        mdf.to_excel(w, sheet_name="每月", index=False)
ex1, ex2 = st.columns(2)
with ex1:
    st.download_button("匯出 CSV（明細）", data=csv, file_name="realized_pnl.csv", mime="text/csv", use_container_width=True)
with ex2:
    st.download_button(
        "匯出 Excel（明細＋依股票＋依產業＋每月）", data=xbuf.getvalue(),
        file_name="realized_pnl.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )

render_page_notes(
    "**只統計已沖銷（買進後已賣出）**的實現損益；淨損益已扣買進手續費、賣出手續費、證交稅。尚未賣出的庫存（未實現）請看「庫存損益」頁。",
    "**日期**＝賣出日。沖銷方式固定為「自定沖銷 ＋ 其餘先進先出」（所有賣出都計入）。",
    f"**對帳**：總賣出金額 {s['總賣出金額']:,.0f} − 總買進成本（含買費）{s['總買進成本']:,.0f} "
    f"− 賣手續費＋稅 {s['總賣費及稅']:,.0f} = 已實現損益 {s['總淨損益']:,.0f}"
    f"（買手續費 {s['總買手續費']:,.0f} 已計入買進成本，勿再重複扣一次）。",
    "**佔比%**：賺錢的項目＝佔「全部獲利」的比例；賠錢的項目＝佔「全部虧損」的比例。",
    f"**盈虧比**＝平均賺 ÷ 平均賠（平均賺 {s['平均獲利']:,.0f}／平均賠 {s['平均虧損']:,.0f}）；"
    "**每筆期望值**＝勝率×平均賺＋敗率×平均賠。",
    f"**最大單筆**：獲利 {s['最大單筆獲利']:,.0f}、虧損 {s['最大單筆虧損']:,.0f}（單筆＝一次買→賣沖銷配對）。",
)
