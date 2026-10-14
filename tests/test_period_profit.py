# -*- coding: utf-8 -*-
"""期間獲利表：期間切分、系統／手動來源、小計與累計、手動數字存取。"""
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.models import Base
from reports.period_profit import build_period_blocks, manual_period_keys, BALANCE_KEY
from services.manual_figures import load_manual_figures, save_manual_figures

TODAY = date(2026, 10, 14)


def _rows(blocks):
    return [r for b in blocks for r in b]


def test_manual_keys_stop_at_system_start():
    keys = [k for k, _ in manual_period_keys(TODAY)]
    assert keys[:2] == ["2023H2", "2024"]
    assert "2025-01" in keys and "2025-12" in keys
    assert "2026-08" in keys and "2026-09" not in keys   # 115/9 起系統
    assert keys[-1] == BALANCE_KEY


def test_blocks_layout_sources_and_totals():
    manual = {"2023H2": 100.0, "2024": 1000.0, "2025-01": 10.0, "2025-12": 20.0,
              "2026-08": 5.0, BALANCE_KEY: 999.0, "2026-09": 777.0}  # 系統月份的手動值要被忽略
    system = {"2026-09": 300.0, "2026-10": -50.0, "2026-08": 12345.0}  # 115/8 之前的系統值要被忽略
    blocks = build_period_blocks(manual, system, TODAY)
    assert len(blocks) == 2                                   # 112～114 一塊、115 一塊
    left, right = blocks
    assert left[0]["期間"] == "112/7/19～112/12/31" and left[1]["期間"] == "113/1-12月"
    assert left[-2] == {"期間": "114/1-12月", "獲利": 30.0, "來源": "小計", "kind": "subtotal"}
    assert left[-1]["期間"] == "112/7/19～114/12月" and left[-1]["獲利"] == 1130.0

    by = {r["期間"]: r for r in right}
    assert by["115/8月"]["來源"] == "手動" and by["115/8月"]["獲利"] == 5.0
    assert by["115/9月"]["來源"] == "系統" and by["115/9月"]["獲利"] == 300.0
    assert by["115/10月"]["獲利"] == -50.0
    assert by["115/11月"]["來源"] == "未到" and by["115/11月"]["獲利"] is None
    assert by["115/1-12月"]["獲利"] == 255.0
    assert by["112/7/19～115/10月"]["獲利"] == 1385.0
    assert right[-1]["kind"] == "balance" and right[-1]["獲利"] == 999.0


def test_blank_manual_shown_as_none_and_counts_zero():
    blocks = build_period_blocks({}, {}, TODAY)
    rows = _rows(blocks)
    assert next(r for r in rows if r["期間"] == "114/3月")["獲利"] is None
    assert next(r for r in rows if r["kind"] == "total" and "115" in r["期間"])["獲利"] == 0.0


def test_future_year_gets_its_own_block():
    blocks = build_period_blocks({}, {"2027-01": 10.0}, date(2027, 1, 5))
    assert len(blocks) == 3
    assert blocks[2][0] == {"期間": "116/1月", "獲利": 10.0, "來源": "系統", "kind": "item"}


def test_manual_figures_round_trip():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    sess = sessionmaker(bind=eng)()
    assert save_manual_figures(sess, "Peggy姐", {"2024": 1000, "2025-01": None, BALANCE_KEY: 5}) == 2
    sess.commit()
    assert load_manual_figures(sess, "Peggy姐") == {"2024": 1000.0, BALANCE_KEY: 5.0}
    # 改值、清空（NaN）、不變
    assert save_manual_figures(sess, "Peggy姐", {"2024": 1200, BALANCE_KEY: float("nan"), "2025-01": None}) == 2
    sess.commit()
    assert load_manual_figures(sess, "Peggy姐") == {"2024": 1200.0, BALANCE_KEY: None}
    assert load_manual_figures(sess, "別人") == {}
