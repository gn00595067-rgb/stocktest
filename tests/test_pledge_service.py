# -*- coding: utf-8 -*-
"""股票質借（docs/specs/質借.md）。"""
from sqlalchemy import create_engine

from services.pledge_service import load_pledges, pledge_label, pledge_sell_warning, save_pledge


def test_save_load_overwrite_and_release():
    eng = create_engine("sqlite://")
    assert load_pledges(eng, "Peggy") == {}
    save_pledge(eng, "Peggy", "2330", 16000)
    save_pledge(eng, "豪", "2330", 3000)
    assert load_pledges(eng, "Peggy") == {"2330": 16000}       # 每個買賣人各自
    save_pledge(eng, "Peggy", "2330", 10000)                    # 覆蓋
    assert load_pledges(eng, "Peggy") == {"2330": 10000}
    save_pledge(eng, "Peggy", "2330", 0)                        # 0＝解除
    assert load_pledges(eng, "Peggy") == {}
    assert load_pledges(eng, "豪") == {"2330": 3000}


def test_sell_warning_only_when_over_usable():
    # Peggy 例：20 張押 16 張，可動用 4 張
    assert pledge_sell_warning(4000, 20000, 16000) is None
    w = pledge_sell_warning(5000, 20000, 16000)
    assert "16,000" in w and "4,000" in w
    assert pledge_sell_warning(5000, 20000, 0) is None


def test_label():
    assert pledge_label(20000, 16000) == ["質押 16,000", "可動用 4,000"]
    assert pledge_label(20000, 0) == []
    assert "超過持股" in pledge_label(10000, 16000)[1]
