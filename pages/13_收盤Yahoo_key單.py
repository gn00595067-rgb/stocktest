# -*- coding: utf-8 -*-
"""收盤 Yahoo key 單：每檔今天要怎麼改 Yahoo 投資組合，Yahoo 的已實現／庫存均價才會跟系統一致。
只讀不寫。規格：docs/specs/收盤Yahoo_key單.md
"""
import os
import sys
from datetime import date

import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.fresh import reload_stale_modules
reload_stale_modules()   # 部署後自動重新載入過期的 services／reports（免 Reboot）
from services.stock_list_loader import ensure_google_sheet_loaded
ensure_google_sheet_loaded()
from services.auth_service import ensure_bootstrap_admin, login_guard, render_auth_sidebar, filter_trades_by_permission
from services.data_cache import load_trades_rules_masters
from services.mobile_ui import inject_mobile_css
from services.prefs import DEFAULT_POLICY
from services.report_ui import render_page_notes
from reports.yahoo_keysheet import build_keysheet, key_steps

st.set_page_config(page_title="收盤Yahoo key單", layout="wide")
inject_mobile_css()
ensure_bootstrap_admin()
login_guard()
render_auth_sidebar()

st.title("收盤 Yahoo key 單")
st.caption("收盤後照著改 Yahoo，已實現和庫存均價就會跟系統一致。價格都是不含稅費（Yahoo 沒有稅費）。")

all_trades, rules, masters = load_trades_rules_masters()
trades = filter_trades_by_permission(all_trades)
users = sorted({(t.user or "").strip() for t in trades if (t.user or "").strip()})
if not users:
    st.info("尚無交易資料。")
    st.stop()

c1, c2 = st.columns([1, 2])
day = c1.date_input("日期", value=date.today(), key="yk_day")
user = c2.selectbox("買賣人（＝Yahoo 的哪個投資組合）", users, key="yk_user")

rows = build_keysheet([t for t in trades if (t.user or "").strip() == user], day, DEFAULT_POLICY, rules)
if not rows:
    st.info(f"{user} 在 {day:%Y/%m/%d} 沒有交易，Yahoo 不用動。")
    st.stop()


def _q(q, p):
    return f"{q:,} 股＠{p:.2f}" if q else "—"


for r in rows:
    m = masters.get(r["stock_id"])
    name = getattr(m, "name", None) or ""
    with st.container(border=True):
        st.markdown(f"#### {name} {r['stock_id']}")
        left, right = st.columns([1, 1])
        with left:
            st.markdown("**照這個改 Yahoo**")
            for i, s in enumerate(key_steps(r, day), 1):
                st.markdown(f"{i}. {s}")
            if r["unmatched_sell_qty"]:
                st.warning(f"有 {r['unmatched_sell_qty']:,} 股賣出在系統找不到買進可沖，Yahoo 會對不起來，請先檢查交易。")
        with right:
            st.markdown("**系統的數字**")
            tbl = pd.DataFrame([
                ("前一天庫存", _q(r["prev_qty"], r["prev_avg"])),
                ("今天買進", _q(r["buy_qty"], r["buy_avg"])),
                ("今天賣出", _q(r["sell_qty"], r["sell_avg"])),
                ("　沖掉以前的庫存", _q(r["old_sold_qty"], r["old_sold_avg"])),
                ("　沖掉今天的買進（當沖）", _q(r["dt_sold_qty"], r["dt_sold_avg"])),
                ("收盤後庫存", _q(r["end_qty"], r["end_avg"])),
            ], columns=["項目", "股數＠均價"])
            st.dataframe(tbl, hide_index=True, width="stretch")
            if r["sell_qty"]:
                line = f"今天已實現（不含稅費）：系統 **{r['realized_gross']:,.0f}**"
                if r["yahoo_realized"] is not None:
                    diff = r["yahoo_realized"] - r["realized_gross"]
                    line += f"；Yahoo 照上面 key 會是 {r['yahoo_realized']:,.0f}"
                    if abs(diff) >= 1:
                        line += f"（差 {diff:+,.0f}，價格只能填到小數 2 位造成）"
                st.markdown(line)

render_page_notes(
    "**Yahoo 怎麼算**：只有日期／股數／成交價，沒有稅費；賣出一律先扣日期最早的買進（先進先出）。",
    "**為什麼這樣改**：把今天要被沖掉的股數做成最早的一筆（通常就是把原本那一包改掉、日期不動），"
    "Yahoo 賣出時就剛好扣它，已實現＝系統的配對結果。收盤後庫存另外一筆、今天日期。",
    "**已實現不會不見**：被賣過的那筆和賣出以後都不再動；隔天只改「收盤後庫存」那筆（沒被賣過）。",
    "**盤中**：照常逐筆 key 看均價；收盤後照這頁整理。今天的買入列刪掉、賣出列股數價格都對可保留。",
    "**淨損益**（含手續費、證交稅、當沖減半）以系統的已實現損益頁為準；Yahoo 只對不含稅費的數字。",
)
