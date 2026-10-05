# -*- coding: utf-8 -*-
"""報價快取：查不到的不要每次重查；FinMind 補價有時間上限（正式站曾因逐檔補價卡近一分鐘）。"""
import time

import services.price_service as ps


class _SlowSvc:
    def __init__(self, delay, price=None):
        self.delay, self.price, self.calls = delay, price, 0

    def get_quote(self, sid):
        self.calls += 1
        time.sleep(self.delay)
        return {"price": self.price} if self.price else None


def _setup(monkeypatch, mis_result, svc):
    ps._price_cache.clear()
    monkeypatch.setattr(ps._mis_provider, "get_quotes", lambda ids, exchanges=None: dict(mis_result))
    monkeypatch.setattr(ps, "get_price_service", lambda: svc)


def test_failed_quotes_not_retried_within_neg_cache(monkeypatch):
    svc = _SlowSvc(0, price=None)                    # FinMind 也查不到
    _setup(monkeypatch, {}, svc)
    assert ps.get_quotes_cached(["9999", "8888"]) == {}
    assert svc.calls == 2
    assert ps.get_quotes_cached(["9999", "8888"]) == {}
    assert svc.calls == 2                            # 60 秒內不再重查


def test_fallback_is_parallel_and_bounded(monkeypatch):
    monkeypatch.setattr(ps, "FALLBACK_BUDGET_SECONDS", 0.5)
    svc = _SlowSvc(2.0, price=10)                    # 每檔要 2 秒
    _setup(monkeypatch, {}, svc)
    t0 = time.monotonic()
    ps.get_quotes_cached([f"{i:04d}" for i in range(20)])
    assert time.monotonic() - t0 < 1.5               # 20 檔逐檔要 40 秒；現在最多等預算時間


def test_mis_hits_skip_fallback(monkeypatch):
    svc = _SlowSvc(0, price=1)
    _setup(monkeypatch, {"2330": {"price": 1000}}, svc)
    out = ps.get_quotes_cached(["2330"])
    assert out["2330"]["price"] == 1000 and svc.calls == 0


def test_good_quotes_cached(monkeypatch):
    calls = {"n": 0}
    ps._price_cache.clear()

    def mis(ids, exchanges=None):
        calls["n"] += 1
        return {"2330": {"price": 1000}}
    monkeypatch.setattr(ps._mis_provider, "get_quotes", mis)
    ps.get_quotes_cached(["2330"]); ps.get_quotes_cached(["2330"])
    assert calls["n"] == 1


def test_market_hours_ttl():
    from datetime import datetime, timezone, timedelta
    tz = timezone(timedelta(hours=8))
    ts = lambda *a: datetime(*a, tzinfo=tz).timestamp()
    assert ps._quote_ttl(ts(2026, 10, 5, 10, 0)) == ps.CACHE_SECONDS            # 週一盤中
    assert ps._quote_ttl(ts(2026, 10, 5, 18, 0)) == ps.AFTER_HOURS_CACHE_SECONDS  # 週一收盤後
    assert ps._quote_ttl(ts(2026, 10, 4, 10, 0)) == ps.AFTER_HOURS_CACHE_SECONDS  # 週日


def test_after_hours_quotes_not_refetched(monkeypatch):
    calls = {"n": 0}
    ps._price_cache.clear()
    monkeypatch.setattr(ps, "_tw_market_open", lambda now=None: False)

    def mis(ids, exchanges=None):
        calls["n"] += 1
        return {"2330": {"price": 1000}}
    monkeypatch.setattr(ps._mis_provider, "get_quotes", mis)
    ps.get_quotes_cached(["2330"])
    ps._price_cache["2330"] = (ps._price_cache["2330"][0], ps._price_cache["2330"][1] - 120)  # 假裝 2 分鐘前抓的
    ps.get_quotes_cached(["2330"])
    assert calls["n"] == 1                       # 收盤後 2 分鐘內不重抓
