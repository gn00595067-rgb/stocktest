# -*- coding: utf-8 -*-
"""手續費與證交稅估算測試（含現股當沖證交稅減半）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.trade_fees import estimate_broker_fee, estimate_sell_tax, fees_for_trade


def test_broker_fee_standard_rate():
    # 2365 × 100 × 0.1425% = 337.0125 → 337
    assert estimate_broker_fee(2365, 100, 0.001425) == 337
    # 487.5 × 200 × 0.1425% = 138.9375 → 139
    assert estimate_broker_fee(487.5, 200, 0.001425) == 139


def test_broker_fee_minimum_20():
    # 小額成交手續費未滿 20 元以 20 元計
    assert estimate_broker_fee(10, 100, 0.001425) == 20


def test_sell_tax_normal():
    # 一般個股賣出 0.3%
    assert estimate_sell_tax(100, 1000, tax_rate=0.003) == 300


def test_sell_tax_daytrade_halved():
    # 現股當沖：一般個股證交稅減半 0.3% → 0.15%
    assert estimate_sell_tax(100, 1000, tax_rate=0.003, is_daytrade=True) == 150


def test_sell_tax_etf_not_halved_by_daytrade():
    # ETF 本即 0.1%，當沖不再另外減半
    assert estimate_sell_tax(100, 1000, is_etf=True, is_daytrade=True) == 100


def test_fees_for_trade_buy_has_no_tax():
    fee, tax = fees_for_trade("BUY", 500, 1000, is_daytrade=True)
    assert tax == 0.0
    assert fee > 0


def test_fees_for_trade_sell_daytrade():
    fee, tax = fees_for_trade("SELL", 100, 1000, is_daytrade=True)
    # 稅 = 100 × 1000 × 0.0015 = 150
    assert tax == 150
