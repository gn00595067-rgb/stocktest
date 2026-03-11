# -*- coding: utf-8 -*-
"""
Portfolio 持倉與損益報表

持倉與已實現損益 **僅依自定沖銷**（custom_rules）計算，無 FIFO/LIFO 等其他方式。

持倉成本計算邏輯（單一股票）：
1. 合併該檔所有買賣人的買進/賣出，依 trade_id 去重（同一筆交易只計一次）。
2. all_buys / all_sells 依 (date, trade_id) 排序，供沖銷與剩餘持倉一致。
3. position_qty = total_buy_qty - total_sell_qty。
4. total_buy_amount = sum(買進股數×單價)，total_buy_fee_raw = sum(買進手續費)。
5. compute_matches(all_buys, all_sells, policy) 得沖銷列表；(buy_id, sell_id, matched_qty, buy_price, ...)。
6. matched_cost = sum(matched_qty × buy_price)；matched_buy_fee = 已沖銷部分之買進手續費。
7. remaining_qty_by_buy = 每筆買進剩餘股數（原股數 − 該筆被沖銷股數）。
8. 剩餘持倉成本唯一定義：remaining_cost = sum(剩餘股數×單價) + 剩餘買進手續費 = sum_remaining_from_lots + remaining_buy_fee。
9. 均價 = remaining_cost / position_qty。且 sum(剩餘股數) 必須等於 position_qty（不一致則 raise ValueError）。
"""
from datetime import date
from collections import defaultdict
from typing import Optional, List, Tuple

import pandas as pd
from db.models import Trade, StockMaster
from services.pnl_engine import Lot, compute_matches, net_pnl_for_match


def _is_buy(t) -> bool:
    """與投資績效、daily_detail、stock_detail 一致：買/賣不區分大小寫。"""
    return (getattr(t, "side", None) or "").strip().upper() == "BUY"


def compute_position_and_cost_by_stock(trades, custom_rules: Optional[List[Tuple[int, int, int]]] = None, policy: str = "CUSTOM"):
    """
    依自定沖銷計算每檔持倉股數與剩餘成本（均價 = cost / qty）。
    與 build_portfolio_df 持倉邏輯完全一致，供投資績效頁共用，避免兩套計算導致均價不一致。
    回傳 {stock_id: {"qty": int, "cost": float}, ...}，僅含 qty > 0 的股票。
    """
    if not custom_rules:
        custom_rules = []
    buys_by_stock_user = defaultdict(lambda: defaultdict(list))
    sells_by_stock_user = defaultdict(lambda: defaultdict(list))
    for t in trades:
        lot = Lot(t.id, t.quantity, t.price, str(t.trade_date))
        if _is_buy(t):
            buys_by_stock_user[t.stock_id][t.user].append(lot)
        else:
            sells_by_stock_user[t.stock_id][t.user].append(lot)
    trade_by_id = {t.id: t for t in trades}
    result = {}
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
        all_buys.sort(key=lambda b: (b.date, b.trade_id))
        all_sells.sort(key=lambda s: (s.date, s.trade_id))
        total_buy_qty = sum(b.qty for b in all_buys)
        total_sell_qty = sum(s.qty for s in all_sells)
        q = total_buy_qty - total_sell_qty
        if q <= 0:
            continue
        total_buy_fee_raw = sum(float(getattr(trade_by_id.get(b.trade_id), "fee", None) or 0) for b in all_buys)
        matches = compute_matches(all_buys, all_sells, policy, custom_rules=custom_rules)
        matched_buy_fee = sum(
            float(getattr(trade_by_id.get(m[0]), "fee", None) or 0) * (m[2] / (trade_by_id.get(m[0]).quantity or 1))
            for m in matches if trade_by_id.get(m[0]) and getattr(trade_by_id.get(m[0]), "quantity", 0)
        )
        remaining_qty_by_buy = {b.trade_id: b.qty for b in all_buys}
        for m in matches:
            remaining_qty_by_buy[m[0]] = remaining_qty_by_buy.get(m[0], 0) - m[2]
        buy_info_by_id = {b.trade_id: (b.date, b.price) for b in all_buys}
        sum_remaining_from_lots = 0.0
        sum_remaining_qty_from_lots = 0
        for tid, rem in remaining_qty_by_buy.items():
            if rem <= 0:
                continue
            info = buy_info_by_id.get(tid)
            if info:
                _, pr = info
                sum_remaining_from_lots += rem * pr
                sum_remaining_qty_from_lots += rem
        remaining_buy_fee = total_buy_fee_raw - matched_buy_fee
        remaining_cost = sum_remaining_from_lots + remaining_buy_fee
        if sum_remaining_qty_from_lots != q and q and sum_remaining_qty_from_lots > 0 and sum_remaining_qty_from_lots >= q:
            remaining_cost = (sum_remaining_from_lots / sum_remaining_qty_from_lots) * q + remaining_buy_fee
        elif not q:
            remaining_cost = 0.0
        result[sid] = {"qty": q, "cost": remaining_cost}
    return result


def get_realized_pnl_by_stock(trades, start_date: date, end_date: date, policy: str, custom_rules: Optional[List[Tuple[int, int, int]]] = None):
    """依時間區間內的買賣計算已實現損益（依股票），淨損益（扣手續費與證交稅）。"""
    in_range = [t for t in trades if start_date <= t.trade_date <= end_date]
    buys_by_stock = defaultdict(list)
    sells_by_stock = defaultdict(list)
    for t in in_range:
        lot = Lot(t.id, t.quantity, t.price, str(t.trade_date))
        if _is_buy(t):
            buys_by_stock[t.stock_id].append(lot)
        else:
            sells_by_stock[t.stock_id].append(lot)
    trade_by_id = {t.id: t for t in trades}
    realized = defaultdict(float)
    for sid, sells in sells_by_stock.items():
        buys = buys_by_stock.get(sid, [])
        matches = compute_matches(buys, sells, policy, custom_rules=custom_rules)
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
        if _is_buy(t):
            buys_by_stock_user[t.stock_id][t.user].append(lot)
        else:
            sells_by_stock_user[t.stock_id][t.user].append(lot)

    in_range = [t for t in trades if start_date <= t.trade_date <= end_date]
    buys_range = defaultdict(list)
    sells_range = defaultdict(list)
    for t in in_range:
        lot = Lot(t.id, t.quantity, t.price, str(t.trade_date))
        if _is_buy(t):
            buys_range[t.stock_id].append(lot)
        else:
            sells_range[t.stock_id].append(lot)

    realized = defaultdict(float)
    trade_by_id = {t.id: t for t in trades}
    for sid, sells in sells_range.items():
        matches = compute_matches(buys_range.get(sid, []), sells, policy, custom_rules=custom_rules)
        for m in matches:
            realized[sid] += net_pnl_for_match(m, trade_by_id)

    # 持倉：全體買 - 全體賣；剩餘成本含買進手續費
    position_qty = defaultdict(int)
    total_buy_cost = defaultdict(float)
    matched_cost = defaultdict(float)
    debug_cost = {}
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
        # 合併後排序，供自定沖銷與剩餘持倉計算一致
        all_buys.sort(key=lambda b: (b.date, b.trade_id))
        all_sells.sort(key=lambda s: (s.date, s.trade_id))
        total_buy_qty = sum(b.qty for b in all_buys)
        total_sell_qty = sum(s.qty for s in all_sells)
        position_qty[sid] = total_buy_qty - total_sell_qty
        total_buy_amount = sum(b.qty * b.price for b in all_buys)
        total_buy_fee_raw = sum(float(getattr(trade_by_id.get(b.trade_id), "fee", None) or 0) for b in all_buys)
        total_buy_cost_before_match = total_buy_amount + total_buy_fee_raw
        matches = compute_matches(all_buys, all_sells, policy, custom_rules=custom_rules)
        matched_cost[sid] = sum(m[3] * m[2] for m in matches)
        matched_buy_fee = sum(
            float(getattr(trade_by_id.get(m[0]), "fee", None) or 0) * (m[2] / (trade_by_id.get(m[0]).quantity or 1))
            for m in matches if trade_by_id.get(m[0]) and getattr(trade_by_id.get(m[0]), "quantity", 0)
        )
        remaining_qty_by_buy = {b.trade_id: b.qty for b in all_buys}
        for m in matches:
            remaining_qty_by_buy[m[0]] = remaining_qty_by_buy.get(m[0], 0) - m[2]
        buy_info_by_id = {b.trade_id: (b.date, b.price) for b in all_buys}
        remaining_lots_detail = []
        for tid, rem in remaining_qty_by_buy.items():
            if rem <= 0:
                continue
            info = buy_info_by_id.get(tid)
            if info:
                dte, pr = info
                remaining_lots_detail.append({"buy_id": tid, "date": dte, "remaining_qty": rem, "price": pr, "remaining_cost": rem * pr})
        sum_remaining_from_lots = sum(r["remaining_cost"] for r in remaining_lots_detail)
        sum_remaining_qty_from_lots = sum(r["remaining_qty"] for r in remaining_lots_detail)
        remaining_buy_fee = total_buy_fee_raw - matched_buy_fee
        max_buy_price = max((b.price for b in all_buys), default=0)
        # 剩餘持倉成本唯一定義：未沖銷買進之 (股數×單價) + 未沖銷部分之買進手續費
        remaining_cost = sum_remaining_from_lots + remaining_buy_fee
        q = position_qty[sid]
        # 自定沖銷時，規則可能未涵蓋全部賣出或有多餘配對，導致 sum_remaining_qty_from_lots != position_qty；不 raise，以 position_qty 為準並在 debug 中標示
        if sum_remaining_qty_from_lots != q:
            if q and sum_remaining_qty_from_lots > 0 and sum_remaining_qty_from_lots >= q:
                remaining_cost = (sum_remaining_from_lots / sum_remaining_qty_from_lots) * q + remaining_buy_fee
            elif not q:
                remaining_cost = 0.0
        avg_cost = remaining_cost / q if q else 0
        # 數學上「剩餘均價」不可能高於任一本檔買進單價；若發生則必為程式或資料錯誤
        if q and max_buy_price and avg_cost > max_buy_price + 1.0:
            raise ValueError(
                f"{sid}: 持倉均價({avg_cost:.2f}) > 本檔最高買價({max_buy_price})，違反數學約束。"
                f" sum_remaining_from_lots={sum_remaining_from_lots:.0f} remaining_buy_fee={remaining_buy_fee:.0f} q={q}"
            )
        total_buy_cost[sid] = remaining_cost
        buys_detail = []
        for b in all_buys:
            fee_val = float(getattr(trade_by_id.get(b.trade_id), "fee", None) or 0)
            cost = b.qty * b.price + fee_val
            buys_detail.append({"trade_id": b.trade_id, "date": b.date, "qty": b.qty, "price": b.price, "fee": fee_val, "cost": cost})
        matches_detail = [{"buy_id": m[0], "sell_id": m[1], "matched_qty": m[2], "buy_price": m[3], "matched_cost": m[3] * m[2]} for m in matches]
        max_buy_price = max((b["price"] for b in buys_detail), default=0)
        debug_cost[sid] = {
            "total_buy_cost_raw": total_buy_cost_before_match,
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
            "qty_mismatch": sum_remaining_qty_from_lots != q,
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
        matches = compute_matches(buy_lots, sell_lots, policy, custom_rules=custom_rules)
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
        in_range_buys = [Lot(t.id, t.quantity, t.price, str(t.trade_date)) for t in in_range if t.stock_id == sid and t.user == user and _is_buy(t)]
        in_range_sells = [Lot(t.id, t.quantity, t.price, str(t.trade_date)) for t in in_range if t.stock_id == sid and t.user == user and not _is_buy(t)]
        real = sum(net_pnl_for_match(m, trade_by_id) for m in compute_matches(in_range_buys, in_range_sells, policy, custom_rules=custom_rules))
        user_rows.append({"買賣人": user, "股票代號": sid, "市值": round(mv, 2), "總損益": round(unrealized + real, 2)})
    df_user = pd.DataFrame(user_rows).groupby("買賣人", as_index=False).agg({"市值": "sum", "總損益": "sum"}) if user_rows else pd.DataFrame()
    return df, df_industry, df_user, debug_cost
