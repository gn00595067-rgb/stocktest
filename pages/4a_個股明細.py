# -*- coding: utf-8 -*-
"""個股明細表：單一股票的「已出售」與「庫存」明細（參考券商格式）"""
import io
import streamlit as st
import pandas as pd
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.stock_list_loader import ensure_google_sheet_loaded
ensure_google_sheet_loaded()
from db.database import get_session
from db.models import Trade, StockMaster, CustomMatchRule
from reports.stock_detail_report import build_stock_detail

st.set_page_config(page_title="個股明細", layout="wide")
st.title("個股明細表")

sess = get_session()
trades = sess.query(Trade).all()
masters = {m.stock_id: m for m in sess.query(StockMaster).all()}
custom_rules_list = [(r.sell_trade_id, r.buy_trade_id, r.matched_qty) for r in sess.query(CustomMatchRule).all()]
sess.close()

# 有交易紀錄的股票清單
stock_ids = sorted(set(t.stock_id for t in trades))
if not stock_ids:
    st.info("尚無交易，無法顯示個股明細")
    st.stop()

stock_options = {}
for sid in stock_ids:
    m = masters.get(sid)
    name = getattr(m, "name", None) or ""
    stock_options[sid] = f"{sid} {name}".strip() if name else sid

policy = st.selectbox(
    "損益沖銷方式",
    ["FIFO", "LIFO", "AVERAGE", "CUSTOM"],
    index=3,
    format_func=lambda x: {"FIFO": "FIFO（先買先賣）", "LIFO": "LIFO（後買先賣）", "AVERAGE": "AVERAGE（均價）", "CUSTOM": "自定沖銷"}.get(x, x),
)
selected_id = st.selectbox("選擇股票", options=list(stock_options.keys()), format_func=lambda x: stock_options.get(x, x))

sold_df, sold_revenue, inv_df, inv_summary = build_stock_detail(selected_id, trades, masters, policy, custom_rules=custom_rules_list if policy == "CUSTOM" else None)
company_label = stock_options.get(selected_id, selected_id)


def _style_signed(val):
    if val is None or (isinstance(val, float) and pd.isna(val)):
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
        return f"{int(v):,}" if v == int(v) else f"{v:,.2f}"
    except (ValueError, TypeError):
        return str(val)


# ---------- 已出售 ----------
st.subheader("已出售")
st.caption(f"**{company_label}** · 以下為已沖銷的「買→賣」明細，每列一筆沖銷；**買/賣** 欄為「買→賣」表示該列為買進後賣出，實際賣出資訊見 **出售日、賣價、賣出金額**。")
if sold_df.empty:
    st.caption("此股票尚無已出售紀錄")
else:
    sold_cols_num = [c for c in sold_df.columns if sold_df[c].dtype in ("int64", "float64")]
    fmt_sold = {c: _fmt_num for c in sold_cols_num}
    style_sold = [c for c in ["單筆損益", "累計損益"] if c in sold_df.columns]
    if style_sold:
        st.dataframe(
            sold_df.style.format(fmt_sold).applymap(_style_signed, subset=style_sold),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.dataframe(sold_df.style.format(fmt_sold), use_container_width=True, hide_index=True)
    with st.expander("已出售 損益分析"):
        st.metric("總賣出金額（營收）", f"{sold_revenue:,.0f}" if sold_revenue else "0", None)


# ---------- 庫存 ----------
st.subheader("庫存")
st.caption(f"**{company_label}** · 尚未賣出的買單明細")
if inv_df.empty:
    st.caption("此股票目前無庫存（已全數賣出或尚無買進）")
else:
    inv_cols_num = [c for c in inv_df.columns if inv_df[c].dtype in ("int64", "float64")]
    fmt_inv = {c: _fmt_num for c in inv_cols_num}
    style_inv = [c for c in ["單筆損益", "累計損益"] if c in inv_df.columns]
    if style_inv:
        st.dataframe(
            inv_df.style.format(fmt_inv).applymap(_style_signed, subset=style_inv),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.dataframe(inv_df.style.format(fmt_inv), use_container_width=True, hide_index=True)

    st.caption("**小計**")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("庫存股數", f"{inv_summary['庫存股數']:,}", None)
    c2.metric("原始成本", f"{inv_summary['原始成本']:,.0f}", None)
    c3.metric("原始均價", f"{inv_summary['原始均價']:,.2f}", None)
    c4.metric("結算後均價", f"{inv_summary['結算後均價']:,.2f}", None)

# ---------- 匯出 Excel ----------
buffer = io.BytesIO()
with pd.ExcelWriter(buffer, engine="openpyxl") as w:
    if not sold_df.empty:
        sold_df.to_excel(w, sheet_name="已出售", index=False)
    if not inv_df.empty:
        inv_df.to_excel(w, sheet_name="庫存", index=False)
st.download_button(
    "匯出 Excel（已出售＋庫存）",
    data=buffer.getvalue(),
    file_name=f"stock_detail_{selected_id}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)
