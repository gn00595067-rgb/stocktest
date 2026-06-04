# -*- coding: utf-8 -*-
import math
from types import SimpleNamespace

import pandas as pd

from services.trade_entry_service import safe_int_qty, estimate_match_row_net_pnl


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
