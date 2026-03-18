# -*- coding: utf-8 -*-
"""
損益沖銷（配對買進↔賣出）：

- CUSTOM / CUSTOM_ONLY：僅使用自定沖銷規則（custom_match_rules）
- CUSTOM_PLUS_*：先套用自定沖銷規則，剩餘未覆蓋部分再用策略補配

策略用於「未定沖銷部分」：
- FIFO：先進先出（買進日期舊→新）
- CONSERVATIVE：保守（賺最少；先配成本高的買進）
- OPTIMISTIC：樂觀（賺最多；先配成本低的買進）
- MEAN：均值（買價最接近可用買進的加權平均成本）
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
    回傳 [(buy_trade_id, sell_trade_id, matched_qty, buy_price, sell_price, pnl_gross), ...]

    注意：
    - 本函式回傳「毛損益」，淨損益請用 net_pnl_for_match 依手續費/稅換算。
    - buys/sells 的 Lot.date 通常為 YYYY-MM-DD 字串；策略配對會約束 buy.date <= sell.date。
    """
    policy = (policy or "").strip().upper()
    # 相容舊值
    if policy in ("CUSTOM", ""):
        policy = "CUSTOM_ONLY"

    buys0 = [Lot(trade_id=b.trade_id, qty=int(b.qty), price=float(b.price), date=str(b.date)) for b in buys]
    sells0 = [Lot(trade_id=s.trade_id, qty=int(s.qty), price=float(s.price), date=str(s.date)) for s in sells]

    # 先套用自定規則
    raw_custom: List[Tuple[Lot, Lot, int, float]] = []
    if custom_rules:
        raw_custom = _custom_match(sells0, buys0, list(custom_rules))

    def _emit(raw: List[Tuple[Lot, Lot, int, float]]):
        return [(bl.trade_id, sl.trade_id, qty, bl.price, sl.price, pnl) for bl, sl, qty, pnl in raw]

    if policy == "CUSTOM_ONLY":
        return _emit(raw_custom)

    # 未定沖銷部分：策略補配
    def _eligible_buys_for_sell(all_buys: List[Lot], sell: Lot) -> List[Lot]:
        return [b for b in all_buys if b.qty > 0 and b.date <= sell.date]

    def _strategy_order(buys_for_sell: List[Lot], strategy: str) -> List[Lot]:
        if not buys_for_sell:
            return []
        if strategy == "FIFO":
            return sorted(buys_for_sell, key=lambda b: (b.date, b.trade_id))
        if strategy == "CONSERVATIVE":
            # 成本高先出 → 帳上損益較低
            return sorted(buys_for_sell, key=lambda b: (b.price, b.date, b.trade_id), reverse=True)
        if strategy == "OPTIMISTIC":
            # 成本低先出 → 帳上損益較高
            return sorted(buys_for_sell, key=lambda b: (b.price, b.date, b.trade_id))
        if strategy == "MEAN":
            tot_qty = sum(b.qty for b in buys_for_sell)
            avg = (sum(b.qty * b.price for b in buys_for_sell) / tot_qty) if tot_qty else 0.0
            return sorted(buys_for_sell, key=lambda b: (abs(b.price - avg), b.date, b.trade_id))
        return sorted(buys_for_sell, key=lambda b: (b.date, b.trade_id))

    def _strategy_match(all_buys: List[Lot], all_sells: List[Lot], strategy: str) -> List[Tuple[Lot, Lot, int, float]]:
        res: List[Tuple[Lot, Lot, int, float]] = []
        for s in sorted(all_sells, key=lambda x: (x.date, x.trade_id)):
            if s.qty <= 0:
                continue
            ordered = _strategy_order(_eligible_buys_for_sell(all_buys, s), strategy)
            for b in ordered:
                if s.qty <= 0:
                    break
                if b.qty <= 0:
                    continue
                q = min(s.qty, b.qty)
                if q <= 0:
                    continue
                pnl = (s.price - b.price) * q
                res.append((b, s, q, pnl))
                b.qty -= q
                s.qty -= q
        return res

    # policy -> strategy key
    strat = None
    if policy in ("CUSTOM_PLUS_FIFO", "CUSTOM_PLUS_UNMATCHED_FIFO", "CUSTOM_PLUS_FIF0"):
        strat = "FIFO"
    elif policy in ("CUSTOM_PLUS_CONSERVATIVE", "CUSTOM_PLUS_UNMATCHED_CONSERVATIVE"):
        strat = "CONSERVATIVE"
    elif policy in ("CUSTOM_PLUS_OPTIMISTIC", "CUSTOM_PLUS_UNMATCHED_OPTIMISTIC"):
        strat = "OPTIMISTIC"
    elif policy in ("CUSTOM_PLUS_MEAN", "CUSTOM_PLUS_UNMATCHED_MEAN", "CUSTOM_PLUS_AVG"):
        strat = "MEAN"
    elif policy in ("FIFO", "CONSERVATIVE", "OPTIMISTIC", "MEAN"):
        # 允許純策略（未使用自定規則）
        strat = policy
        buys0 = [Lot(trade_id=b.trade_id, qty=int(b.qty), price=float(b.price), date=str(b.date)) for b in buys]
        sells0 = [Lot(trade_id=s.trade_id, qty=int(s.qty), price=float(s.price), date=str(s.date)) for s in sells]
        raw_custom = []
    else:
        # 未知 policy：回退到僅自定
        return _emit(raw_custom)

    raw_strategy = _strategy_match(buys0, sells0, strat)
    return _emit(raw_custom) + _emit(raw_strategy)
