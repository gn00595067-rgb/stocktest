# -*- coding: utf-8 -*-
"""
持倉股數與剩餘成本計算（僅自定沖銷）。
與 庫存損益 同一套邏輯，供投資績效頁共用，避免均價不一致。
"""
from collections import defaultdict
from typing import Optional, List, Tuple

from services.pnl_engine import Lot, compute_matches


def _is_buy(t) -> bool:
    """買/賣不區分大小寫，與 portfolio_report 一致。"""
    return (getattr(t, "side", None) or "").strip().upper() == "BUY"


def compute_position_and_cost_by_stock(
    trades,
    custom_rules: Optional[List[Tuple[int, int, int]]] = None,
    policy: str = "CUSTOM",
):
    """
    依自定沖銷計算每檔持倉股數與剩餘成本（均價 = cost / qty）。
    與 build_portfolio_df 持倉邏輯完全一致。
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
            float(getattr(trade_by_id.get(m[0]), "fee", None) or 0) * (m[2] / (getattr(trade_by_id.get(m[0]), "quantity", None) or 1))
            for m in matches if trade_by_id.get(m[0])
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
