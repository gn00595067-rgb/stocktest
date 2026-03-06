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
from services.price_service import fetch_stock_list_finmind

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

st.subheader("從 FinMind 同步股票列表")
st.caption("從 FinMind TaiwanStockInfo API 取得上市櫃清單，寫入 stock_master。需設定 FINMIND_TOKEN（可選，v3 免 token 但較慢）。")
if st.button("同步股票列表（FinMind）"):
    lst = fetch_stock_list_finmind()
    if not lst:
        st.warning("無法取得清單（請檢查網路或 FINMIND_TOKEN）")
    else:
        sess = get_session()
        try:
            for item in lst:
                existing = sess.query(StockMaster).filter(StockMaster.stock_id == item["stock_id"]).first()
                if existing:
                    existing.name = item["name"]
                    existing.industry_name = item.get("industry_name") or existing.industry_name
                    existing.market = item.get("market") or existing.market
                    existing.exchange = item.get("exchange") or existing.exchange
                    existing.is_etf = item.get("is_etf", False)
                else:
                    sess.add(StockMaster(
                        stock_id=item["stock_id"],
                        name=item["name"],
                        industry_name=item.get("industry_name"),
                        market=item.get("market", "TW"),
                        exchange=item.get("exchange", "TWSE"),
                        is_etf=item.get("is_etf", False),
                    ))
            sess.commit()
            st.success(f"已同步 {len(lst)} 筆股票至 stock_master")
        finally:
            sess.close()

st.subheader("即時股價 API")
try:
    secret_token = st.secrets.get("FINMIND_TOKEN", "") if hasattr(st, "secrets") else ""
except Exception:
    secret_token = ""
token_set = bool(os.environ.get("FINMIND_TOKEN") or secret_token)
if token_set:
    st.success("**FINMIND_TOKEN：已設定 ✓** 報價應為 FinMind 真實資料，請到「交易輸入」按「更新即時現價」確認。")
else:
    st.warning("**FINMIND_TOKEN：未設定 ✗** 目前報價為模擬數據（例如 2330 固定 580）。")
st.markdown("**若要顯示正確即時／收盤價，必須設定 FINMIND_TOKEN。** 未設定時報價卡會顯示「模擬報價」（例如 2330 固定 580），僅供測試。")
st.caption("**若您是在 Streamlit Cloud 上執行**：Cloud 不會讀取 repo 裡的 .env，請務必到 App → **Settings** → **Secrets** 新增：`FINMIND_TOKEN = \"你的token\"`，存檔後等重新部署。")
st.caption("本機執行：在專案根目錄放 `.env`，內容為 `FINMIND_TOKEN=你的token`，並**重新啟動** Streamlit。")
st.caption("詳細步驟請見專案中的 **FinMind_Token取得步驟.md**，或依下列簡述操作：")
st.markdown("""
1. 打開 **https://finmindtrade.com** → 點「登入」或「註冊」  
2. **註冊**：填信箱、密碼 → 收信點驗證連結  
3. **登入**：https://finmindtrade.com/analysis/#/account/login  
4. 登入後進入 **使用者資訊／帳戶／API** 頁面，複製 **API Token**  
5. 本機：在專案 `.env` 加上 `FINMIND_TOKEN=你的token`，重啟 Streamlit  
   Cloud：App → Settings → Secrets → 新增 `FINMIND_TOKEN = "你的token"`  
""")
st.code("FINMIND_TOKEN=your_token\nFUGLE_API_KEY=your_key  # 預留", language="bash")
