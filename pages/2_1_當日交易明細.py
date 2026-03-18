# -*- coding: utf-8 -*-
"""當日交易明細：以「日期」為主體顯示全部原始買賣與交割加總。"""
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
from db.models import Trade, StockMaster

st.set_page_config(page_title="當日交易明細", layout="wide")
st.title("當日交易明細")
st.caption("以「每日」為主體列出當日全部原始買賣，並加總交割應收/應付，協助核對交割金額。")

try:
    sess = get_session()
    trades = sess.query(Trade).all()
    masters = {m.stock_id: m for m in sess.query(StockMaster).all()}
    sess.close()
except OperationalError:
    st.warning("資料庫無法使用（雲端部署請在 Secrets 設定 USE_GOOGLE_SHEET、GOOGLE_SHEET_ID、GOOGLE_SHEET_CREDENTIALS_B64）。")
    st.stop()
except Exception:
    st.warning("無法載入交易資料。")
    st.stop()

if not trades:
    st.info("尚無交易資料。")
    st.stop()

stock_ids = sorted(set(str(t.stock_id).strip() for t in trades if getattr(t, "stock_id", None)))
users = sorted(set((getattr(t, "user", None) or "").strip() for t in trades if getattr(t, "user", None)))
dates = sorted({t.trade_date for t in trades if getattr(t, "trade_date", None)})

def _stock_label(sid: str) -> str:
    sid = str(sid).strip()
    m = masters.get(sid)
    name = (getattr(m, "name", None) or "").strip() if m else ""
    return f"{sid} {name}".strip() if name else sid

stock_opts = ["全部"] + [_stock_label(sid) for sid in stock_ids]
user_opts = ["全部"] + users

f1, f2, f3 = st.columns([1.2, 1.2, 1.2])
with f1:
    day = st.date_input("日期", value=dates[-1], key="daily_page_date")
with f2:
    stock_label = st.selectbox("股票", options=stock_opts, key="daily_page_stock")
with f3:
    user = st.selectbox("買賣人", options=user_opts, key="daily_page_user")

def _label_to_sid(lbl: str):
    if lbl == "全部":
        return None
    return str(lbl).split(" ")[0].strip()

sid_filter = _label_to_sid(stock_label)

daily = [t for t in trades if getattr(t, "trade_date", None) == day]
if user != "全部":
    daily = [t for t in daily if (getattr(t, "user", None) or "").strip() == user]
if sid_filter:
    daily = [t for t in daily if str(getattr(t, "stock_id", "")).strip() == sid_filter]

st.caption(f"當日筆數：**{len(daily)}**")

if not daily:
    st.info("此日期下沒有符合篩選條件的交易。")
    st.stop()

rows = []
for t in sorted(daily, key=lambda x: (x.trade_date, str(x.stock_id), (x.user or ""), x.id)):
    side = (getattr(t, "side", "") or "").upper()
    qty = int(getattr(t, "quantity", 0) or 0)
    price = float(getattr(t, "price", 0) or 0)
    fee = float(getattr(t, "fee", 0) or 0)
    tax = float(getattr(t, "tax", 0) or 0)
    gross = qty * price
    if side == "BUY":
        settle_in = 0.0
        settle_out = gross + fee
    else:
        settle_in = gross - fee - tax
        settle_out = 0.0
    rows.append({
        "買賣人": getattr(t, "user", "") or "",
        "股票": _stock_label(getattr(t, "stock_id", "")),
        "買/賣": side,
        "股數": qty,
        "價格": price,
        "成交金額": gross,
        "手續費": fee,
        "證交稅": tax,
        "交割應收": settle_in,
        "交割應付": settle_out,
        "當沖": bool(getattr(t, "is_daytrade", False)),
        "備註": (getattr(t, "note", "") or "")[:40],
    })

df = pd.DataFrame(rows)
df["_rank"] = (df["交割應收"] + df["交割應付"]).apply(lambda v: 0 if v else 1)
df = df.sort_values(by=["_rank", "股票", "買賣人", "買/賣"]).drop(columns=["_rank"])

fmt = {
    "股數": "{:,.0f}",
    "價格": "{:,.2f}",
    "成交金額": "{:,.0f}",
    "手續費": "{:,.0f}",
    "證交稅": "{:,.0f}",
    "交割應收": "{:,.0f}",
    "交割應付": "{:,.0f}",
}
st.dataframe(df.style.format(fmt, na_rep="—"), use_container_width=True, hide_index=True)

total_in = float(df["交割應收"].sum())
total_out = float(df["交割應付"].sum())
net = total_in - total_out

st.markdown("---")
st.subheader("交割加總")
c1, c2, c3 = st.columns(3)
c1.metric("交割應收（賣出淨入帳）", f"{total_in:,.0f}")
c2.metric("交割應付（買進含手續費）", f"{total_out:,.0f}")
c3.metric("淨交割（應收－應付）", f"{net:,.0f}")

st.caption("提醒：此處以交易金額 ± 手續費/證交稅估算交割收付；若券商另有其他費用/利息，請以帳單為準。")

csv_buf = io.BytesIO()
df.to_csv(csv_buf, index=False, encoding="utf-8-sig")
st.download_button(
    "下載當日交易明細 CSV",
    data=csv_buf.getvalue(),
    file_name=f"daily_trades_{day}.csv",
    mime="text/csv",
    key="dl_daily_trades_page",
)

