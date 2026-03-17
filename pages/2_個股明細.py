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
from sqlalchemy.exc import OperationalError
from db.database import get_session
from db.models import Trade, StockMaster, CustomMatchRule
from reports.stock_detail_report import build_stock_detail

st.set_page_config(page_title="個股明細", layout="wide")
st.title("個股明細表")

try:
    sess = get_session()
    trades = sess.query(Trade).all()
    masters = {m.stock_id: m for m in sess.query(StockMaster).all()}
    custom_rules_list = [(r.sell_trade_id, r.buy_trade_id, r.matched_qty) for r in sess.query(CustomMatchRule).all()]
    sess.close()
except OperationalError:
    st.warning("資料庫無法使用（雲端部署請在 Secrets 設定 USE_GOOGLE_SHEET、GOOGLE_SHEET_ID、GOOGLE_SHEET_CREDENTIALS_B64）。")
    st.stop()
except Exception:
    st.warning("無法載入交易資料。")
    st.stop()

# 有交易紀錄的股票清單與買賣人
stock_ids = sorted(set(t.stock_id for t in trades))
detail_users = sorted(set(t.user for t in trades if getattr(t, "user", None)))
if not stock_ids:
    st.info("尚無交易，無法顯示個股明細")
    st.stop()

stock_options = {}
for sid in stock_ids:
    m = masters.get(sid)
    name = getattr(m, "name", None) or ""
    stock_options[sid] = f"{sid} {name}".strip() if name else sid

c_policy, c_stock, c_user = st.columns([1, 1.5, 1])
with c_policy:
    policy = st.selectbox(
        "損益沖銷方式",
        ["CUSTOM"],
        format_func=lambda x: "自定沖銷",
    )
with c_stock:
    selected_id = st.selectbox("選擇股票", options=list(stock_options.keys()), format_func=lambda x: stock_options.get(x, x))
with c_user:
    user_opts = ["全部"] + detail_users
    user_idx = st.selectbox("買賣人", range(len(user_opts)), format_func=lambda i: user_opts[i], key="detail_filter_user")
    detail_filter_users = None if user_idx == 0 else [user_opts[user_idx]]

trades_for_detail = trades if detail_filter_users is None else [t for t in trades if t.user in detail_filter_users]
sold_df, sold_revenue, inv_df, inv_summary = build_stock_detail(selected_id, trades_for_detail, masters, policy, custom_rules=custom_rules_list)
company_label = stock_options.get(selected_id, selected_id)

# ---------- 原始交易紀錄（除錯／與 Excel 比對） ----------
stock_trades = [t for t in trades_for_detail if t.stock_id == selected_id]
with st.expander("📋 此股票全部交易原始資料（與 Excel 比對用）", expanded=False):
    st.caption("下表為系統內此股票的所有買進／賣出筆數。若與您手邊 Excel 筆數或單筆「股價」不一致，可能是重複匯入、漏匯或匯入時欄位解析錯誤。均價異常時請檢查是否有單筆價格異常（例如 >500 或接近 788）。")
    if not stock_trades:
        st.caption("尚無交易。")
    else:
        raw_rows = []
        for t in sorted(stock_trades, key=lambda x: (x.trade_date, x.id)):
            raw_rows.append({
                "id": t.id,
                "買賣人": getattr(t, "user", None) or "",
                "日期": str(t.trade_date),
                "買/賣": (t.side or "").upper(),
                "股價": round(float(t.price), 2),
                "股數": int(t.quantity),
                "手續費": round(float(t.fee or 0), 2) if t.fee is not None else None,
                "證交稅": round(float(t.tax or 0), 2) if t.tax is not None else None,
                "備註": (t.note or "")[:30],
            })
        raw_df = pd.DataFrame(raw_rows)
        _raw_fmt = {"股價": "{:,.2f}", "股數": "{:,.0f}"}
        if "手續費" in raw_df.columns:
            _raw_fmt["手續費"] = "{:,.2f}"
        if "證交稅" in raw_df.columns:
            _raw_fmt["證交稅"] = "{:,.2f}"
        st.dataframe(raw_df.style.format(_raw_fmt, na_rep="—"), use_container_width=True, hide_index=True)
        buy_total_qty = sum(r["股數"] for r in raw_rows if r["買/賣"] == "BUY")
        sell_total_qty = sum(r["股數"] for r in raw_rows if r["買/賣"] == "SELL")
        buy_total_amt = sum(r["股價"] * r["股數"] for r in raw_rows if r["買/賣"] == "BUY")
        st.caption(f"買進筆數：{sum(1 for r in raw_rows if r['買/賣']=='BUY')} 筆，合計股數 {buy_total_qty:,}，合計價金 {buy_total_amt:,.0f}。賣出筆數：{sum(1 for r in raw_rows if r['買/賣']=='SELL')} 筆，合計股數 {sell_total_qty:,}。")
        max_buy_price = max((r["股價"] for r in raw_rows if r["買/賣"] == "BUY"), default=0)
        if max_buy_price > 500:
            st.warning(f"⚠️ 買進單筆最高股價為 **{max_buy_price:,.2f}**，若高於該股合理區間，請檢查該筆是否輸入錯誤或匯入時解析錯誤。")

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

# ---------- 依買賣人加總（本檔股票） ----------
if detail_users:
    with st.expander("📊 依買賣人加總（本檔股票）", expanded=False):
        user_summary_rows = []
        for u in detail_users:
            utrades = [t for t in trades if t.user == u]
            _sold, _rev, _inv, _summ = build_stock_detail(selected_id, utrades, masters, policy, custom_rules=custom_rules_list)
            user_summary_rows.append({
                "買賣人": u,
                "已出售總金額": round(_rev, 0),
                "庫存股數": _summ.get("庫存股數", 0),
                "庫存原始成本": _summ.get("原始成本", 0),
                "庫存均價": _summ.get("原始均價", 0),
            })
        df_user_detail = pd.DataFrame(user_summary_rows)
        st.dataframe(
            df_user_detail.style.format({"已出售總金額": "{:,.0f}", "庫存股數": "{:,.0f}", "庫存原始成本": "{:,.0f}", "庫存均價": "{:,.2f}"}),
            use_container_width=True,
            hide_index=True,
        )

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
