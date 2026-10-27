# -*- coding: utf-8 -*-
"""庫存模擬最後狀態存檔（docs/specs/庫存模擬.md §6）。"""
from sqlalchemy import create_engine

from services.sim_state import inputs_from_json, inputs_to_json, load_sim_state, save_sim_state


def test_round_trip_per_owner_and_overwrite():
    eng = create_engine("sqlite://")
    assert load_sim_state(eng, "老闆") is None
    st1 = {"people": ["Peggy姐", "豪"], "who": "王小姐", "disc": 0.28, "mode": "不含手續費",
           "inputs": inputs_to_json({"2330": (10.0, 2420.0), "2303": (None, None)})}
    save_sim_state(eng, "老闆", st1)
    got = load_sim_state(eng, "老闆")
    assert got == st1
    assert got["inputs"] == {"2330": [10.0, 2420.0]}          # 兩個都空的不存
    assert load_sim_state(eng, "開發者") is None                # 每個帳號各一份

    st2 = dict(st1, people=["雲"], inputs={})
    save_sim_state(eng, "老闆", st2)                            # 整份覆蓋
    assert load_sim_state(eng, "老闆") == st2


def test_inputs_from_json_skips_bad_rows():
    assert inputs_from_json({"2330": [10, 2420], "2303": [None, 132], "bad": 5}) == {
        "2330": (10.0, 2420.0), "2303": (None, 132.0)}
    assert inputs_from_json(None) == {}
