# -*- coding: utf-8 -*-
"""交易輸入頁（仿奇摩）"""
import streamlit as st
from datetime import date
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db.database import get_session
from db.models import Trade, StockMaster
from services.price_service import get_quote_cached, fetch_stock_list_cached, clear_quote_cache

st.set_page_config(page_title="交易輸入", layout="wide")
st.title("交易輸入（仿奇摩）")

session = get_session()
stocks = session.query(StockMaster).all()
stock_options = {s.stock_id: f"{s.stock_id} {s.name or ''}" for s in stocks}
session.close()

# 若有剛從搜尋加入的股票，重新載入選項
if st.session_state.get("selected_stock_id"):
    sess = get_session()
    stocks = sess.query(StockMaster).all()
    stock_options = {s.stock_id: f"{s.stock_id} {s.name or ''}" for s in stocks}
    sess.close()

# 若尚未有任何股票，仍顯示搜尋區塊讓使用者從台股列表加入
show_search_only = not stock_options

with st.expander("從台股列表搜尋股票（可輸入代號或名稱）", expanded=show_search_only):
    search_keyword = st.text_input("搜尋", placeholder="例如：2330 或 台積電", key="stock_search")
    if search_keyword and len(search_keyword.strip()) >= 1:
        try:
            full_list = fetch_stock_list_cached(ttl_seconds=3600)
            kw = search_keyword.strip().upper()
            matches = [
                s for s in full_list
                if kw in (s.get("stock_id") or "").upper() or kw in (s.get("name") or "")
            ][:80]
            if matches:
                match_options = {s["stock_id"]: f"{s['stock_id']} {s.get('name', '')}" for s in matches}
                picked = st.selectbox(
                    "選擇股票",
                    options=list(match_options.keys()),
                    format_func=lambda x: match_options.get(x, x),
                    key="search_pick",
                )
                if st.button("使用此股票並加入我的列表") and picked:
                    sess = get_session()
                    existing = sess.query(StockMaster).filter(StockMaster.stock_id == picked).first()
                    if not existing:
                        info = next((m for m in matches if m["stock_id"] == picked), {})
                        sess.add(StockMaster(
                            stock_id=picked,
                            name=info.get("name"),
                            industry_name=info.get("industry_name"),
                            market=info.get("market", "TW"),
                            exchange=info.get("exchange", "TWSE"),
                            is_etf=info.get("is_etf", False),
                        ))
                    sess.commit()
                    sess.close()
                    st.session_state["selected_stock_id"] = picked
                    st.success(f"已加入 {picked}，請在上方選擇該股票")
                    st.rerun()
            else:
                st.caption("查無符合的股票（可先至主檔/設定同步股票列表）")
        except Exception as e:
            st.caption(f"載入列表失敗：{e}。請至主檔/設定先「同步股票列表（FinMind）」")

if not stock_options and not st.session_state.get("selected_stock_id"):
    st.warning("請在上方「從台股列表搜尋」選擇股票並加入，或至「主檔/設定」載入種子資料。")
    st.stop()

col_left, col_right = st.columns([1, 1])

with col_left:
    st.subheader("交易表單")
    options = list(stock_options.keys())
    default_idx = 0
    if st.session_state.get("selected_stock_id") and st.session_state["selected_stock_id"] in options:
        default_idx = options.index(st.session_state["selected_stock_id"])
        st.session_state.pop("selected_stock_id", None)
    stock_key = st.selectbox(
        "股票代號",
        options=options,
        index=default_idx,
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
        if st.button("🔄 更新即時現價", key="refresh_quote"):
            clear_quote_cache(stock_key)
            st.rerun()
        q = get_quote_cached(stock_key)
        master = next((s for s in stocks if s.stock_id == stock_key), None)
        if q:
            st.metric("現價", f"{q['price']:.2f}", f"{q['change']:+.2f} ({q['change_pct']:+.2f}%)")
            if q.get("prev_close") is not None:
                st.caption(f"昨收：{q['prev_close']:.2f}")
            if q.get("limit_up") is not None and q.get("limit_down") is not None:
                st.caption(f"漲停：{q['limit_up']:.2f}　跌停：{q['limit_down']:.2f}")
            src = q.get("source", "")
            if src == "mock":
                st.warning("⚠️ 目前為**模擬報價**，非真實行情。請至「主檔/設定」設定 **FINMIND_TOKEN** 以取得正確即時價。")
            elif src == "finmind":
                st.caption(f"資料來源：FinMind" + (f"（{q.get('data_date', '')}）" if q.get("data_date") else ""))
        else:
            st.info("無法取得報價。請至「主檔/設定」設定 **FINMIND_TOKEN** 並按「更新即時現價」。")
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
