# -*- coding: utf-8 -*-
"""買賣人手續費折數（docs/specs/買賣人手續費折數.md）。"""
import pytest

import services.trader_service as ts
from services.trade_fees import (
    BASE_FEE_RATE, DEFAULT_DISCOUNT, estimate_broker_fee, fee_rate_for_discount, fees_for_trade,
)


@pytest.fixture
def discounts(monkeypatch):
    data = {"Peggy姐": (0.25, None), "王小姐": (0.28, 0.25), "雅雲姐": (None, None)}
    monkeypatch.setattr(ts, "load_fee_discounts", lambda: data)
    return data


def test_rate_for_discount_defaults_to_general():
    assert fee_rate_for_discount(None) == pytest.approx(BASE_FEE_RATE * 0.28)
    assert fee_rate_for_discount(0.25) == pytest.approx(0.00035625)
    assert DEFAULT_DISCOUNT == 0.28


def test_peggy_25_vs_general_28():
    # 友達 1,000 股 @ 35.8＝35,800：2.5 折 12 元（與正式資料一致）、2.8 折 14 元
    assert estimate_broker_fee(35.8, 1000, fee_rate=fee_rate_for_discount(0.25)) == 12
    assert estimate_broker_fee(35.8, 1000, fee_rate=fee_rate_for_discount(0.28)) == 14


def test_floor_exact_integer():
    # 100,000 × 0.1425% × 0.28 ＝ 39.9 → 39；160,000 × 0.1425% × 0.25 ＝ 57 整，不可少 1 元
    assert estimate_broker_fee(100.0, 1000, fee_rate=fee_rate_for_discount(0.28)) == 39
    assert estimate_broker_fee(160.0, 1000, fee_rate=fee_rate_for_discount(0.25)) == 57


def test_min_fee_one_dollar():
    assert estimate_broker_fee(10.0, 1, fee_rate=fee_rate_for_discount(0.28)) == 1


def test_trader_discounts(discounts):
    assert ts.trader_discounts("Peggy姐") == (0.25, None)
    assert ts.trader_discounts("王小姐") == (0.28, 0.25)
    assert ts.trader_discounts("雅雲姐") == (0.28, None)       # 空白＝一般人
    assert ts.trader_discounts("沒設定的人") == (0.28, None)


def test_trader_fee_rate_and_per_trade_override(discounts):
    assert ts.trader_fee_rate("Peggy姐") == pytest.approx(0.00035625)
    assert ts.trader_fee_rate("王小姐") == pytest.approx(BASE_FEE_RATE * 0.28)
    assert ts.trader_fee_rate("王小姐", 0.25) == pytest.approx(0.00035625)   # 這筆選 2.5 折


def test_fees_for_trade_uses_given_rate(discounts):
    fee, tax = fees_for_trade("SELL", 35.8, 1000, is_daytrade=True, fee_rate=ts.trader_fee_rate("雅雲姐"))
    assert (fee, tax) == (14, 53)


def test_discount_label():
    assert ts.discount_label(0.25) == "2.5 折"
    assert ts.discount_label(None) == "2.8 折"
