# -*- coding: utf-8 -*-
"""Portfolio 持倉與損益報表"""
from datetime import date
from collections import defaultdict
import pandas as pd
from db.models import Trade, StockMaster
from services.pnl_engine import Lot, compute_matches


def get_realized_pnl_by_stock(trades, start_date: date, end_date: date, policy: str):
    """依時間區間內的買賣計算已實現損益（依股票）。"""
    in_range = [t for t in trades if start_date <= t.trade_date <= end_date]
    buys_by_stock = defaultdict(list)
    sells_by_stock = defaultdict(list)
    for t in in_range:
        lot = Lot(t.id, t.quantity, t.price, str(t.trade_date))
        if t.side == "BUY":
            buys_by_stock[t.stock_id].append(lot)
        else:
            sells_by_stock[t.stock_id].append(lot)
    realized = defaultdict(float)
    for sid, sells in sells_by_stock.items():
        buys = buys_by_stock.get(sid, [])
        matches = compute_matches(buys, sells, policy)
        for m in matches:
            realized[sid] += m[5]
    return realized


def build_portfolio_df(trades, masters, start_date: date, end_date: date, policy: str, get_quote_fn):
    """
    建構持倉表：stock_id, name, industry, user, shares, avg_cost, last_price,
    market_value, unrealized_pnl, realized_pnl, total_pnl.
    持倉用「全部」交易計算；已實現損益用 start_date~end_date 內沖銷計算。
    """
    buys_by_stock_user = defaultdict(lambda: defaultdict(list))
    sells_by_stock_user = defaultdict(lambda: defaultdict(list))
    for t in trades:
        lot = Lot(t.id, t.quantity, t.price, str(t.trade_date))
        key = (t.stock_id, t.user)
        if t.side == "BUY":
            buys_by_stock_user[t.stock_id][t.user].append(lot)
        else:
            sells_by_stock_user[t.stock_id][t.user].append(lot)

    in_range = [t for t in trades if start_date <= t.trade_date <= end_date]
    buys_range = defaultdict(list)
    sells_range = defaultdict(list)
    for t in in_range:
        lot = Lot(t.id, t.quantity, t.price, str(t.trade_date))
        if t.side == "BUY":
            buys_range[t.stock_id].append(lot)
        else:
            sells_range[t.stock_id].append(lot)

    realized = defaultdict(float)
    for sid, sells in sells_range.items():
        matches = compute_matches(buys_range.get(sid, []), sells, policy)
        for m in matches:
            realized[sid] += m[5]

    # 持倉：全體買 - 全體賣（不分 user 先彙總股票）
    position_qty = defaultdict(int)
    total_buy_cost = defaultdict(float)
    matched_cost = defaultdict(float)
    for sid in set(list(buys_by_stock_user) + list(sells_by_stock_user)):
        all_buys = []
        all_sells = []
        for user in set(list(buys_by_stock_user.get(sid, {})) + list(sells_by_stock_user.get(sid, {}))):
            all_buys.extend(buys_by_stock_user.get(sid, {}).get(user, []))
            all_sells.extend(sells_by_stock_user.get(sid, {}).get(user, []))
        total_buy_qty = sum(b.qty for b in all_buys)
        total_sell_qty = sum(s.qty for s in all_sells)
        position_qty[sid] = total_buy_qty - total_sell_qty
        total_buy_cost[sid] = sum(b.qty * b.price for b in all_buys)
        matches = compute_matches(all_buys, all_sells, policy)
        matched_cost[sid] = sum(m[3] * m[2] for m in matches)

    rows = []
    for sid, qty in position_qty.items():
        if qty <= 0:
            continue
        remaining_cost = total_buy_cost[sid] - matched_cost[sid]
        avg_cost = remaining_cost / qty if qty else 0
        quote = get_quote_fn(sid)
        last_price = quote["price"] if quote else avg_cost
        market_value = qty * last_price
        unrealized = (last_price - avg_cost) * qty
        real = realized.get(sid, 0)
        m = masters.get(sid)
        rows.append({
            "stock_id": sid,
            "name": (m.name if m else "-"),
            "industry": (m.industry_name if m else "-"),
            "shares": qty,
            "avg_cost": round(avg_cost, 2),
            "last_price": last_price,
            "market_value": round(market_value, 2),
            "unrealized_pnl": round(unrealized, 2),
            "realized_pnl": round(real, 2),
            "total_pnl": round(unrealized + real, 2),
        })
    df = pd.DataFrame(rows)
    df_industry = df.groupby("industry", as_index=False).agg({"market_value": "sum", "total_pnl": "sum"}) if not df.empty else pd.DataFrame()
    # 按買賣人小計：per (stock_id, user) 持倉後加總
    user_rows = []
    all_keys = set()
    for sid in buys_by_stock_user:
        for u in buys_by_stock_user[sid]:
            all_keys.add((sid, u))
    for sid in sells_by_stock_user:
        for u in sells_by_stock_user[sid]:
            all_keys.add((sid, u))
    for (sid, user) in all_keys:
        all_buys = list(buys_by_stock_user.get(sid, {}).get(user, []))
        all_sells = list(sells_by_stock_user.get(sid, {}).get(user, []))
        buy_lots = [Lot(b.trade_id, b.qty, b.price, b.date) for b in all_buys]
        sell_lots = [Lot(s.trade_id, s.qty, s.price, s.date) for s in all_sells]
        total_buy_qty = sum(b.qty for b in buy_lots)
        total_sell_qty = sum(s.qty for s in sell_lots)
        qty = total_buy_qty - total_sell_qty
        if qty <= 0:
            continue
        total_buy_cost = sum(b.qty * b.price for b in buy_lots)
        matches = compute_matches(buy_lots, sell_lots, policy)
        matched_cost = sum(m[3] * m[2] for m in matches)
        avg_cost = (total_buy_cost - matched_cost) / qty
        quote = get_quote_fn(sid)
        last_price = quote["price"] if quote else avg_cost
        mv = qty * last_price
        unrealized = (last_price - avg_cost) * qty
        in_range_buys = [Lot(t.id, t.quantity, t.price, str(t.trade_date)) for t in in_range if t.stock_id == sid and t.user == user and t.side == "BUY"]
        in_range_sells = [Lot(t.id, t.quantity, t.price, str(t.trade_date)) for t in in_range if t.stock_id == sid and t.user == user and t.side == "SELL"]
        real = sum(m[5] for m in compute_matches(in_range_buys, in_range_sells, policy))
        user_rows.append({"user": user, "stock_id": sid, "market_value": round(mv, 2), "total_pnl": round(unrealized + real, 2)})
    df_user = pd.DataFrame(user_rows).groupby("user", as_index=False).agg({"market_value": "sum", "total_pnl": "sum"}) if user_rows else pd.DataFrame()
    return df, df_industry, df_user
