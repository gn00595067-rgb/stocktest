# -*- coding: utf-8 -*-
"""當沖證交稅：只有當天買進可以對沖的股數減半。"""
from datetime import date
from types import SimpleNamespace as T

from services.trade_fees import daytrade_sell_taxes

D = date(2026, 9, 21)


def _t(i, side, q, p, dt=False, d=D, user="A", sid="3189"):
    return T(id=i, user=user, stock_id=sid, trade_date=d, side=side, quantity=q, price=p, is_daytrade=dt)


def test_only_same_day_bought_qty_is_halved():
    # 正式資料 3189 2026-09-21：買 3,000、賣 5,000（都勾當沖）→ 前 3 筆減半、後 2 筆全額
    trades = [_t(526, "BUY", 1000, 833), _t(527, "BUY", 1000, 829), _t(528, "BUY", 1000, 831),
              _t(529, "SELL", 1000, 863, True), _t(530, "SELL", 1000, 857, True), _t(531, "SELL", 1000, 852, True),
              _t(532, "SELL", 1000, 862, True), _t(533, "SELL", 1000, 865, True)]
    r = daytrade_sell_taxes(trades)
    assert [r[i][0] for i in (529, 530, 531, 532, 533)] == [1294, 1285, 1278, 2586, 2595]   # 與正式資料相同
    assert [r[i][1] for i in (529, 532)] == [1000, 0]


def test_partial_daytrade_in_one_sell():
    r = daytrade_sell_taxes([_t(1, "BUY", 500, 100), _t(2, "SELL", 1000, 100, True)])
    assert r[2] == (225.0, 500)   # 500 股 × 100 × 0.15% ＋ 500 股 × 100 × 0.3% = 75 + 150


def test_no_same_day_buy_means_full_tax():
    r = daytrade_sell_taxes([_t(1, "BUY", 1000, 100, d=date(2026, 9, 20)), _t(2, "SELL", 1000, 100, True)])
    assert r[2] == (300.0, 0)


def test_other_user_or_stock_does_not_count():
    r = daytrade_sell_taxes([_t(1, "BUY", 1000, 100, user="B"), _t(2, "BUY", 1000, 100, sid="2330"),
                             _t(3, "SELL", 1000, 100, True)])
    assert r[3][1] == 0


def test_etf_not_halved_and_unflagged_ignored():
    trades = [_t(1, "BUY", 1000, 50, sid="0050"), _t(2, "SELL", 1000, 50, True, sid="0050"), _t(3, "SELL", 1000, 50, sid="0050")]
    r = daytrade_sell_taxes(trades, is_etf=lambda s: s == "0050")
    assert r == {2: (50.0, 1000)}


def test_capacity_after_existing_daytrade_sells():
    from services.trade_fees import daytrade_capacity, fees_for_trade
    trades = [_t(1, "BUY", 3000, 830), _t(2, "SELL", 1000, 860, True), _t(3, "SELL", 1000, 860, True)]
    assert daytrade_capacity(trades, "A", "3189", D) == 1000
    assert daytrade_capacity(trades, "A", "3189", D, exclude_id=3) == 2000
    assert daytrade_capacity(trades, "A", "3189", date(2026, 9, 22)) == 0
    # 再賣 2,000 股勾當沖：只有 1,000 股減半
    _, tax = fees_for_trade("SELL", 860, 2000, is_daytrade=True, fee_rate=0.0004,
                            daytrade_qty=min(2000, daytrade_capacity(trades, "A", "3189", D)))
    assert tax == 1290 + 2580


def test_estimate_sell_tax_backward_compatible():
    from services.trade_fees import estimate_sell_tax
    assert estimate_sell_tax(863, 1000, tax_rate=0.003, is_daytrade=True) == 1294   # 沒給 daytrade_qty＝整筆減半
    assert estimate_sell_tax(863, 1000, tax_rate=0.003) == 2589
