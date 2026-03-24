# -*- coding: utf-8 -*-
"""個股明細表：單一股票的「已出售」與「庫存」明細（參考券商格式）"""
import pandas as pd
from collections import defaultdict
from typing import Optional, List, Tuple

from db.models import Trade
from services.pnl_engine import Lot, compute_matches, net_pnl_for_match


def build_stock_sold_df(stock_id: str, trades, masters, policy: str, custom_rules: Optional[List[Tuple[int, int, int]]] = None):
    """
    已出售：該股票所有已沖銷的買→賣明細，一筆沖銷一行。
    欄位：買賣日、公司、股數、股價、買/賣、收/付價金、手續費、買股票支出、出售日、賣價、賣出股數、賣出金額、單筆損益、累計損益
    """
    masters = masters or {}
    trades = [t for t in trades if t.stock_id == stock_id]
    buys = [Lot(t.id, t.quantity, t.price, str(t.trade_date)) for t in trades if (t.side or "").strip().upper() in ("BUY", "配股")]
    sells = [Lot(t.id, t.quantity, t.price, str(t.trade_date)) for t in trades if (t.side or "").upper() == "SELL"]
    if not buys or not sells:
        return pd.DataFrame(), 0.0

    matches = compute_matches(buys, sells, policy, custom_rules=custom_rules)
    trade_by_id = {t.id: t for t in trades}
    m = masters.get(str(stock_id))
    company = f"{stock_id} {getattr(m, 'name', None) or ''}".strip()

    rows = []
    cum_pnl = 0.0
    for m_tuple in matches:
        buy_id, sell_id, qty, buy_price, sell_price, _ = m_tuple
        pnl = net_pnl_for_match(m_tuple, trade_by_id)
        buy_t = trade_by_id.get(buy_id)
        sell_t = trade_by_id.get(sell_id)
        if not buy_t or not sell_t:
            continue
        buy_fee = float(buy_t.fee or 0) * (qty / buy_t.quantity) if buy_t.quantity else 0
        sell_fee = float(sell_t.fee or 0) * (qty / sell_t.quantity) if sell_t.quantity else 0
        sell_tax = float(sell_t.tax or 0) * (qty / sell_t.quantity) if sell_t.quantity else 0
        pay = qty * buy_price
        buy_expense = pay + buy_fee
        sell_amount = qty * sell_price
        cum_pnl += pnl
        buy_user = getattr(buy_t, "user", None) or ""
        rows.append({
            "買賣人": buy_user,
            "買賣日": str(buy_t.trade_date),
            "公司": company,
            "股數": qty,
            "股價": round(buy_price, 2),
            "買/賣": "買→賣",
            "當沖": bool(getattr(sell_t, "is_daytrade", False)) or (buy_t.trade_date == sell_t.trade_date),
            "收/付價金": round(pay, 0),
            "手續費": round(buy_fee, 0),
            "買股票支出": round(buy_expense, 0),
            "出售日": str(sell_t.trade_date),
            "賣價": round(sell_price, 2),
            "賣出股數": qty,
            "賣出金額": round(sell_amount, 0),
            "賣出手續費": round(sell_fee, 0),
            "證交稅": round(sell_tax, 0),
            "單筆損益": round(pnl, 0),
            "累計損益": round(cum_pnl, 0),
        })
    total_revenue = sum(r["賣出金額"] for r in rows)
    return pd.DataFrame(rows), total_revenue


def build_stock_inventory_df(stock_id: str, trades, masters, policy: str, custom_rules: Optional[List[Tuple[int, int, int]]] = None):
    """
    庫存：該股票尚未賣出的買單明細（沖銷後剩餘部位）。
    欄位：買賣日、公司、股數、股價、買/賣、收/付價金、手續費、買股票支出、單筆損益、累計損益
    並回傳小計與均價分析用資料。
    """
    masters = masters or {}
    trades = [t for t in trades if t.stock_id == stock_id]
    buys = [Lot(t.id, t.quantity, t.price, str(t.trade_date)) for t in trades if (t.side or "").strip().upper() in ("BUY", "配股")]
    sells = [Lot(t.id, t.quantity, t.price, str(t.trade_date)) for t in trades if (t.side or "").upper() == "SELL"]
    if not buys:
        return pd.DataFrame(), {"庫存股數": 0, "原始成本": 0, "原始均價": 0}

    matches = compute_matches(buys, sells, policy, custom_rules=custom_rules)
    remaining_by_buy = defaultdict(int)
    for b in buys:
        remaining_by_buy[b.trade_id] = b.qty
    for buy_id, _s, qty, *_ in matches:
        remaining_by_buy[buy_id] -= qty

    trade_by_id = {t.id: t for t in trades}
    m = masters.get(str(stock_id))
    company = f"{stock_id} {getattr(m, 'name', None) or ''}".strip()

    rows = []
    cum_cost = 0.0
    for b in buys:
        rem = remaining_by_buy.get(b.trade_id, 0)
        if rem <= 0:
            continue
        t = trade_by_id.get(b.trade_id)
        if not t:
            continue
        fee = float(t.fee or 0) * (rem / t.quantity) if t.quantity else 0
        pay = rem * b.price
        expense = pay + fee
        cum_cost += expense
        buy_user = getattr(t, "user", None) or ""
        rows.append({
            "買賣人": buy_user,
            "買賣日": str(t.trade_date),
            "公司": company,
            "股數": rem,
            "股價": round(b.price, 2),
            "買/賣": "買",
            "當沖": bool(getattr(t, "is_daytrade", False)),
            "收/付價金": round(pay, 0),
            "手續費": round(fee, 0),
            "買股票支出": round(expense, 0),
            "單筆損益": round(-expense, 0),
            "累計損益": round(-cum_cost, 0),
        })

    total_qty = sum(r["股數"] for r in rows)
    total_cost = sum(r["買股票支出"] for r in rows)
    avg_price = total_cost / total_qty if total_qty else 0
    summary = {
        "庫存股數": total_qty,
        "原始成本": round(total_cost, 0),
        "原始均價": round(avg_price, 2),
        "結算後成本": round(total_cost, 0),
        "結算後均價": round(avg_price, 2),
    }
    return pd.DataFrame(rows), summary


def build_stock_detail(stock_id: str, trades, masters, policy: str, custom_rules: Optional[List[Tuple[int, int, int]]] = None):
    """
    回傳 (sold_df, sold_total_revenue, inventory_df, inventory_summary)。
    """
    sold_df, sold_revenue = build_stock_sold_df(stock_id, trades, masters, policy, custom_rules=custom_rules)
    inv_df, inv_summary = build_stock_inventory_df(stock_id, trades, masters, policy, custom_rules=custom_rules)
    return sold_df, sold_revenue, inv_df, inv_summary
