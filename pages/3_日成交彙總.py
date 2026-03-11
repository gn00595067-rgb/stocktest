# -*- coding: utf-8 -*-
"""日成交彙總"""
import io
import streamlit as st
import pandas as pd
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.stock_list_loader import ensure_google_sheet_loaded
ensure_google_sheet_loaded()
from db.database import get_session
from db.models import Trade, StockMaster
from reports.daily_summary import build_daily_summary_pivot

st.set_page_config(page_title="日成交彙總", layout="wide")
st.title("日成交彙總")

pivot_by = st.radio("彙總維度", ["date", "stock_id", "user"], format_func=lambda x: {"date": "日期", "stock_id": "股票", "user": "買賣人"}.get(x, x))

sess = get_session()
trades = sess.query(Trade).all()
masters = {m.stock_id: m for m in sess.query(StockMaster).all()}
sess.close()

if not trades:
    st.info("尚無交易")
    st.stop()

summary = build_daily_summary_pivot(trades, pivot_by, masters)

# 數字欄位：正數帶 +、千分位；負數千分位（台股習慣）
def _format_signed(val):
    if not isinstance(val, (int, float)) or pd.isna(val):
        return ""
    if val > 0:
        return f"+{int(val):,}"
    if val < 0:
        return f"{int(val):,}"
    return "0"

numeric_cols = summary.select_dtypes(include=["number"]).columns.tolist()
if numeric_cols:
    # 僅用 format 避免 Styler.map 在 Streamlit Cloud 序列化失敗；正負號與千分位仍保留
    st.dataframe(
        summary.style.format({c: _format_signed for c in numeric_cols}),
        use_container_width=True,
        hide_index=True,
    )
else:
    st.dataframe(summary, use_container_width=True, hide_index=True)

buffer = io.BytesIO()
with pd.ExcelWriter(buffer, engine="openpyxl") as w:
    summary.to_excel(w, sheet_name="彙總")
st.download_button(
    "匯出 Excel",
    data=buffer.getvalue(),
    file_name="daily_summary.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)
