# -*- coding: utf-8 -*-
"""戰略儀表板"""
import streamlit as st
import pandas as pd
from collections import defaultdict
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
from services.pnl_engine import Lot, compute_matches
from services.price_service import get_quote_cached

st.set_page_config(page_title="戰略儀表板", layout="wide")
st.title("戰略儀表板")

sess = get_session()
trades = sess.query(Trade).all()
masters = {m.stock_id: m for m in sess.query(StockMaster).all()}
sess.close()

buys_by_stock = defaultdict(list)
sells_by_stock = defaultdict(list)
for t in trades:
    lot = Lot(t.id, t.quantity, t.price, str(t.trade_date))
    if t.side == "BUY":
        buys_by_stock[t.stock_id].append(lot)
    else:
        sells_by_stock[t.stock_id].append(lot)

realized_total = 0.0
match_pnls = []
for sid, sells in sells_by_stock.items():
    buys = list(buys_by_stock.get(sid, []))
    sell_lots = [Lot(s.trade_id, s.qty, s.price, s.date) for s in sells]
    matches = compute_matches(buys, sell_lots, "FIFO")
    for m in matches:
        realized_total += m[5]
        match_pnls.append(m[5])

position = defaultdict(lambda: {"qty": 0, "cost": 0.0})
for sid, lots in buys_by_stock.items():
    for l in lots:
        position[sid]["qty"] += l.qty
        position[sid]["cost"] += l.qty * l.price
for sid, lots in sells_by_stock.items():
    for l in lots:
        position[sid]["qty"] -= l.qty

unrealized_total = 0.0
industry_exposure = defaultdict(float)
industry_pnl = defaultdict(float)
for sid, p in position.items():
    if p["qty"] <= 0:
        continue
    avg = p["cost"] / p["qty"]
    q = get_quote_cached(sid)
    last = q["price"] if q else avg
    unrealized_total += (last - avg) * p["qty"]
    ind = masters.get(sid).industry_name if masters.get(sid) else "其他"
    industry_exposure[ind] += p["qty"] * last
    industry_pnl[ind] += (last - avg) * p["qty"]

total_pnl = realized_total + unrealized_total
win_count = sum(1 for p in match_pnls if p > 0)
total_trades = len(match_pnls)
win_rate = (win_count / total_trades * 100) if total_trades else 0
max_single = max(match_pnls) if match_pnls else 0
min_single = min(match_pnls) if match_pnls else 0

daytrade_pnl = 0.0
for t in trades:
    if t.is_daytrade and t.side == "SELL":
        daytrade_pnl += (t.price - 0) * t.quantity  # 簡化：實際應依沖銷計算
# 簡化：用已實現中當沖標記的估算
sess = get_session()
daytrade_sells = sess.query(Trade).filter(Trade.side == "SELL", Trade.is_daytrade == True).all()
sess.close()
daytrade_contrib = 0.0  # 可再細算當沖損益

st.subheader("KPI")
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("已實現損益", f"{realized_total:,.0f}", None)
c2.metric("未實現損益", f"{unrealized_total:,.0f}", None)
c3.metric("總損益", f"{total_pnl:,.0f}", None)
c4.metric("勝率", f"{win_rate:.1f}%", f"{win_count}/{total_trades}")
c5.metric("最大單筆盈/虧", f"{max_single:,.0f} / {min_single:,.0f}", None)

st.metric("產業曝險 Top3", ", ".join([f"{k}: {v:,.0f}" for k, v in sorted(industry_exposure.items(), key=lambda x: -x[1])[:3]]))

st.subheader("累積損益時間序列")
if trades:
    dates = sorted(set(str(t.trade_date) for t in trades))
    cum = []
    r = 0.0
    for d in dates:
        day_sells = [t for t in trades if str(t.trade_date) == d and t.side == "SELL"]
        day_buys = [t for t in trades if str(t.trade_date) == d and t.side == "BUY"]
        for t in day_sells:
            r += t.price * t.quantity
        for t in day_buys:
            r -= t.price * t.quantity
        cum.append({"date": d, "cumulative_pnl": r})
    df_ts = pd.DataFrame(cum)
    st.line_chart(df_ts.set_index("date"))

st.subheader("產業損益柱狀")
if industry_pnl:
    df_ind = pd.DataFrame([{"產業": k, "損益": v} for k, v in industry_pnl.items()])
    st.bar_chart(df_ind.set_index("產業"))

st.subheader("持倉市值分布")
if industry_exposure:
    df_mv = pd.DataFrame([{"產業": k, "市值": v} for k, v in industry_exposure.items()])
    st.bar_chart(df_mv.set_index("產業"))
