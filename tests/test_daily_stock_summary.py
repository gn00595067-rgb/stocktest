# -*- coding: utf-8 -*-
"""當日交易明細：每股損益簡表。"""
import pandas as pd

from reports.daily_detail_report import build_daily_stock_summary, STOCK_SUMMARY_COLUMNS


def _row(stock, side, qty, gross, fee, tax, pnl=None):
    sell = side == "SELL"
    return {
        "股票": stock, "買/賣": side, "股數": qty, "成交金額": gross, "手續費": fee, "證交稅": tax,
        "交割應收": gross - fee - tax if sell else 0.0,
        "交割應付": 0.0 if sell else gross + fee,
        "_pnl_raw": pnl if sell else None,
    }


def test_summary_groups_by_stock_and_adds_total():
    detail = pd.DataFrame([
        _row("1301 台塑", "BUY", 1000, 81300, 28, 0),
        _row("1301 台塑", "BUY", 1000, 78600, 28, 0),
        _row("2408 南亞科", "SELL", 1000, 527000, 187, 1581, pnl=5047.4),
        _row("2408 南亞科", "BUY", 100, 52000, 18, 0),
    ])
    out = build_daily_stock_summary(detail)
    assert list(out.columns) == STOCK_SUMMARY_COLUMNS
    assert list(out["股票"]) == ["1301 台塑", "2408 南亞科", "合計"]
    tw = out.iloc[0]
    assert (tw["買進股數"], tw["買進支出"], tw["賣出股數"], tw["損益"]) == (2000, 159956, 0, 0)
    ny = out.iloc[1]
    assert (ny["買進股數"], ny["賣出股數"], ny["賣出收入"], ny["損益"]) == (100, 1000, 525232, 5047)
    total = out.iloc[2]
    assert total["買進支出"] == 159956 + 52018
    assert total["手續費"] == 28 + 28 + 187 + 18
    assert total["損益"] == 5047
    assert pd.isna(total["買進股數"]) and pd.isna(total["賣出股數"])


def test_summary_empty():
    out = build_daily_stock_summary(pd.DataFrame())
    assert out.empty and list(out.columns) == STOCK_SUMMARY_COLUMNS
