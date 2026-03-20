# -*- coding: utf-8 -*-
"""日成交明細表：每筆交易的詳細紀錄（參考券商日成交表格式）"""
import pandas as pd
from datetime import date
from collections import defaultdict
from typing import Optional, List, Tuple

from db.models import Trade
from services.pnl_engine import Lot, compute_matches, net_pnl_for_match


def build_daily_detail_df(trades, masters, policy: str = "CUSTOM", filter_date=None, custom_rules: Optional[List[Tuple[int, int, int]]] = None):
    """
    建構日成交明細表，一筆交易一行。
    policy 僅支援自定沖銷；custom_rules 必傳。
    """
    if not trades:
        return pd.DataFrame()
    masters = masters or {}

    # 計算每筆賣出的已實現損益（依 policy）
    buys_by_stock = defaultdict(list)
    sells_by_stock = defaultdict(list)
    for t in trades:
        lot = Lot(t.id, t.quantity, t.price, str(t.trade_date))
        if (t.side or "").strip().upper() in ("BUY", "配股"):
            buys_by_stock[t.stock_id].append(lot)
        else:
            sells_by_stock[t.stock_id].append(lot)
    trade_by_id = {t.id: t for t in trades}
    pnl_by_sell_id = defaultdict(float)
    for sid, sells in sells_by_stock.items():
        buys = buys_by_stock.get(sid, [])
        matches = compute_matches(buys, sells, policy, custom_rules=custom_rules)
        for m in matches:
            _buy_id, sell_id, _qty, _bp, _sp, _ = m
            pnl_by_sell_id[sell_id] += net_pnl_for_match(m, trade_by_id)

    rows = []
    for t in sorted(trades, key=lambda x: (x.trade_date, x.stock_id, x.id)):
        raw_side = (t.side or "").strip().upper()
        side_cht = "配股" if raw_side == "配股" else ("買" if raw_side == "BUY" else "賣")
        price = float(t.price or 0)
        qty = int(t.quantity or 0)
        fee = float(t.fee or 0)
        tax = float(t.tax or 0)
        gross = price * qty  # 成交金額

        if side_cht in ("買", "配股"):
            net = -(gross + fee)  # 淨付出
            pnl = None  # 買入無損益
        else:
            net = gross - fee - tax  # 淨收入
            pnl = pnl_by_sell_id.get(t.id, 0)

        m = masters.get(str(t.stock_id))
        name = getattr(m, "name", None) or ""
        company = f"{t.stock_id}{name}" if name else str(t.stock_id)

        rows.append({
            "誰名下": t.user,
            "買賣日": str(t.trade_date),
            "公司": company,
            "股數": qty,
            "股價": round(price, 2),
            "買/賣": side_cht,
            "當沖": bool(getattr(t, "is_daytrade", False)),
            "成交金額": round(gross, 0),
            "手續費": round(fee, 0),
            "證交稅": round(tax, 0),
            "淨收付": round(net, 0),
            "損益": round(pnl, 0) if pnl is not None else None,
        })

    df = pd.DataFrame(rows)
    if filter_date is not None:
        df = df[df["買賣日"] == str(filter_date)].copy()
    return df
