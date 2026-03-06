# -*- coding: utf-8 -*-
"""交易輸入頁（仿奇摩）"""
import streamlit as st
from datetime import date
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db.database import get_session
from db.models import Trade, StockMaster
from services.price_service import get_quote_cached

st.set_page_config(page_title="交易輸入", layout="wide")
st.title("交易輸入（仿奇摩）")

session = get_session()
stocks = session.query(StockMaster).all()
stock_options = {s.stock_id: f"{s.stock_id} {s.name or ''}" for s in stocks}
session.close()

if not stock_options:
    st.warning("請先至「主檔/設定」載入種子資料或匯入 stock_master。")
    st.stop()

col_left, col_right = st.columns([1, 1])

with col_left:
    st.subheader("交易表單")
    stock_key = st.selectbox(
        "股票代號",
        options=list(stock_options.keys()),
        format_func=lambda x: stock_options.get(x, x),
    )
    user = st.text_input("買賣人", value=st.session_state.get("last_user", ""))
    trade_date = st.date_input("日期", value=st.session_state.get("last_date", date.today()))
    side = st.radio("買/賣", ["BUY", "SELL"], horizontal=True)
    quote = get_quote_cached(stock_key) if stock_key else None
    default_price = float(quote["price"]) if quote else 0.0
    price = st.number_input("價格", value=default_price, min_value=0.0, step=0.01, format="%.2f")
    quantity = st.number_input("股數", min_value=1, value=1000, step=100)
    is_daytrade = st.checkbox("是否當沖", value=False)
    note = st.text_input("備註", value="")
    if st.button("送出"):
        sess = get_session()
        t = Trade(
            user=user,
            stock_id=stock_key,
            trade_date=trade_date,
            side=side,
            price=price,
            quantity=int(quantity),
            is_daytrade=is_daytrade,
            note=note or None,
        )
        sess.add(t)
        sess.commit()
        sess.close()
        st.session_state["last_user"] = user
        st.session_state["last_date"] = trade_date
        st.success("已新增一筆交易")
        st.rerun()

with col_right:
    st.subheader("報價卡")
    if stock_key:
        q = get_quote_cached(stock_key)
        master = next((s for s in stocks if s.stock_id == stock_key), None)
        if q:
            st.metric("現價", f"{q['price']:.2f}", f"{q['change']:+.2f} ({q['change_pct']:+.2f}%)")
            if q.get("prev_close") is not None:
                st.caption(f"昨收：{q['prev_close']:.2f}")
            if q.get("limit_up") is not None and q.get("limit_down") is not None:
                st.caption(f"漲停：{q['limit_up']:.2f}　跌停：{q['limit_down']:.2f}")
        else:
            st.info("無法取得報價（使用 Mock 或檢查 FINMIND_TOKEN）")
        st.caption(f"名稱：{master.name if master else '-'}")
        st.caption(f"產業：{master.industry_name if master else '-'}")

st.subheader("今日交易列表")
sess = get_session()
today_trades = sess.query(Trade).filter(Trade.trade_date == trade_date).order_by(Trade.id).all()
if today_trades:
    import pandas as pd
    data = [
        {
            "id": t.id,
            "股票": t.stock_id,
            "買賣人": t.user,
            "買/賣": t.side,
            "價格": t.price,
            "股數": t.quantity,
            "當沖": t.is_daytrade,
            "備註": t.note or "",
        }
        for t in today_trades
    ]
    df = pd.DataFrame(data)
    st.data_editor(df, use_container_width=True, disabled=["id"], hide_index=True)
    del_id = st.number_input("要刪除的交易 ID", min_value=0, value=0, step=1, key="del_trade_id")
    if st.button("刪除該筆交易") and del_id:
        sess.query(Trade).filter(Trade.id == int(del_id)).delete()
        sess.commit()
        st.success("已刪除")
        st.rerun()
else:
    st.info("本日尚無交易")
sess.close()
