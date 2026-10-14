# -*- coding: utf-8 -*-
"""共用篩選列的快速區間計算。"""
from datetime import date

import pytest

from services.report_ui import quick_range, active_quick_range, ALL_START, QUICK_RANGES

TODAY = date(2026, 10, 14)  # 週三


def test_quick_ranges():
    assert quick_range("當日", TODAY) == (TODAY, TODAY)
    assert quick_range("近3天", TODAY) == (date(2026, 10, 11), TODAY)
    assert quick_range("當週", TODAY) == (date(2026, 10, 12), TODAY)   # 本週一
    assert quick_range("當月", TODAY) == (date(2026, 10, 1), TODAY)
    assert quick_range("當年", TODAY) == (date(2026, 1, 1), TODAY)
    assert quick_range("近1年", TODAY) == (date(2025, 10, 14), TODAY)
    assert quick_range("全部", TODAY) == (ALL_START, TODAY)


def test_quick_range_unknown():
    with pytest.raises(ValueError):
        quick_range("近半年", TODAY)


def test_active_round_trip():
    for lab in QUICK_RANGES:
        s, e = quick_range(lab, TODAY)
        assert active_quick_range(s, e, TODAY) == lab


def test_active_monday_prefers_first_button():
    monday = date(2026, 10, 12)
    assert active_quick_range(monday, monday, monday) == "當日"


def test_active_custom_dates_none():
    assert active_quick_range(date(2026, 5, 5), TODAY, TODAY) is None
    assert active_quick_range(date(2026, 1, 1), date(2026, 5, 1), TODAY) is None
