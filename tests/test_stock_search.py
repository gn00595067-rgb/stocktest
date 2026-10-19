# -*- coding: utf-8 -*-
"""新增股票的搜尋：全形／破折號／空白都要找得到；打錯代號給建議。"""
from services.stock_list_loader import search_stocks

L = [{"stock_id": "7841", "name": "經貿聯網"}, {"stock_id": "3665", "name": "貿聯-KY"},
     {"stock_id": "3665", "name": "貿聯-KY"}, {"stock_id": "2330", "name": "台積電"},
     {"stock_id": "6415", "name": "矽力*-KY"}]


def ids(kw):
    return [s["stock_id"] for s in search_stocks(L, kw)[0]]


def test_variants_of_dash_and_space_and_fullwidth():
    for kw in ["貿聯-KY", "貿聯－KY", "貿聯—KY", "貿聯KY", "貿聯 KY", "貿聯ＫＹ", "貿聯-ky", " 3665 ", "３６６５"]:
        assert ids(kw) == ["3665"], kw


def test_partial_name_and_dedupe():
    assert ids("貿聯") == ["3665", "7841"]      # 同代號只一筆
    assert ids("台積") == ["2330"]


def test_typo_code_gives_suggestion():
    hits, sug = search_stocks(L, "3655")
    assert hits == [] and sug[0]["stock_id"] == "3665"


def test_nothing():
    assert search_stocks(L, "") == ([], [])
    hits, sug = search_stocks(L, "完全不存在的公司名稱")
    assert hits == [] and sug == []


def test_code_suggestions_one_digit_off():
    big = L + [{"stock_id": "6955", "name": "邦睿生技"}, {"stock_id": "3656", "name": "某某"}, {"stock_id": "3566", "name": "對調"}]
    _, sug = search_stocks(big, "3655")
    got = [s["stock_id"] for s in sug]
    assert "3665" in got and "3656" in got and "3566" not in got and "6955" not in got
    _, sug = search_stocks(big, "6365")              # 相鄰對調 → 3665? 不是相鄰對調（6↔3 在頭兩碼）
    assert "3665" in [s["stock_id"] for s in sug]
