# -*- coding: utf-8 -*-
"""日成交明細表：當日每筆交易的詳細紀錄（參考券商日成交表格式）"""
import io
import streamlit as st
import pandas as pd
from datetime import date
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.stock_list_loader import ensure_google_sheet_loaded
ensure_google_sheet_loaded()
from db.database import get_session
from db.models import Trade, StockMaster, CustomMatchRule
from reports.daily_detail_report import build_daily_detail_df

st.set_page_config(page_title="日成交明細", layout="wide")
st.title("日成交明細表")

query_date = st.date_input("查詢日期", value=date.today())
show_all = st.checkbox("顯示全部日期（不限當日）", value=False)
policy = st.selectbox(
    "損益沖銷方式（影響賣出損益計算）",
    ["FIFO", "LIFO", "AVERAGE", "CUSTOM"],
    index=3,
    format_func=lambda x: {"FIFO": "FIFO（先買先賣）", "LIFO": "LIFO（後買先賣）", "AVERAGE": "AVERAGE（均價）", "CUSTOM": "自定沖銷"}.get(x, x),
)

sess = get_session()
all_trades = sess.query(Trade).all()
masters = {m.stock_id: m for m in sess.query(StockMaster).all()}
custom_rules = None
if policy == "CUSTOM":
    custom_rules = [(r.sell_trade_id, r.buy_trade_id, r.matched_qty) for r in sess.query(CustomMatchRule).all()]
sess.close()

if not all_trades:
    st.info("尚無交易")
    st.stop()

# 損益需依全部交易沖銷；若只顯示當日則篩選結果
filter_date = None if show_all else query_date
if not show_all and not any(t.trade_date == query_date for t in all_trades):
    st.info(f"{query_date} 尚無交易")
    st.stop()

detail = build_daily_detail_df(all_trades, masters, policy=policy, filter_date=filter_date, custom_rules=custom_rules)

# 正數紅、負數綠（台股習慣）
def _style_signed(val):
    if val is None or (isinstance(val, float) and pd.isna(val)) or val == "":
        return ""
    if isinstance(val, (int, float)):
        if val > 0:
            return "color: #c00; font-weight: 500;"
        if val < 0:
            return "color: #0d7a0d; font-weight: 500;"
    return ""

def _fmt_num(val):
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return ""
    try:
        v = float(val)
        if v == int(v):
            return f"{int(v):,}"
        return f"{v:,.2f}"
    except (ValueError, TypeError):
        return str(val)

signed_cols = [c for c in ["成交金額", "淨收付", "損益"] if c in detail.columns]
numeric_cols = [c for c in detail.columns if c in ["股數", "股價", "成交金額", "手續費", "證交稅", "淨收付", "損益"]]
fmt_map = {c: _fmt_num for c in numeric_cols}

if signed_cols:
    styled = detail.style.format(fmt_map).applymap(_style_signed, subset=signed_cols)
else:
    styled = detail.style.format(fmt_map) if fmt_map else detail

st.dataframe(styled, use_container_width=True, hide_index=True)

buffer = io.BytesIO()
with pd.ExcelWriter(buffer, engine="openpyxl") as w:
    detail.to_excel(w, sheet_name="日成交明細", index=False)
st.download_button(
    "匯出 Excel",
    data=buffer.getvalue(),
    file_name=f"daily_detail_{query_date}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)
