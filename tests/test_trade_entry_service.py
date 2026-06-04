# -*- coding: utf-8 -*-
import math

import pandas as pd

from services.trade_entry_service import safe_int_qty


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
