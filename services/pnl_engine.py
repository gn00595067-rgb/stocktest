# -*- coding: utf-8 -*-
"""
損益沖銷演算法：依 policy 計算已實現損益與持倉。
- FIFO: 先買先賣（依交易日期/ID）
- LIFO: 後買先賣
- MINCOST(樂觀): 先沖銷成本最低的買單
- MAXCOST(保守): 先沖銷成本最高的買單
- AVERAGE: 均價沖銷（加權平均成本）
- CLOSEST: 最接近兩平的買單優先（買價最接近賣價）
"""
from dataclasses import dataclass, field
from typing import List, Tuple

@dataclass
class Lot:
    trade_id: int
    qty: int
    price: float
    date: str


def _fifo_match(sells: List[Lot], buys: List[Lot]) -> List[Tuple[Lot, Lot, int, float]]:
    """FIFO: 依買單順序沖銷，回傳 (buy_lot, sell_lot, matched_qty, pnl)"""
    result = []
    buy_idx = 0
    for sl in sells:
        remain = sl.qty
        while remain > 0 and buy_idx < len(buys):
            bl = buys[buy_idx]
            match_qty = min(remain, bl.qty)
            if match_qty <= 0:
                buy_idx += 1
                continue
            pnl = (sl.price - bl.price) * match_qty
            result.append((bl, sl, match_qty, pnl))
            remain -= match_qty
            bl.qty -= match_qty
            if bl.qty <= 0:
                buy_idx += 1
        sl.qty = remain
    return result


def _lifo_match(sells: List[Lot], buys: List[Lot]) -> List[Tuple[Lot, Lot, int, float]]:
    """LIFO: 依買單逆序沖銷"""
    result = []
    for sl in sells:
        remain = sl.qty
        idx = len(buys) - 1
        while remain > 0 and idx >= 0:
            bl = buys[idx]
            match_qty = min(remain, bl.qty)
            if match_qty <= 0:
                idx -= 1
                continue
            pnl = (sl.price - bl.price) * match_qty
            result.append((bl, sl, match_qty, pnl))
            remain -= match_qty
            bl.qty -= match_qty
            if bl.qty <= 0:
                idx -= 1
            idx -= 1
        sl.qty = remain
    return result


def _mincost_match(sells: List[Lot], buys: List[Lot]) -> List[Tuple[Lot, Lot, int, float]]:
    """MINCOST(樂觀): 先沖銷成本最低的買單"""
    buys_sorted = sorted(buys, key=lambda x: x.price)
    return _fifo_match(sells, buys_sorted)


def _maxcost_match(sells: List[Lot], buys: List[Lot]) -> List[Tuple[Lot, Lot, int, float]]:
    """MAXCOST(保守): 先沖銷成本最高的買單"""
    buys_sorted = sorted(buys, key=lambda x: -x.price)
    return _fifo_match(sells, buys_sorted)


def _average_match(sells: List[Lot], buys: List[Lot]) -> List[Tuple[Lot, Lot, int, float]]:
    """AVERAGE: 用加權均價當成本沖銷（虛擬一筆均價買單）"""
    total_qty = sum(b.qty for b in buys)
    if total_qty <= 0:
        return []
    avg = sum(b.qty * b.price for b in buys) / total_qty
    virtual = Lot(trade_id=-1, qty=total_qty, price=avg, date="")
    result = []
    for sl in sells:
        match_qty = min(sl.qty, virtual.qty)
        if match_qty <= 0:
            continue
        pnl = (sl.price - avg) * match_qty
        result.append((virtual, sl, match_qty, pnl))
        virtual.qty -= match_qty
        sl.qty -= match_qty
    return result


def _closest_match(sells: List[Lot], buys: List[Lot]) -> List[Tuple[Lot, Lot, int, float]]:
    """CLOSEST(最接近兩平): 買價最接近賣價的優先"""
    result = []
    buys_list = [b for b in buys if b.qty > 0]
    for sl in sells:
        remain = sl.qty
        while remain > 0 and buys_list:
            closest = min(buys_list, key=lambda b: abs(b.price - sl.price))
            match_qty = min(remain, closest.qty)
            if match_qty <= 0:
                buys_list.remove(closest)
                continue
            pnl = (sl.price - closest.price) * match_qty
            result.append((closest, sl, match_qty, pnl))
            remain -= match_qty
            closest.qty -= match_qty
            if closest.qty <= 0:
                buys_list.remove(closest)
        sl.qty = remain
    return result


def compute_matches(
    buys: List[Lot], sells: List[Lot], policy: str
) -> List[Tuple[int, int, int, float, float, float]]:
    """
    輸入買賣 Lot 列表（會複製，不修改原始），依 policy 沖銷。
    回傳 [(buy_trade_id, sell_trade_id, matched_qty, buy_price, sell_price, pnl), ...]
    """
    buys = [Lot(trade_id=b.trade_id, qty=b.qty, price=b.price, date=b.date) for b in buys]
    sells = [Lot(trade_id=s.trade_id, qty=s.qty, price=s.price, date=s.date) for s in sells]
    policy = (policy or "FIFO").upper()
    if policy == "FIFO":
        raw = _fifo_match(sells, buys)
    elif policy == "LIFO":
        raw = _lifo_match(sells, buys)
    elif policy == "MINCOST":
        raw = _mincost_match(sells, buys)
    elif policy == "MAXCOST":
        raw = _maxcost_match(sells, buys)
    elif policy == "AVERAGE":
        raw = _average_match(sells, buys)
    elif policy == "CLOSEST":
        raw = _closest_match(sells, buys)
    else:
        raw = _fifo_match(sells, buys)
    return [(bl.trade_id, sl.trade_id, qty, bl.price, sl.price, pnl) for bl, sl, qty, pnl in raw]
