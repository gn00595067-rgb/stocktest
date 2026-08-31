# -*- coding: utf-8 -*-
"""已實現損益彙整 realized_report 的行為測試。"""
import os
import sys
from datetime import date
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from reports.realized_report import (
    build_realized_ledger, summarize_ledger, aggregate_by, monthly_series,
)


def _t(id, user, sid, d, side, price, qty, fee=None, tax=None, dt=False):
    return NS(id=id, user=user, stock_id=sid, trade_date=d, side=side,
              price=price, quantity=qty, fee=fee, tax=tax, is_daytrade=dt, note=None)


def _sample():
    masters = {
        "2330": NS(name="台積電", industry_name="半導體"),
        "3189": NS(name="景碩", industry_name="電子零組件"),
    }
    trades = [
        _t(1, "Jonathan", "2330", date(2026, 1, 5), "BUY", 1000, 1000, 1425),
        _t(2, "Jonathan", "2330", date(2026, 3, 10), "SELL", 1100, 1000, 1567, 3300),
        _t(3, "Jonathan", "3189", date(2026, 2, 1), "BUY", 800, 2000, 2280),
        _t(4, "Jonathan", "3189", date(2026, 2, 1), "SELL", 780, 2000, 2223, 2340, dt=True),
        _t(5, "Peggy", "2330", date(2026, 4, 1), "BUY", 900, 1000, 1283),
        _t(6, "Peggy", "2330", date(2026, 4, 20), "SELL", 950, 1000, 1354, 2850),
    ]
    return trades, masters


def test_ledger_rows_and_signs():
    trades, masters = _sample()
    led = build_realized_ledger(trades, masters, "CUSTOM_PLUS_FIFO")
    assert len(led) == 3
    # 3189 當沖，且賠錢
    d = led[led["代號"] == "3189"].iloc[0]
    assert d["當沖"] is True or bool(d["當沖"]) is True
    assert d["淨損益"] < 0
    assert d["持有天數"] == 0
    # 2330 兩筆都賺
    assert (led[led["代號"] == "2330"]["淨損益"] > 0).all()


def test_summary_kpis():
    trades, masters = _sample()
    led = build_realized_ledger(trades, masters, "CUSTOM_PLUS_FIFO")
    s = summarize_ledger(led)
    assert s["筆數"] == 3
    assert s["獲利筆數"] == 2 and s["虧損筆數"] == 1
    assert round(s["勝率%"], 1) == 66.7
    assert s["當沖筆數"] == 1 and s["波段筆數"] == 2
    # 總淨損益 = 各筆加總
    assert abs(s["總淨損益"] - led["淨損益"].sum()) < 1e-6
    assert s["盈虧比"] > 0


def test_aggregate_and_monthly():
    trades, masters = _sample()
    led = build_realized_ledger(trades, masters, "CUSTOM_PLUS_FIFO")
    by_stock = aggregate_by(led, "代號")
    assert set(by_stock["代號"]) == {"2330", "3189"}
    # 2330 兩筆、勝率 100%
    row2330 = by_stock[by_stock["代號"] == "2330"].iloc[0]
    assert row2330["筆數"] == 2 and row2330["勝率%"] == 100.0

    m = monthly_series(led)
    assert list(m["月份"]) == ["2026-02", "2026-03", "2026-04"]
    # 累積最後一格 = 總損益
    assert abs(m["累積已實現"].iloc[-1] - led["淨損益"].sum()) < 1e-6


def test_empty_inputs():
    assert build_realized_ledger([], {}, "CUSTOM_PLUS_FIFO").empty
    s = summarize_ledger(build_realized_ledger([], {}, "CUSTOM_PLUS_FIFO"))
    assert s["筆數"] == 0 and s["總淨損益"] == 0.0
