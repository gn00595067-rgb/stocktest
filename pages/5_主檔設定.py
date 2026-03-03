# -*- coding: utf-8 -*-
"""主檔/設定"""
import streamlit as st
import pandas as pd
import io
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db.database import get_session
from db.models import StockMaster
from db.seed_data import run_seed

st.set_page_config(page_title="主檔/設定", layout="wide")
st.title("主檔/設定")

st.subheader("stock_master CSV 匯入")
st.caption("CSV 欄位：stock_id, name, industry_name, market, exchange, is_etf（選填）")
uploaded = st.file_uploader("上傳 CSV", type=["csv"])
if uploaded:
    try:
        df = pd.read_csv(uploaded)
        required = ["stock_id"]
        if not all(c in df.columns for c in required):
            st.error("CSV 至少需有 stock_id 欄位")
        else:
            sess = get_session()
            for _, row in df.iterrows():
                sid = str(row["stock_id"]).strip()
                if not sid:
                    continue
                existing = sess.query(StockMaster).filter(StockMaster.stock_id == sid).first()
                name = row.get("name", existing.name if existing else None)
                industry_name = row.get("industry_name", existing.industry_name if existing else None)
                market = row.get("market", existing.market if existing else None)
                exchange = row.get("exchange", existing.exchange if existing else None)
                is_etf = bool(row.get("is_etf", False)) if "is_etf" in row else (existing.is_etf if existing else False)
                if existing:
                    existing.name = name
                    existing.industry_name = industry_name
                    existing.market = market
                    existing.exchange = exchange
                    existing.is_etf = is_etf
                else:
                    sess.add(StockMaster(stock_id=sid, name=name, industry_name=industry_name, market=market, exchange=exchange, is_etf=is_etf))
            sess.commit()
            sess.close()
            st.success("匯入完成")
    except Exception as e:
        st.error(str(e))

st.subheader("手續費/稅率設定")
fee_rate = st.number_input("手續費率（若 trades.fee 為空則用此估算）", value=0.001425, format="%.6f")
tax_rate = st.number_input("證交稅率（賣出）", value=0.003, format="%.4f")
st.caption("trades 有填 fee/tax 則以實際為準，否則用上述估算。估算邏輯可在寫入交易時套用。")

if "fee_rate" not in st.session_state:
    st.session_state["fee_rate"] = fee_rate
if "tax_rate" not in st.session_state:
    st.session_state["tax_rate"] = tax_rate
st.session_state["fee_rate"] = fee_rate
st.session_state["tax_rate"] = tax_rate

st.subheader("種子資料")
if st.button("載入種子資料（2330/2317/3706 等）"):
    run_seed()
    st.success("種子資料已寫入")

st.subheader("即時股價 API")
st.caption("FINMIND_TOKEN：有設定則使用 FinMind，否則使用 Mock。FUGLE_API_KEY：預留。")
st.code("FINMIND_TOKEN=your_token\nFUGLE_API_KEY=your_key", language="bash")
