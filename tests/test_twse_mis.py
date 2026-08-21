# -*- coding: utf-8 -*-
"""TWSE MIS 即時報價解析：漲跌＝現價−昨收；無成交價時退回買/賣價或昨收。"""
from services.price_service import TwseMisProvider


def test_parse_normal():
    p = TwseMisProvider()
    q = p._parse({"c": "2330", "n": "台積電", "z": "2410.0", "y": "2375.0", "o": "2375.0",
                  "h": "2410.0", "l": "2365.0", "ex": "tse"})
    assert q["price"] == 2410.0
    assert q["change"] == 35.0            # 2410 - 2375（對昨收，不是對開盤）
    assert round(q["change_pct"], 2) == 1.47
    assert q["prev_close"] == 2375.0
    assert q["source"] == "twse_mis"


def test_parse_down():
    p = TwseMisProvider()
    q = p._parse({"c": "6488", "z": "941.0", "y": "993.0", "o": "991.0", "ex": "otc"})
    assert q["change"] == -52.0
    assert q["change_pct"] < 0


def test_parse_no_trade_uses_bid():
    p = TwseMisProvider()
    q = p._parse({"c": "1101", "z": "-", "y": "40.0",
                  "b": "39.9_39.8_39.7_", "a": "40.1_40.2_"})
    assert q["price"] == 39.9            # 用最佳買價
    assert round(q["change"], 2) == -0.1


def test_parse_no_trade_no_bid_uses_prev_close():
    p = TwseMisProvider()
    q = p._parse({"c": "1101", "z": "-", "y": "40.0", "b": "", "a": ""})
    assert q["price"] == 40.0            # 退回昨收
    assert q["change"] == 0.0


def test_parse_invalid_returns_none():
    p = TwseMisProvider()
    assert p._parse({"c": "9999", "z": "-", "y": None, "b": "", "a": ""}) is None
