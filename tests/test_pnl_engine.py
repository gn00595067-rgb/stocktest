# -*- coding: utf-8 -*-
import pytest
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.pnl_engine import Lot, compute_matches


def test_custom_match():
    """自定沖銷：依 (sell_id, buy_id, qty) 規則配對"""
    buys = [Lot(1, 100, 100.0, "2024-01-01"), Lot(2, 100, 102.0, "2024-01-02")]
    sells = [Lot(3, 150, 105.0, "2024-01-03")]
    rules = [(3, 1, 100), (3, 2, 50)]  # 賣3 配 買1 100股、買2 50股
    r = compute_matches(buys, sells, "CUSTOM", custom_rules=rules)
    assert len(r) == 2
    assert sum(x[2] for x in r) == 150
    assert r[0][0] == 1 and r[0][1] == 3 and r[0][2] == 100 and r[0][3] == 100.0
    assert r[1][0] == 2 and r[1][1] == 3 and r[1][2] == 50 and r[1][3] == 102.0
    assert abs(r[0][5] - (105 - 100) * 100) < 1e-6
    assert abs(r[1][5] - (105 - 102) * 50) < 1e-6


def test_custom_empty_rules():
    """無自定規則時回傳空列表"""
    buys = [Lot(1, 100, 100.0, "")]
    sells = [Lot(2, 50, 105.0, "")]
    r = compute_matches(buys, sells, "CUSTOM", custom_rules=None)
    assert r == []
    r2 = compute_matches(buys, sells, "CUSTOM", custom_rules=[])
    assert r2 == []
