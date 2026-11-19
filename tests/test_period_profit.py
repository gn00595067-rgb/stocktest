# -*- coding: utf-8 -*-
"""期間獲利表：期間切分、系統／手動來源、小計與累計、手動數字存取。"""
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.models import Base
from reports.period_profit import (build_period_blocks, manual_period_keys, merge_manual,
                                   BALANCE_KEY, LOSS_A_KEY, LOSS_A_LABEL, LOSS_C_LABEL)
from services.manual_figures import load_manual_figures, save_manual_figures

TODAY = date(2026, 10, 14)
OLD_START = date(2026, 9, 1)   # 版面／小計邏輯沿用原本 115/9 起的情境測


def _rows(blocks):
    return [r for b in blocks for r in b]


def test_manual_keys_stop_at_system_start():
    keys = [k for k, _ in manual_period_keys(TODAY, OLD_START)]
    assert keys[:2] == ["2023H2", "2024"]
    assert "2025-01" in keys and "2025-12" in keys
    assert "2026-08" in keys and "2026-09" not in keys   # 115/9 起系統
    assert keys[-2:] == [LOSS_A_KEY, BALANCE_KEY]


def test_blocks_layout_sources_and_totals():
    manual = {"2023H2": 100.0, "2024": 1000.0, "2025-01": 10.0, "2025-12": 20.0,
              "2026-08": 5.0, BALANCE_KEY: 999.0, "2026-09": 777.0}  # 系統月份的手動值要被忽略
    system = {"2026-09": 300.0, "2026-10": -50.0, "2026-08": 12345.0}  # 115/8 之前的系統值要被忽略
    blocks = build_period_blocks(manual, system, TODAY, OLD_START)
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
    assert by["115/1-12月 (b)"]["獲利"] == 255.0
    assert by["112/7/19～115/10月"]["獲利"] == 1385.0
    assert right[-3]["kind"] == "balance" and right[-3]["獲利"] == 999.0
    # 加減數(a) 沒填 → c＝0＋當年小計 b
    assert right[-2]["期間"] == LOSS_A_LABEL and right[-2]["獲利"] is None
    assert right[-1]["期間"] == LOSS_C_LABEL and right[-1]["獲利"] == 255.0


def test_loss_c_equals_a_plus_this_year_subtotal():
    blocks = build_period_blocks({LOSS_A_KEY: -100.0}, {"2026-09": 300.0}, TODAY, OLD_START)
    last = blocks[-1]
    assert last[-2]["獲利"] == -100.0
    assert last[-1]["獲利"] == 200.0


def test_adjustment_a_can_be_positive():
    blocks = build_period_blocks({LOSS_A_KEY: 50.0}, {"2026-09": 300.0}, TODAY, OLD_START)
    assert blocks[-1][-1]["獲利"] == 350.0


def test_default_system_start_is_115_11():
    """11/19 Peggy：9、10 月也改手 key，115/11 起才用系統。"""
    keys = [k for k, _ in manual_period_keys(date(2026, 11, 19))]
    assert "2026-09" in keys and "2026-10" in keys and "2026-11" not in keys
    blocks = build_period_blocks({"2026-09": 9.0, "2026-10": 10.0}, {"2026-09": 999.0, "2026-11": 5.0},
                                 date(2026, 11, 19))
    by = {r["期間"]: r for r in blocks[-1]}
    assert by["115/9月"] == {"期間": "115/9月", "獲利": 9.0, "來源": "手動", "kind": "item"}
    assert by["115/10月"]["來源"] == "手動" and by["115/11月"]["來源"] == "系統"
    assert by["115/1-12月 (b)"]["獲利"] == 24.0


def test_merge_manual_sums_traders():
    m = merge_manual([{"2024": 100.0, "2025-01": None, BALANCE_KEY: 5.0},
                      {"2024": -30.0, "2025-02": float("nan"), LOSS_A_KEY: 7.0}])
    assert m == {"2024": 70.0, "2025-01": None, "2025-02": None, BALANCE_KEY: 5.0, LOSS_A_KEY: 7.0}


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
