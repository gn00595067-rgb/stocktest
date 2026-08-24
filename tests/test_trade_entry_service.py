# -*- coding: utf-8 -*-
import math
from types import SimpleNamespace

import pandas as pd

from datetime import date, timedelta

from services.trade_entry_service import (
    safe_int_qty,
    estimate_match_row_net_pnl,
    profit_ranked_match_plan,
    nearest_avg_match_plan,
    sort_lots_by_strategy,
    filter_lots_by_time,
    filter_and_sort_lots,
    combined_match_plan,
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
    # 賠多：買價高→低（虧損最多先）＝與 profit_min 同序
    assert ids(sort_lots_by_strategy(_lots(), "loss_max")) == [278, 276, 277, 279]
    # 賠少：買價低→高（虧損最少先）＝與 profit_max 同序
    assert ids(sort_lots_by_strategy(_lots(), "loss_min")) == [279, 277, 276, 278]
    # 接近均價(363.125)：276(362.5),277(360.5),279(360.0),278(369.5)
    assert ids(sort_lots_by_strategy(_lots(), "nearest_avg")) == [276, 277, 279, 278]
    # 未知策略維持原順序
    assert ids(sort_lots_by_strategy(_lots(), None)) == [276, 277, 278, 279]


def _mixed_lots():
    """買價分布在賣價 363 兩側：賺=價<363、賠=價>363。"""
    return [
        {"trade_id": 1, "date": "2026-02-01", "price": 350.0, "remaining_qty": 1000},  # 賺
        {"trade_id": 2, "date": "2026-02-02", "price": 360.0, "remaining_qty": 1000},  # 賺
        {"trade_id": 3, "date": "2026-02-03", "price": 370.0, "remaining_qty": 1000},  # 賠
        {"trade_id": 4, "date": "2026-02-04", "price": 380.0, "remaining_qty": 1000},  # 賠
    ]


def test_filter_and_sort_profit_only_keeps_winners():
    ids = lambda lots: [l["trade_id"] for l in lots]
    # 賺多：只留買價<363（1,2），買價低→高 → [1,2]
    assert ids(filter_and_sort_lots(_mixed_lots(), "profit_max", sell_price=363.0)) == [1, 2]
    # 賺少：只留賺，買價高→低 → [2,1]
    assert ids(filter_and_sort_lots(_mixed_lots(), "profit_min", sell_price=363.0)) == [2, 1]


def test_filter_and_sort_loss_only_keeps_losers():
    ids = lambda lots: [l["trade_id"] for l in lots]
    # 賠多：只留買價>363（3,4），虧損最多（買價高）先 → [4,3]
    assert ids(filter_and_sort_lots(_mixed_lots(), "loss_max", sell_price=363.0)) == [4, 3]
    # 賠少：只留賠，虧損最少（買價低）先 → [3,4]
    assert ids(filter_and_sort_lots(_mixed_lots(), "loss_min", sell_price=363.0)) == [3, 4]


def test_filter_and_sort_no_sell_price_skips_profit_loss_filter():
    ids = lambda lots: [l["trade_id"] for l in lots]
    # 未給賣價時不做賺賠篩選，只排序（全部保留）
    assert set(ids(filter_and_sort_lots(_mixed_lots(), "profit_max"))) == {1, 2, 3, 4}


def test_filter_lots_by_time_all_and_recent():
    lots = [
        {"trade_id": 1, "date": (date.today() - timedelta(days=10)).isoformat(), "price": 10, "remaining_qty": 1000},
        {"trade_id": 2, "date": (date.today() - timedelta(days=2)).isoformat(), "price": 10, "remaining_qty": 1000},
        {"trade_id": 3, "date": date.today().isoformat(), "price": 10, "remaining_qty": 1000},
    ]
    ids = lambda ls: sorted(l["trade_id"] for l in ls)
    assert ids(filter_lots_by_time(lots, "all")) == [1, 2, 3]
    assert ids(filter_lots_by_time(lots, "3d")) == [2, 3]
    assert ids(filter_lots_by_time(lots, "5d")) == [2, 3]


def test_combined_match_plan_time_and_profit_axes_intersect():
    lots = [
        {"trade_id": 1, "date": (date.today() - timedelta(days=10)).isoformat(), "price": 350.0, "remaining_qty": 1000},  # 賺但太舊
        {"trade_id": 2, "date": (date.today() - timedelta(days=1)).isoformat(), "price": 355.0, "remaining_qty": 1000},  # 賺+近
        {"trade_id": 3, "date": date.today().isoformat(), "price": 380.0, "remaining_qty": 1000},  # 賠+近
    ]
    # 近3天 × 賺多：時間留 2,3；賺只留 2 → 全配 2
    plan = combined_match_plan(1000, lots, time_key="3d", sort_key="profit_max", sell_price=363.0)
    assert plan == [(2, 1000)]
    # 近3天 × 賠多：時間留 2,3；賠只留 3 → 全配 3
    plan2 = combined_match_plan(1000, lots, time_key="3d", sort_key="loss_max", sell_price=363.0)
    assert plan2 == [(3, 1000)]
    # 符合者不足時，計畫合計 < 賣出股數（不報錯）
    plan3 = combined_match_plan(5000, lots, time_key="3d", sort_key="profit_max", sell_price=363.0)
    assert sum(q for _, q in plan3) == 1000
