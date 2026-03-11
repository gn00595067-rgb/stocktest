# -*- coding: utf-8 -*-
"""Portfolio 持倉與損益報表"""
from datetime import date
from collections import defaultdict
from typing import Optional, List, Tuple

import pandas as pd
from db.models import Trade, StockMaster
from services.pnl_engine import Lot, compute_matches, net_pnl_for_match


def get_realized_pnl_by_stock(trades, start_date: date, end_date: date, policy: str, custom_rules: Optional[List[Tuple[int, int, int]]] = None):
    """依時間區間內的買賣計算已實現損益（依股票），淨損益（扣手續費與證交稅）。"""
    in_range = [t for t in trades if start_date <= t.trade_date <= end_date]
    buys_by_stock = defaultdict(list)
    sells_by_stock = defaultdict(list)
    for t in in_range:
        lot = Lot(t.id, t.quantity, t.price, str(t.trade_date))
        if t.side == "BUY":
            buys_by_stock[t.stock_id].append(lot)
        else:
            sells_by_stock[t.stock_id].append(lot)
    trade_by_id = {t.id: t for t in trades}
    realized = defaultdict(float)
    for sid, sells in sells_by_stock.items():
        buys = buys_by_stock.get(sid, [])
        matches = compute_matches(buys, sells, policy, custom_rules=custom_rules if policy == "CUSTOM" else None)
        for m in matches:
            realized[sid] += net_pnl_for_match(m, trade_by_id)
    return realized


def build_portfolio_df(trades, masters, start_date: date, end_date: date, policy: str, get_quote_fn, custom_rules: Optional[List[Tuple[int, int, int]]] = None):
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
    trade_by_id = {t.id: t for t in trades}
    for sid, sells in sells_range.items():
        matches = compute_matches(buys_range.get(sid, []), sells, policy, custom_rules=custom_rules if policy == "CUSTOM" else None)
        for m in matches:
            realized[sid] += net_pnl_for_match(m, trade_by_id)

    # 持倉：全體買 - 全體賣；剩餘成本含買進手續費
    position_qty = defaultdict(int)
    total_buy_cost = defaultdict(float)
    matched_cost = defaultdict(float)
    debug_cost = {}  # sid -> {total_buy_cost, matched_cost, matched_buy_fee, remaining_cost, avg_cost} 供除錯
    for sid in set(list(buys_by_stock_user) + list(sells_by_stock_user)):
        all_buys = []
        all_sells = []
        seen_buy_ids = set()
        seen_sell_ids = set()
        for user in set(list(buys_by_stock_user.get(sid, {})) + list(sells_by_stock_user.get(sid, {}))):
            for b in buys_by_stock_user.get(sid, {}).get(user, []):
                if b.trade_id not in seen_buy_ids:
                    seen_buy_ids.add(b.trade_id)
                    all_buys.append(b)
            for s in sells_by_stock_user.get(sid, {}).get(user, []):
                if s.trade_id not in seen_sell_ids:
                    seen_sell_ids.add(s.trade_id)
                    all_sells.append(s)
        total_buy_qty = sum(b.qty for b in all_buys)
        total_sell_qty = sum(s.qty for s in all_sells)
        position_qty[sid] = total_buy_qty - total_sell_qty
        total_buy_cost[sid] = sum(b.qty * b.price for b in all_buys) + sum(
            float(getattr(trade_by_id.get(b.trade_id), "fee", None) or 0) for b in all_buys
        )
        matches = compute_matches(all_buys, all_sells, policy, custom_rules=custom_rules if policy == "CUSTOM" else None)
        matched_cost[sid] = sum(m[3] * m[2] for m in matches)
        matched_buy_fee = sum(
            float(getattr(trade_by_id.get(m[0]), "fee", None) or 0) * (m[2] / (trade_by_id.get(m[0]).quantity or 1))
            for m in matches if trade_by_id.get(m[0]) and getattr(trade_by_id.get(m[0]), "quantity", 0)
        )
        remaining_cost = total_buy_cost[sid] - matched_cost[sid] - matched_buy_fee
        # 剩餘股數 per 買進單：用於顯示「哪幾筆買進」構成剩餘持倉
        remaining_qty_by_buy = {b.trade_id: b.qty for b in all_buys}
        for m in matches:
            remaining_qty_by_buy[m[0]] = remaining_qty_by_buy.get(m[0], 0) - m[2]
        buys_detail = []
        for b in all_buys:
            fee_val = float(getattr(trade_by_id.get(b.trade_id), "fee", None) or 0)
            cost = b.qty * b.price + fee_val
            buys_detail.append({"trade_id": b.trade_id, "date": b.date, "qty": b.qty, "price": b.price, "fee": fee_val, "cost": cost})
        matches_detail = [{"buy_id": m[0], "sell_id": m[1], "matched_qty": m[2], "buy_price": m[3], "matched_cost": m[3] * m[2]} for m in matches]
        buy_info_by_id = {b.trade_id: (b.date, b.price) for b in all_buys}
        remaining_lots_detail = []
        for tid, rem in remaining_qty_by_buy.items():
            if rem <= 0:
                continue
            info = buy_info_by_id.get(tid)
            if info:
                dte, pr = info
                remaining_lots_detail.append({"buy_id": tid, "date": dte, "remaining_qty": rem, "price": pr, "remaining_cost": rem * pr})
        total_buy_fee_raw = sum(float(getattr(trade_by_id.get(b.trade_id), "fee", None) or 0) for b in all_buys)
        remaining_buy_fee = total_buy_fee_raw - matched_buy_fee
        sum_remaining_from_lots = sum(r["remaining_cost"] for r in remaining_lots_detail)
        sum_remaining_qty_from_lots = sum(r["remaining_qty"] for r in remaining_lots_detail)
        total_buy_cost[sid] = remaining_cost
        q = position_qty[sid]
        max_buy_price = max((b["price"] for b in buys_detail), default=0)
        debug_cost[sid] = {
            "total_buy_cost_raw": total_buy_cost[sid] + matched_cost[sid] + matched_buy_fee,
            "matched_cost": matched_cost[sid],
            "matched_buy_fee": matched_buy_fee,
            "remaining_cost": remaining_cost,
            "remaining_buy_fee": remaining_buy_fee,
            "position_qty": q,
            "avg_cost": (remaining_cost / q if q else 0),
            "max_buy_price": max_buy_price,
            "sum_remaining_from_lots": sum_remaining_from_lots,
            "sum_remaining_qty_from_lots": sum_remaining_qty_from_lots,
            "avg_cost_from_lots": (sum_remaining_from_lots / sum_remaining_qty_from_lots if sum_remaining_qty_from_lots else 0),
            "buys_detail": buys_detail,
            "matches_detail": matches_detail,
            "remaining_lots_detail": remaining_lots_detail,
        }

    # 每檔股票只向 API 取價一次，避免持倉多檔時爆量（FinMind 每小時約 600 次上限）
    unique_sids = [sid for sid, qty in position_qty.items() if qty > 0]
    quote_by_sid = {}
    for sid in unique_sids:
        quote_by_sid[sid] = get_quote_fn(sid)

    rows = []
    for sid, qty in position_qty.items():
        if qty <= 0:
            continue
        remaining_cost = total_buy_cost[sid]
        avg_cost = remaining_cost / qty if qty else 0
        quote = quote_by_sid.get(sid)
        last_price = quote["price"] if quote else avg_cost
        market_value = qty * last_price
        unrealized = (last_price - avg_cost) * qty
        real = realized.get(sid, 0)
        m = masters.get(sid)
        rows.append({
            "股票代號": sid,
            "名稱": (m.name if m else "-"),
            "產業": (m.industry_name if m else "-"),
            "股數": qty,
            "均價": round(avg_cost, 2),
            "現價": last_price,
            "市值": round(market_value, 2),
            "未實現損益": round(unrealized, 2),
            "已實現損益": round(real, 2),
            "總損益": round(unrealized + real, 2),
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df_industry = df.groupby("產業", as_index=False).agg({"市值": "sum", "總損益": "sum"})
    else:
        df_industry = pd.DataFrame()
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
        total_buy_cost_local = sum(b.qty * b.price for b in buy_lots) + sum(
            float(getattr(trade_by_id.get(b.trade_id), "fee", None) or 0) for b in buy_lots
        )
        matches = compute_matches(buy_lots, sell_lots, policy, custom_rules=custom_rules if policy == "CUSTOM" else None)
        matched_cost_local = sum(m[3] * m[2] for m in matches)
        matched_buy_fee_local = sum(
            float(getattr(trade_by_id.get(m[0]), "fee", None) or 0) * (m[2] / (getattr(trade_by_id.get(m[0]), "quantity", None) or 1))
            for m in matches if trade_by_id.get(m[0])
        )
        avg_cost = (total_buy_cost_local - matched_cost_local - matched_buy_fee_local) / qty
        quote = quote_by_sid.get(sid)
        last_price = quote["price"] if quote else avg_cost
        mv = qty * last_price
        unrealized = (last_price - avg_cost) * qty
        in_range_buys = [Lot(t.id, t.quantity, t.price, str(t.trade_date)) for t in in_range if t.stock_id == sid and t.user == user and t.side == "BUY"]
        in_range_sells = [Lot(t.id, t.quantity, t.price, str(t.trade_date)) for t in in_range if t.stock_id == sid and t.user == user and t.side == "SELL"]
        real = sum(net_pnl_for_match(m, trade_by_id) for m in compute_matches(in_range_buys, in_range_sells, policy, custom_rules=custom_rules if policy == "CUSTOM" else None))
        user_rows.append({"買賣人": user, "股票代號": sid, "市值": round(mv, 2), "總損益": round(unrealized + real, 2)})
    df_user = pd.DataFrame(user_rows).groupby("買賣人", as_index=False).agg({"市值": "sum", "總損益": "sum"}) if user_rows else pd.DataFrame()
    return df, df_industry, df_user, debug_cost
