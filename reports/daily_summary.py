# -*- coding: utf-8 -*-
"""日成交彙總 pivot"""
import pandas as pd
from db.models import Trade


def build_daily_summary_pivot(trades, pivot_by: str, masters=None):
    """
    pivot_by: "date" | "stock_id" | "user"
    masters: 可選，dict stock_id -> StockMaster（用於顯示股票名稱）
    回傳 pivot DataFrame，索引會還原為第一欄（日期/股票/買賣人），股票欄位會顯示「代號 名稱」。
    """
    if not trades:
        return pd.DataFrame()
    masters = masters or {}
    rows = []
    for t in trades:
        # 買＝正數、賣＝負數，彙總後可看出淨買賣
        sign = 1 if (getattr(t, "side", "") or "").upper() == "BUY" else -1
        signed_qty = sign * t.quantity
        rows.append({
            "date": str(t.trade_date),
            "stock_id": t.stock_id,
            "user": t.user,
            "side": t.side,
            "quantity": t.quantity,
            "signed_qty": signed_qty,
            "amount": t.quantity * t.price,
        })
    df = pd.DataFrame(rows)
    if pivot_by == "date":
        summary = df.pivot_table(index="date", columns="stock_id", values="signed_qty", aggfunc="sum", fill_value=0)
        summary.index.name = "日期"
        summary = summary.reset_index()
        # 欄位為股票代號，改為「代號 名稱」
        summary = _rename_stock_columns(summary, masters, skip_cols={"日期"})
    elif pivot_by == "stock_id":
        summary = df.pivot_table(index="stock_id", columns="date", values="signed_qty", aggfunc="sum", fill_value=0)
        summary.index.name = "股票代號"
        summary = summary.reset_index()
        summary.insert(1, "股票名稱", summary["股票代號"].map(lambda sid: getattr(masters.get(sid), "name", None) or ""))
        # 日期欄保留原名即可
    else:
        summary = df.pivot_table(index="user", columns="stock_id", values="signed_qty", aggfunc="sum", fill_value=0)
        summary.index.name = "買賣人"
        summary = summary.reset_index()
        summary = _rename_stock_columns(summary, masters, skip_cols={"買賣人"})
    if "合計" not in summary.columns:
        summary["合計"] = summary.select_dtypes(include="number").sum(axis=1)
    return summary


def _rename_stock_columns(df, masters, skip_cols=None):
    """將欄名中為 stock_id 的改為「代號 名稱」"""
    skip_cols = skip_cols or set()
    rename = {}
    for col in df.columns:
        if col in skip_cols or col == "合計":
            continue
        key = str(col) if col is not None else ""
        m = masters.get(key) if isinstance(masters, dict) else None
        name = getattr(m, "name", None) if m else None
        if name:
            rename[col] = f"{col} {name}"
        # 若無主檔名稱則保留原欄名
    return df.rename(columns=rename)
