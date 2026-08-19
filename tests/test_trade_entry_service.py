# -*- coding: utf-8 -*-
import math
from types import SimpleNamespace

import pandas as pd

from services.trade_entry_service import (
    safe_int_qty,
    estimate_match_row_net_pnl,
    profit_ranked_match_plan,
    nearest_avg_match_plan,
    sort_lots_by_strategy,
)


def _lots():
    return [
        {"trade_id": 276, "date": "2026-02-02", "price": 362.5, "remaining_qty": 1000},
        {"trade_id": 277, "date": "2026-02-02", "price": 360.5, "remaining_qty": 1000},
        {"trade_id": 278, "date": "2026-02-02", "price": 369.5, "remaining_qty": 1000},
        {"trade_id": 279, "date": "2026-02-02", "price": 360.0, "remaining_qty": 1000},
    ]


def test_safe_int_qty_none_and_nan():
    assert safe_int_qty(None) == 0
    assert safe_int_qty(float("nan")) == 0
    assert safe_int_qty(pd.NA) == 0
    assert safe_int_qty("") == 0
    assert safe_int_qty("None") == 0


def test_safe_int_qty_valid():
    assert safe_int_qty(800) == 800
    assert safe_int_qty(800.0) == 800
    assert safe_int_qty("1000") == 1000


def test_estimate_match_row_net_pnl():
    buy = SimpleNamespace(id=1, quantity=1000, price=88.0, fee=126.0)
    gross, net = estimate_match_row_net_pnl(
        sell_price=90.0,
        buy_price=88.0,
        matched_qty=500,
        buy_trade=buy,
        sell_fee_est=115.0,
        sell_tax_est=243.0,
        sell_qty_total=1000,
    )
    assert gross == 1000.0  # (90-88)*500
    assert net < gross
    assert isinstance(net, float)


def test_profit_ranked_most_profit_picks_lowest_buy_first():
    # 賺多優先：買價最低（獲利最多）先配 → 279(360.0), 277(360.5), 276(362.5 部分)
    plan = profit_ranked_match_plan(2500, _lots(), most_profit=True)
    assert plan == [(279, 1000), (277, 1000), (276, 500)]


def test_profit_ranked_least_profit_picks_highest_buy_first():
    # 賺少優先：買價最高（獲利最少）先配 → 278(369.5), 276(362.5), 277(360.5 部分)
    plan = profit_ranked_match_plan(2500, _lots(), most_profit=False)
    assert plan == [(278, 1000), (276, 1000), (277, 500)]


def test_nearest_avg_picks_closest_to_weighted_average():
    # 加權均價 = 363.125，最接近者為 276(362.5)
    plan = nearest_avg_match_plan(1000, _lots())
    assert plan == [(276, 1000)]


def test_match_plans_never_exceed_sell_qty():
    for plan in (
        profit_ranked_match_plan(1500, _lots(), most_profit=True),
        profit_ranked_match_plan(1500, _lots(), most_profit=False),
        nearest_avg_match_plan(1500, _lots()),
    ):
        assert sum(q for _, q in plan) == 1500


def test_match_plans_empty_lots():
    assert profit_ranked_match_plan(1000, [], most_profit=True) == []
    assert nearest_avg_match_plan(1000, []) == []


def test_sort_lots_by_strategy_orders():
    ids = lambda lots: [l["trade_id"] for l in lots]
    # 賺多優先：買價低→高
    assert ids(sort_lots_by_strategy(_lots(), "profit_max")) == [279, 277, 276, 278]
    # 賺少優先：買價高→低
    assert ids(sort_lots_by_strategy(_lots(), "profit_min")) == [278, 276, 277, 279]
    # 接近均價(363.125)：276(362.5),277(360.5),279(360.0),278(369.5)
    assert ids(sort_lots_by_strategy(_lots(), "nearest_avg")) == [276, 277, 279, 278]
    # 未知策略維持原順序
    assert ids(sort_lots_by_strategy(_lots(), None)) == [276, 277, 278, 279]
