# -*- coding: utf-8 -*-
"""台灣 Yahoo 即時報價：解析、與證交所擇一、盤中拿不到今天價的提示（2026-11-10 Peggy 開盤後股價沒更新）。"""
from datetime import datetime, timezone, timedelta

import services.price_service as ps
from services.price_service import YahooTwProvider, pick_live_quote, stale_quote_ids


def _ts(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=timezone(timedelta(hours=8))).timestamp()


def test_parse_yahoo_row():
    q = YahooTwProvider()._parse({
        "symbol": "2303.TW", "systexId": "2303", "price": "102", "regularMarketPreviousClose": "96.7",
        "regularMarketTime": "2026-11-10T01:52:47Z", "limitUpPrice": "106", "limitDownPrice": "87.1",
        "regularMarketOpen": "99.4", "regularMarketDayHigh": "102.5", "regularMarketDayLow": "98.7"})
    assert q["price"] == 102 and q["prev_close"] == 96.7
    assert round(q["change"], 2) == 5.3
    assert q["data_date"] == "20261110" and q["time"] == "09:52:47"
    assert q["limit_up"] == 106 and q["source"] == "yahoo_tw"


def test_parse_yahoo_no_price():
    assert YahooTwProvider()._parse({"systexId": "1234", "price": "-"}) is None


def test_symbols_by_exchange():
    assert YahooTwProvider._symbols("3105", "TPEX") == ["3105.TWO"]
    assert YahooTwProvider._symbols("2330", "TWSE") == ["2330.TW"]
    assert YahooTwProvider._symbols("0050", None) == ["0050.TW", "0050.TWO"]


def test_pick_prefers_today_yahoo_then_today_mis():
    y_today = {"price": 102, "data_date": "20261110"}
    m_today = {"price": 101.5, "data_date": "20261110"}
    y_old = {"price": 96.7, "data_date": "20261109"}
    assert pick_live_quote(m_today, y_today, "20261110") is y_today
    assert pick_live_quote(m_today, y_old, "20261110") is m_today
    assert pick_live_quote(None, None, "20261110") is None
    assert pick_live_quote(None, y_old, "20261110") is y_old


def test_live_quotes_yahoo_covers_mis_failure(monkeypatch):
    monkeypatch.setattr(ps._mis_provider, "get_quotes", lambda ids, exchanges=None: {})
    monkeypatch.setattr(ps._yahoo_provider, "get_quotes",
                        lambda ids, exchanges=None: {"2330": {"price": 2260, "data_date": ps._today_tw()}})
    assert ps._live_quotes(["2330"])["2330"]["price"] == 2260


def test_stale_ids_only_during_market():
    quotes = {"2330": {"data_date": "20261110"}, "2303": {"data_date": "2026-11-09"}}
    ids = ["2330", "2303", "1101"]
    assert stale_quote_ids(ids, quotes, now=_ts(2026, 11, 10, 9, 20)) == ["2303", "1101"]
    assert stale_quote_ids(ids, quotes, now=_ts(2026, 11, 10, 15, 0)) == []   # 收盤後不提示
    assert stale_quote_ids(ids, quotes, now=_ts(2026, 11, 14, 10, 0)) == []   # 週六
