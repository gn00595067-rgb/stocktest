# -*- coding: utf-8 -*-
"""底價（扣除賣出手續費與證交稅後打平的最低賣價）"""
from services.trade_fees import (
    DEFAULT_FEE_RATE, DEFAULT_TAX_RATE, breakeven_sell_price,
    estimate_broker_fee, estimate_sell_tax, tick_size,
)


def _net(p, q, is_etf=False):
    return p * q - estimate_broker_fee(p, q, DEFAULT_FEE_RATE) - estimate_sell_tax(p, q, is_etf, DEFAULT_TAX_RATE)


def _be(cost, q, is_etf=False):
    return breakeven_sell_price(cost, q, is_etf, DEFAULT_FEE_RATE, DEFAULT_TAX_RATE)


def test_breakeven_is_minimal_and_covers_cost():
    for cost, q in [(1_793_134, 1900), (3_808_926, 21000), (609_716, 2000), (1_540_294, 700), (10_050, 1000)]:
        p = _be(cost, q)
        assert _net(p, q) >= cost
        prev = round(p - tick_size(p - 1e-9), 2)
        assert _net(prev, q) < cost
        assert abs(round(p / tick_size(p), 6) - round(p / tick_size(p))) < 1e-6  # 符合升降單位


def test_breakeven_above_avg_cost():
    # 景碩：成本均價 943.75 → 底價須高於均價（要補賣出費稅），升降單位 1 元
    p = _be(943.75 * 1900, 1900)
    assert p > 943.75 and p == int(p)


def test_breakeven_etf_uses_etf_tax():
    p_etf = _be(100_000, 1000, is_etf=True)
    p_stock = _be(100_000, 1000)
    assert p_etf < p_stock


def test_breakeven_no_position():
    assert _be(0, 0) is None
    assert _be(1000, 0) is None
