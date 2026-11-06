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


def test_parse_limit_down_locked_skips_market_order_zero():
    """跌停鎖死：沒有成交價、沒有買價，賣價第一格是 0.0000（市價單）→ 要拿到跌停價，不是昨收（11/06 奇鋐）。"""
    p = TwseMisProvider()
    q = p._parse({"c": "3017", "z": "-", "pz": "-", "y": "2565.0000", "b": "-",
                  "a": "0.0000_2310.0000_2315.0000_2320.0000_2325.0000_"})
    assert q["price"] == 2310 and q["change"] == -255


def test_official_close_parse_and_apply():
    """收盤後改用官方收盤價（秘書 11/06 比對：富世達收跌停 1,690，系統顯示 1,695）。"""
    from datetime import date
    from services.price_service import apply_official_close, parse_tpex_close_json, parse_twse_day_all_csv
    d = date(2026, 11, 6)
    csv_text = ("日期,證券代號,證券名稱,成交股數,成交金額,開盤價,最高價,最低價,收盤價,漲跌價差,成交筆數\n"
                '"1151106","6805","富世達","1","1","1700","1740","1690","1690.00","-185.0000","1"\n'
                '"1151105","2303","聯電","1","1","1","1","1","98.40","0","1"\n'      # 不是今天：不要
                '"1151106","9999","停牌","0","0","--","--","--","--","0","0"\n')
    assert parse_twse_day_all_csv(csv_text, d) == {"6805": 1690.0}
    assert parse_tpex_close_json([{"Date": "1151106", "SecuritiesCompanyCode": "3105", "Close": "406.00"},
                                  {"Date": "1151105", "SecuritiesCompanyCode": "8358", "Close": "1"}], d) == {"3105": 406.0}
    q = {"price": 1695.0, "change": -180.0, "change_pct": -9.6, "prev_close": 1875.0, "data_date": "20261106"}
    q2 = apply_official_close("6805", q, {"6805": 1690.0}, day=d)
    assert q2["price"] == 1690 and q2["change"] == -185 and q["price"] == 1695.0   # 原本快取的不改
    # 報價不是今天的（停牌等）不套
    assert apply_official_close("6805", dict(q, data_date="20261105"), {"6805": 1690.0}, day=d)["price"] == 1695.0
