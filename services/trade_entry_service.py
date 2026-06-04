# -*- coding: utf-8 -*-
"""交易輸入頁：持倉彙總、損益、剩餘買進批次、沖銷預覽"""
import math
from collections import defaultdict
from datetime import date, timedelta
from typing import Callable, Dict, List, Optional, Tuple

import pandas as pd


def safe_int_qty(val, default: int = 0) -> int:
    """安全轉整數：None / NaN / 空白 / 字串 none 皆回 default（避免 data_editor 空值爆錯）。"""
    if val is None:
        return default
    if isinstance(val, str):
        s = val.strip()
        if not s or s.lower() in ("none", "nan", "-", "null"):
            return default
        try:
            f = float(s)
            if math.isnan(f):
                return default
            return max(0, int(f))
        except ValueError:
            return default
    if isinstance(val, float) and math.isnan(val):
        return default
    try:
        if pd.isna(val):
            return default
    except (TypeError, ValueError):
        pass
    try:
        f = float(val)
        if math.isnan(f):
            return default
        return max(0, int(f))
    except (TypeError, ValueError):
        return default

from services.pnl_engine import Lot, compute_matches, net_pnl_for_match
from services.position_cost import compute_position_and_cost_by_stock, _is_buy
from services.trade_fees import estimate_broker_fee


def _filter_trader(trades, trader: Optional[str]):
    if not trader:
        return list(trades)
    return [t for t in trades if (getattr(t, "user", None) or "").strip() == trader.strip()]


def get_open_buy_lots(
    trades,
    stock_id: str,
    trader: str,
    custom_rules: List[Tuple[int, int, int]],
    policy: str = "CUSTOM_PLUS_FIFO",
) -> List[dict]:
    """未沖銷完的買進批次（供賣出配對選擇）。"""
    ts = _filter_trader(trades, trader)
    ts = [t for t in ts if str(t.stock_id).strip() == str(stock_id).strip()]
    buys = [
        Lot(t.id, int(t.quantity or 0), float(t.price or 0), str(t.trade_date))
        for t in ts if _is_buy(t)
    ]
    sells = [
        Lot(t.id, int(t.quantity or 0), float(t.price or 0), str(t.trade_date))
        for t in ts if not _is_buy(t)
    ]
    if not buys:
        return []
    buys_sorted = sorted(buys, key=lambda b: (b.date, b.trade_id))
    sells_sorted = sorted(sells, key=lambda s: (s.date, s.trade_id))
    matches = compute_matches(list(buys_sorted), list(sells_sorted), policy, custom_rules=custom_rules or [])
    used = defaultdict(int)
    for m in matches:
        used[m[0]] += m[2]
    trade_by_id = {t.id: t for t in ts}
    lots = []
    for b in buys_sorted:
        rem = b.qty - used.get(b.trade_id, 0)
        if rem <= 0:
            continue
        t = trade_by_id.get(b.trade_id)
        fee = float(getattr(t, "fee", None) or 0)
        lots.append({
            "trade_id": b.trade_id,
            "date": b.date,
            "price": b.price,
            "remaining_qty": rem,
            "original_qty": int(getattr(t, "quantity", 0) or b.qty),
            "fee": fee,
        })
    return lots


def estimate_match_row_net_pnl(
    sell_price: float,
    buy_price: float,
    matched_qty: int,
    buy_trade,
    sell_fee_est: float,
    sell_tax_est: float,
    sell_qty_total: int,
) -> Tuple[float, float]:
    """
    單筆沖銷列的毛損益與淨損益（含費稅估算）。
    買進手續費：trades.fee 有值則按比例；否則依費率估算。
    賣出費稅：依表單估算的 sell_fee_est / sell_tax_est 按股數比例分攤。
    """
    qty = int(matched_qty)
    if qty <= 0:
        return 0.0, 0.0
    gross = (float(sell_price) - float(buy_price)) * qty
    buy_fee = 0.0
    if buy_trade:
        bq = int(getattr(buy_trade, "quantity", 0) or 0)
        if bq > 0:
            stored_fee = getattr(buy_trade, "fee", None)
            if stored_fee is not None and float(stored_fee) > 0:
                buy_fee = float(stored_fee) * (qty / bq)
            else:
                buy_fee = estimate_broker_fee(float(buy_price), bq) * (qty / bq)
    sell_total = max(1, int(sell_qty_total))
    sell_fee_part = float(sell_fee_est or 0) * (qty / sell_total)
    sell_tax_part = float(sell_tax_est or 0) * (qty / sell_total)
    net = gross - buy_fee - sell_fee_part - sell_tax_part
    return gross, net


def fifo_match_plan(sell_qty: int, open_lots: List[dict]) -> List[Tuple[int, int]]:
    """依買進日期由舊到新配對。"""
    plan = []
    left = sell_qty
    for lot in sorted(open_lots, key=lambda x: (x["date"], x["trade_id"])):
        if left <= 0:
            break
        q = min(left, lot["remaining_qty"])
        if q > 0:
            plan.append((lot["trade_id"], q))
            left -= q
    return plan


def recent_days_match_plan(sell_qty: int, open_lots: List[dict], days: int = 3) -> List[Tuple[int, int]]:
    """僅配對近 N 日內買進（由新到舊）。"""
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    recent = [lot for lot in open_lots if str(lot["date"])[:10] >= cutoff]
    plan = []
    left = sell_qty
    for lot in sorted(recent, key=lambda x: (x["date"], x["trade_id"]), reverse=True):
        if left <= 0:
            break
        q = min(left, lot["remaining_qty"])
        if q > 0:
            plan.append((lot["trade_id"], q))
            left -= q
    return plan


def preview_avg_cost_after_buy(
    current_qty: int,
    current_cost: float,
    price: float,
    quantity: int,
    fee: float,
) -> Tuple[int, float, float]:
    """模擬買進後的新均價。回傳 (新股數, 新總成本, 新均價)。"""
    if quantity <= 0:
        avg = current_cost / current_qty if current_qty else 0.0
        return current_qty, current_cost, avg
    new_qty = current_qty + quantity
    new_cost = current_cost + price * quantity + fee
    avg = new_cost / new_qty if new_qty else 0.0
    return new_qty, new_cost, avg


def realized_pnl_for_sell_plan(
    sell_price: float,
    sell_qty: int,
    sell_fee: float,
    sell_tax: float,
    match_plan: List[Tuple[int, int]],
    trade_by_id: dict,
) -> float:
    """依配對計畫估算賣出淨損益。"""
    if sell_qty <= 0 or not match_plan:
        return 0.0
    total = 0.0
    for buy_id, qty in match_plan:
        buy_t = trade_by_id.get(buy_id)
        if not buy_t:
            continue
        bp = float(buy_t.price or 0)
        gross = (sell_price - bp) * qty
        buy_fee = float(getattr(buy_t, "fee", None) or 0) * (qty / (buy_t.quantity or 1))
        sf = sell_fee * (qty / sell_qty)
        st = sell_tax * (qty / sell_qty)
        total += gross - buy_fee - sf - st
    return total


def compute_realized_in_range(
    trades,
    trader: Optional[str],
    start_date: date,
    end_date: date,
    custom_rules: List[Tuple[int, int, int]],
    policy: str,
) -> Tuple[float, Dict[str, float]]:
    """區間內已實現損益（賣出日落在區間）。回傳 (總計, {stock_id: pnl})。"""
    ts = _filter_trader(trades, trader)
    sells_in_range = [
        t for t in ts
        if start_date <= t.trade_date <= end_date and not _is_buy(t)
    ]
    if not sells_in_range:
        return 0.0, {}
    trade_by_id = {t.id: t for t in ts}
    by_stock = defaultdict(list)
    buys_by_stock = defaultdict(list)
    for t in ts:
        lot = Lot(t.id, int(t.quantity or 0), float(t.price or 0), str(t.trade_date))
        if _is_buy(t):
            buys_by_stock[t.stock_id].append(lot)
    for t in sells_in_range:
        lot = Lot(t.id, int(t.quantity or 0), float(t.price or 0), str(t.trade_date))
        by_stock[t.stock_id].append(lot)
    per_stock = {}
    total = 0.0
    for sid, sells in by_stock.items():
        buys = buys_by_stock.get(sid, [])
        matches = compute_matches(buys, sells, policy, custom_rules=custom_rules or [])
        pnl = sum(net_pnl_for_match(m, trade_by_id) for m in matches)
        per_stock[sid] = pnl
        total += pnl
    return total, per_stock


def build_holdings_summary(
    trades,
    masters: dict,
    trader: Optional[str],
    custom_rules: List[Tuple[int, int, int]],
    policy: str,
    get_quote_fn: Callable,
    period_start: date,
    period_end: date,
    today: Optional[date] = None,
) -> List[dict]:
    """
    持倉清單（依股票一列），含即時價、均價、當日/期間已實現、未實現。
    """
    today = today or date.today()
    ts = _filter_trader(trades, trader)
    pos = compute_position_and_cost_by_stock(ts, custom_rules=custom_rules, policy=policy)
    _, period_by_stock = compute_realized_in_range(
        trades, trader, period_start, period_end, custom_rules, policy
    )
    _, today_by_stock = compute_realized_in_range(
        trades, trader, today, today, custom_rules, policy
    )
    rows = []
    for sid, info in sorted(pos.items(), key=lambda x: x[0]):
        qty = info["qty"]
        cost = info["cost"]
        avg = cost / qty if qty else 0.0
        quote = get_quote_fn(sid) if get_quote_fn else None
        price = float(quote["price"]) if quote else avg
        chg_pct = float(quote.get("change_pct", 0)) if quote else 0.0
        m = masters.get(sid)
        unrealized = (price - avg) * qty
        rows.append({
            "stock_id": sid,
            "name": (getattr(m, "name", None) or sid) if m else sid,
            "qty": qty,
            "avg_cost": round(avg, 4),
            "price": price,
            "change_pct": chg_pct,
            "market_value": round(price * qty, 2),
            "unrealized": round(unrealized, 2),
            "realized_today": round(today_by_stock.get(sid, 0.0), 2),
            "realized_period": round(period_by_stock.get(sid, 0.0), 2),
            "total_pnl": round(unrealized + period_by_stock.get(sid, 0.0), 2),
        })
    return rows
