# -*- coding: utf-8 -*-
"""持倉成本口徑：投資績效（position_cost）與庫存損益（build_portfolio_df）的均價須一致。"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reports.portfolio_report import build_portfolio_df
from services.position_cost import compute_position_and_cost_by_stock


class _T:
    def __init__(self, id, side, price, quantity, fee, trade_date):
        self.id = id
        self.stock_id = "3189"
        self.user = "Peggy"
        self.side = side
        self.price = price
        self.quantity = quantity
        self.fee = fee
        self.trade_date = trade_date


def _trades():
    # 景碩情境：早期高價買進被「沒有有效自定規則」的賣出沖掉，剩下後來的低價買進
    return [
        _T(1, "BUY", 916.0, 1000, 326, date(2026, 8, 28)),
        _T(2, "BUY", 812.0, 1000, 289, date(2026, 8, 31)),
        _T(3, "SELL", 900.0, 1000, 320, date(2026, 9, 1)),
        _T(4, "BUY", 957.0, 1000, 340, date(2026, 9, 30)),
    ]


def test_default_policy_matches_portfolio_page():
    # 規則指向已刪除的買進（孤兒規則）→ 自定沖銷配不到，應由先進先出補配
    rules = [(3, 999, 1000)]
    pos = compute_position_and_cost_by_stock(_trades(), custom_rules=rules)["3189"]
    df, _, _, _ = build_portfolio_df(
        _trades(), {}, date(2026, 1, 1), date(2026, 12, 31), "CUSTOM_PLUS_FIFO",
        lambda sid: None, custom_rules=rules,
    )
    assert pos["qty"] == 2000
    # FIFO 沖掉 916 那批，剩 812 + 957
    assert round(pos["cost"], 2) == 812 * 1000 + 289 + 957 * 1000 + 340
    assert round(pos["cost"] / pos["qty"], 2) == df.iloc[0]["均價"]
