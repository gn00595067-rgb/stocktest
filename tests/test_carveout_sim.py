# -*- coding: utf-8 -*-
"""庫存模擬（docs/specs/庫存模擬.md）：用 Peggy Excel 的兩個例子驗算。"""
from datetime import date
from types import SimpleNamespace

import pytest

from services.carveout_sim import positions_by_person, simulate


def test_tsmc_example_without_fee():
    r = simulate(18000, 18000 * 2415, 10000, 2420, 0.001425 * 0.28, deduct_with_fee=False)
    assert r["left_qty"] == 8000
    assert r["left_avg"] == pytest.approx(2408.75)
    assert r["sim_cost_avg"] == pytest.approx(2420 * (1 + 0.000399))


def test_umc_example_without_fee():
    r = simulate(64000, 64000 * 130, 30000, 132, 0.000399, deduct_with_fee=False)
    assert round(r["left_avg"], 1) == 128.2


def test_with_fee_deducts_more_cost():
    a = simulate(18000, 18000 * 2415, 10000, 2420, 0.000399, deduct_with_fee=False)["left_avg"]
    b = simulate(18000, 18000 * 2415, 10000, 2420, 0.000399, deduct_with_fee=True)["left_avg"]
    assert b == pytest.approx((18000 * 2415 - 10000 * 2420 * 1.000399) / 8000)
    assert b < a


def test_no_sim_keeps_original():
    r = simulate(18000, 18000 * 2415, 0, None, 0.000399, False)
    assert r["left_qty"] == 18000 and r["left_avg"] == pytest.approx(2415)


def test_over_and_missing_price():
    assert simulate(5000, 5000 * 100, 6000, 100, 0.0004, False)["over"] is True
    r = simulate(5000, 5000 * 100, 1000, None, 0.0004, False)
    assert r["missing_price"] and r["left_avg"] is None
    assert simulate(5000, 5000 * 100, 5000, 100, 0.0004, False)["left_avg"] is None   # 全扣光：沒有均價


def _t(i, user, sid, side, qty, price, fee=0.0):
    return SimpleNamespace(id=i, user=user, stock_id=sid, side=side, quantity=qty, price=price,
                           trade_date=date(2026, 10, i), fee=fee, tax=0.0)


def test_positions_by_person_only_selected_and_open():
    trades = [
        _t(1, "Peggy姐", "2330", "BUY", 2000, 100, fee=10),
        _t(2, "Peggy姐", "2330", "SELL", 1000, 110),
        _t(3, "豪哥", "2330", "BUY", 1000, 120),
        _t(4, "雅雲", "2330", "BUY", 1000, 130),      # 沒選
        _t(5, "豪哥", "2303", "BUY", 1000, 50),
        _t(6, "豪哥", "2303", "SELL", 1000, 55),      # 賣光：不列
    ]
    pos = positions_by_person(trades, ["Peggy姐", "豪哥"], [])
    assert set(pos) == {"2330"}
    assert pos["2330"]["Peggy姐"][0] == 1000
    assert pos["2330"]["Peggy姐"][1] == pytest.approx(100 * 1000 + 5)   # 先進先出，剩一半成本＋一半買進手續費
    assert pos["2330"]["豪哥"] == (1000, pytest.approx(120000))
