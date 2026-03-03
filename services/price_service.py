# -*- coding: utf-8 -*-
"""即時股價 API 抽象介面：FinMind 實作、Fugle 預留、15 秒快取、Mock"""
import os
import time
from abc import ABC, abstractmethod

_price_cache = {}
CACHE_SECONDS = 15


class PriceProvider(ABC):
    @abstractmethod
    def get_quote(self, stock_id: str) -> dict:
        """回傳 { name, price, change, change_pct } 或 None"""
        pass


class FinMindPriceProvider(PriceProvider):
    def __init__(self, token: str = None):
        self.token = token or os.environ.get("FINMIND_TOKEN")

    def get_quote(self, stock_id: str) -> dict:
        if not self.token:
            return None
        try:
            import requests
            url = "https://api.finmindtrade.com/api/v4/data"
            params = {"dataset": "TaiwanStockPrice", "data_id": stock_id, "token": self.token}
            r = requests.get(url, params=params, timeout=5)
            if r.status_code != 200:
                return None
            data = r.json()
            if not data.get("data"):
                return None
            last = data["data"][-1]
            close = float(last.get("close", 0))
            open_p = float(last.get("open", close))
            chg = close - open_p
            return {
                "name": stock_id,
                "price": close,
                "change": chg,
                "change_pct": (chg / open_p * 100) if open_p else 0,
            }
        except Exception:
            return None


class FuglePriceProvider(PriceProvider):
    """預留：env FUGLE_API_KEY"""
    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.environ.get("FUGLE_API_KEY")

    def get_quote(self, stock_id: str) -> dict:
        if not self.api_key:
            return None
        return None


class MockPriceProvider(PriceProvider):
    _mock_prices = {"2330": 580.0, "2317": 105.0, "3706": 52.0, "2454": 920.0, "2881": 68.0}

    def get_quote(self, stock_id: str) -> dict:
        p = self._mock_prices.get(stock_id, 100.0)
        return {"name": stock_id, "price": p, "change": 0.0, "change_pct": 0.0}


def get_price_service():
    token = os.environ.get("FINMIND_TOKEN")
    if token:
        return FinMindPriceProvider(token)
    return MockPriceProvider()


def get_quote_cached(stock_id: str) -> dict:
    now = time.time()
    if stock_id in _price_cache:
        data, ts = _price_cache[stock_id]
        if now - ts < CACHE_SECONDS:
            return data
    provider = get_price_service()
    data = provider.get_quote(stock_id)
    if data:
        _price_cache[stock_id] = (data, now)
    return data
