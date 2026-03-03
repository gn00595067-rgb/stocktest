# -*- coding: utf-8 -*-
"""日成交彙總 pivot"""
import pandas as pd
from db.models import Trade


def build_daily_summary_pivot(trades, pivot_by: str):
    """
    pivot_by: "date" | "stock_id" | "user"
    回傳 pivot DataFrame，並有合計列（合計欄在最後一欄）。
    """
    if not trades:
        return pd.DataFrame()
    rows = []
    for t in trades:
        rows.append({
            "date": str(t.trade_date),
            "stock_id": t.stock_id,
            "user": t.user,
            "side": t.side,
            "quantity": t.quantity,
            "amount": t.quantity * t.price,
        })
    df = pd.DataFrame(rows)
    if pivot_by == "date":
        summary = df.pivot_table(index="date", columns="stock_id", values="quantity", aggfunc="sum", fill_value=0)
    elif pivot_by == "stock_id":
        summary = df.pivot_table(index="stock_id", columns="date", values="quantity", aggfunc="sum", fill_value=0)
    else:
        summary = df.pivot_table(index="user", columns="stock_id", values="quantity", aggfunc="sum", fill_value=0)
    summary["合計"] = summary.sum(axis=1)
    return summary
