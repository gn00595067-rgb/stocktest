# -*- coding: utf-8 -*-
"""
損益沖銷：僅支援自定沖銷（CUSTOM）。
依 custom_match_rules 指定賣出與買進的沖銷股數，計算已實現損益與持倉。
"""
from dataclasses import dataclass
from typing import List, Tuple, Optional


@dataclass
class Lot:
    trade_id: int
    qty: int
    price: float
    date: str


def _custom_match(
    sells: List[Lot],
    buys: List[Lot],
    custom_rules: List[Tuple[int, int, int]],
) -> List[Tuple[Lot, Lot, int, float]]:
    """
    依自定規則沖銷。custom_rules = [(sell_trade_id, buy_trade_id, matched_qty), ...]
    回傳 (buy_lot, sell_lot, matched_qty, pnl)。
    """
    result = []
    buy_by_id = {b.trade_id: b for b in buys}
    sell_by_id = {s.trade_id: s for s in sells}
    for sell_id, buy_id, rule_qty in custom_rules:
        if rule_qty <= 0:
            continue
        sl = sell_by_id.get(sell_id)
        bl = buy_by_id.get(buy_id)
        if sl is None or bl is None:
            continue
        match_qty = min(rule_qty, sl.qty, bl.qty)
        if match_qty <= 0:
            continue
        pnl = (sl.price - bl.price) * match_qty
        result.append((bl, sl, match_qty, pnl))
        bl.qty -= match_qty
        sl.qty -= match_qty
    return result


def net_pnl_for_match(
    match: Tuple[int, int, int, float, float, float],
    trade_by_id: dict,
) -> float:
    """
    將單筆沖銷的毛損益改為淨損益（扣除買進手續費、賣出手續費、賣出證交稅）。
    match = (buy_id, sell_id, qty, buy_price, sell_price, pnl_gross)
    """
    buy_id, sell_id, qty, _bp, _sp, pnl_gross = match
    buy_t = trade_by_id.get(buy_id)
    sell_t = trade_by_id.get(sell_id)
    buy_fee = 0.0
    if buy_t and getattr(buy_t, "quantity", 0):
        fee = float(getattr(buy_t, "fee", None) or 0)
        buy_fee = fee * (qty / buy_t.quantity)
    sell_fee = 0.0
    sell_tax = 0.0
    if sell_t and getattr(sell_t, "quantity", 0):
        fee = float(getattr(sell_t, "fee", None) or 0)
        tax = float(getattr(sell_t, "tax", None) or 0)
        sell_fee = fee * (qty / sell_t.quantity)
        sell_tax = tax * (qty / sell_t.quantity)
    return pnl_gross - buy_fee - sell_fee - sell_tax


def compute_matches(
    buys: List[Lot],
    sells: List[Lot],
    policy: str,
    custom_rules: Optional[List[Tuple[int, int, int]]] = None,
) -> List[Tuple[int, int, int, float, float, float]]:
    """
    僅依自定沖銷規則配對（policy 參數保留相容用，未使用）。
    custom_rules = [(sell_trade_id, buy_trade_id, matched_qty), ...]。
    回傳 [(buy_trade_id, sell_trade_id, matched_qty, buy_price, sell_price, pnl), ...]
    """
    buys = [Lot(trade_id=b.trade_id, qty=b.qty, price=b.price, date=b.date) for b in buys]
    sells = [Lot(trade_id=s.trade_id, qty=s.qty, price=s.price, date=s.date) for s in sells]
    if not custom_rules:
        return []
    raw = _custom_match(sells, buys, custom_rules)
    return [(bl.trade_id, sl.trade_id, qty, bl.price, sl.price, pnl) for bl, sl, qty, pnl in raw]
