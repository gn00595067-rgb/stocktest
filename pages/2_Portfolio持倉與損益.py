# -*- coding: utf-8 -*-
"""Portfolio 持倉與損益"""
import streamlit as st
from datetime import date
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    if hasattr(st, "secrets") and st.secrets.get("FINMIND_TOKEN"):
        os.environ.setdefault("FINMIND_TOKEN", str(st.secrets["FINMIND_TOKEN"]).strip())
except Exception:
    pass
from db.database import get_session
from db.models import Trade, StockMaster
from reports.portfolio_report import build_portfolio_df
from services.price_service import get_quote_cached

st.set_page_config(page_title="Portfolio", layout="wide")
st.title("Portfolio 持倉與損益")

start_date = st.date_input("開始日期", value=date(2020, 1, 1))
end_date = st.date_input("結束日期", value=date.today())
policy = st.selectbox(
    "損益沖銷 policy",
    ["FIFO", "LIFO", "MINCOST", "MAXCOST", "AVERAGE", "CLOSEST"],
    format_func=lambda x: {
        "FIFO": "FIFO（先買先賣）",
        "LIFO": "LIFO（後買先賣）",
        "MINCOST": "MINCOST（樂觀）",
        "MAXCOST": "MAXCOST（保守）",
        "AVERAGE": "AVERAGE（均價）",
        "CLOSEST": "CLOSEST（最接近兩平）",
    }.get(x, x),
)

sess = get_session()
trades = sess.query(Trade).filter(Trade.trade_date >= start_date, Trade.trade_date <= end_date).all()
all_trades = sess.query(Trade).all()
masters = {m.stock_id: m for m in sess.query(StockMaster).all()}
sess.close()

df, df_industry, df_user = build_portfolio_df(
    all_trades, masters, start_date, end_date, policy, get_quote_cached
)

if not df.empty:
    st.dataframe(df, use_container_width=True, hide_index=True)
    st.subheader("按產業小計")
    st.dataframe(df_industry, use_container_width=True, hide_index=True)
    st.subheader("按買賣人小計")
    if not df_user.empty:
        st.dataframe(df_user, use_container_width=True, hide_index=True)
    else:
        st.caption("無依買賣人區分之持倉")
else:
    st.info("無持倉資料（或區間內無交易）")
