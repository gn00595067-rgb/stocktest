# -*- coding: utf-8 -*-
"""build_portfolio_df 回歸測試：高價股含手續費不應誤判違反數學約束而崩潰。"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reports.portfolio_report import build_portfolio_df


class _T:
    def __init__(self, id, stock_id, user, side, price, quantity, fee, trade_date):
        self.id = id
        self.stock_id = stock_id
        self.user = user
        self.side = side
        self.price = price
        self.quantity = quantity
        self.fee = fee
        self.trade_date = trade_date


def _quote(sid):
    return {"price": 974.0}


def test_high_priced_stock_with_fee_does_not_crash():
    # 環球晶 6488 @ ~990，每股手續費約 1.4 元 > 舊版固定 1.0 元容忍值 → 舊版會 raise ValueError
    trades = [
        _T(1, "6488", "Peggy", "BUY", 989.0, 100, 141, date(2026, 8, 20)),
        _T(2, "6488", "Peggy", "BUY", 990.0, 200, 282, date(2026, 8, 21)),
    ]
    df, _, _, dbg = build_portfolio_df(
        trades, {}, date(2026, 8, 22), date(2026, 8, 25), "CUSTOM_ONLY", _quote, custom_rules=[]
    )
    assert not df.empty
    row = df.iloc[0]
    # 含費均價會略高於最高買價（990），這是合法的（手續費是成本一部分）
    assert row["均價"] > 990.0
    assert dbg["6488"]["position_qty"] == 300


def test_low_priced_stock_avg_within_bounds():
    # 低價股：含費均價仍接近買價，不觸發任何約束
    trades = [
        _T(1, "2408", "Peggy", "BUY", 487.0, 100, 69, date(2026, 8, 20)),
        _T(2, "2408", "Peggy", "BUY", 485.0, 100, 69, date(2026, 8, 21)),
    ]
    df, _, _, _ = build_portfolio_df(
        trades, {}, date(2026, 8, 22), date(2026, 8, 25), "CUSTOM_ONLY", _quote, custom_rules=[]
    )
    assert not df.empty
    assert df.iloc[0]["股數"] == 200
