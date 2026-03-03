# -*- coding: utf-8 -*-
import pytest
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.pnl_engine import Lot, compute_matches


def test_fifo():
    buys = [Lot(1, 100, 100.0, "2024-01-01"), Lot(2, 100, 102.0, "2024-01-02")]
    sells = [Lot(3, 150, 105.0, "2024-01-03")]
    r = compute_matches(buys, sells, "FIFO")
    assert len(r) == 2
    assert sum(x[2] for x in r) == 150
    assert abs(sum(x[5] for x in r) - (5 * 100 + 3 * 50)) < 1e-6


def test_mincost_optimistic():
    buys = [Lot(1, 100, 100.0, ""), Lot(2, 100, 110.0, "")]
    sells = [Lot(3, 100, 105.0, "")]
    r = compute_matches(buys, sells, "MINCOST")
    assert len(r) == 1
    assert r[0][2] == 100 and r[0][3] == 100.0
    assert abs(r[0][5] - 500.0) < 1e-6


def test_maxcost_conservative():
    buys = [Lot(1, 100, 100.0, ""), Lot(2, 100, 110.0, "")]
    sells = [Lot(3, 100, 105.0, "")]
    r = compute_matches(buys, sells, "MAXCOST")
    assert len(r) == 1
    assert r[0][3] == 110.0
    assert abs(r[0][5] - (-500.0)) < 1e-6


def test_average():
    buys = [Lot(1, 100, 100.0, ""), Lot(2, 100, 200.0, "")]
    sells = [Lot(3, 100, 150.0, "")]
    r = compute_matches(buys, sells, "AVERAGE")
    assert len(r) == 1
    assert r[0][2] == 100
    assert abs(r[0][3] - 150.0) < 1e-6
    assert abs(r[0][5]) < 1e-6
