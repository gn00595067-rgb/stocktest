# -*- coding: utf-8 -*-
"""即時股價 API 抽象介面：FinMind 實作（含漲跌停價、股票列表）、Fugle 預留、15 秒快取、Mock"""
import os
import time
from abc import ABC, abstractmethod
from typing import List, Optional

_price_cache = {}
CACHE_SECONDS = 15

# 台股漲跌停幅度：一般股 10%，ETF 等 5%
LIMIT_PCT_NORMAL = 0.10
LIMIT_PCT_ETF = 0.05


def _round_price(p: float) -> float:
    """台股價格四捨五入到小數點兩位"""
    return round(p, 2)


class PriceProvider(ABC):
    @abstractmethod
    def get_quote(self, stock_id: str) -> Optional[dict]:
        """回傳 { name, price, change, change_pct, limit_up?, limit_down?, prev_close? } 或 None"""
        pass


class FinMindPriceProvider(PriceProvider):
    def __init__(self, token: str = None):
        self.token = token or os.environ.get("FINMIND_TOKEN")

    def _fetch_daily_price(self, stock_id: str, last_n_days: int = 5) -> List[dict]:
        """取得最近幾日收盤，用於現價與漲跌停計算"""
        if not self.token:
            return []
        try:
            import requests
            url = "https://api.finmindtrade.com/api/v4/data"
            params = {
                "dataset": "TaiwanStockPrice",
                "data_id": stock_id,
                "token": self.token,
            }
            r = requests.get(url, params=params, timeout=8)
            if r.status_code != 200:
                return []
            data = r.json()
            if not data.get("data"):
                return []
            return data["data"][-last_n_days:]
        except Exception:
            return []

    def _fetch_realtime_tick(self, stock_id: str) -> Optional[dict]:
        """FinMind 即時 tick（需 sponsor，Bearer token）"""
        if not self.token:
            return None
        try:
            import requests
            url = "https://api.finmindtrade.com/api/v4/taiwan_stock_tick_snapshot"
            headers = {"Authorization": f"Bearer {self.token}"}
            params = {"data_id": stock_id}
            r = requests.get(url, headers=headers, params=params, timeout=5)
            if r.status_code != 200:
                return None
            data = r.json()
            if not data.get("data"):
                return None
            tick = data["data"]
            if isinstance(tick, list):
                tick = tick[0] if tick else None
            return tick
        except Exception:
            return None

    def get_quote(self, stock_id: str) -> Optional[dict]:
        if not self.token:
            return None
        try:
            tick = self._fetch_realtime_tick(stock_id)
            if tick:
                close = float(tick.get("close", 0))
                open_p = float(tick.get("open", close))
                chg = close - open_p
                prev_close = close
                daily = self._fetch_daily_price(stock_id, 3)
                if len(daily) >= 2:
                    prev_close = float(daily[-2].get("close", prev_close))
                limit_up, limit_down = _compute_limit_prices(prev_close, is_etf=False)
                return {
                    "name": stock_id,
                    "price": close,
                    "change": chg,
                    "change_pct": (chg / open_p * 100) if open_p else 0,
                    "prev_close": prev_close,
                    "limit_up": limit_up,
                    "limit_down": limit_down,
                    "open": open_p,
                    "high": float(tick.get("high", close)),
                    "low": float(tick.get("low", close)),
                }
            daily = self._fetch_daily_price(stock_id, 5)
            if not daily:
                return None
            last = daily[-1]
            close = float(last.get("close", 0))
            open_p = float(last.get("open", close))
            chg = close - open_p
            prev_close = float(daily[-2].get("close", close)) if len(daily) >= 2 else open_p
            limit_up, limit_down = _compute_limit_prices(prev_close, is_etf=False)
            return {
                "name": stock_id,
                "price": close,
                "change": chg,
                "change_pct": (chg / open_p * 100) if open_p else 0,
                "prev_close": prev_close,
                "limit_up": limit_up,
                "limit_down": limit_down,
                "open": open_p,
                "high": float(last.get("max", close)),
                "low": float(last.get("min", close)),
            }
        except Exception:
            return None


def _compute_limit_prices(prev_close: float, is_etf: bool = False) -> tuple:
    """依昨收與是否 ETF 計算漲停價、跌停價（台股四捨五入到小數兩位）"""
    if prev_close <= 0:
        return None, None
    pct = LIMIT_PCT_ETF if is_etf else LIMIT_PCT_NORMAL
    limit_up = _round_price(prev_close * (1 + pct))
    limit_down = _round_price(prev_close * (1 - pct))
    return limit_up, limit_down


class FuglePriceProvider(PriceProvider):
    """預留：env FUGLE_API_KEY"""

    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.environ.get("FUGLE_API_KEY")

    def get_quote(self, stock_id: str) -> Optional[dict]:
        if not self.api_key:
            return None
        return None


class MockPriceProvider(PriceProvider):
    _mock_prices = {"2330": 580.0, "2317": 105.0, "3706": 52.0, "2454": 920.0, "2881": 68.0}

    def get_quote(self, stock_id: str) -> Optional[dict]:
        p = self._mock_prices.get(stock_id, 100.0)
        prev = p - 5.0  # 昨收，與現價有差距才合理
        limit_up, limit_down = _compute_limit_prices(prev, is_etf=False)
        chg = p - prev
        chg_pct = (chg / prev * 100) if prev else 0.0
        return {
            "name": stock_id,
            "price": p,
            "change": chg,
            "change_pct": chg_pct,
            "prev_close": prev,
            "limit_up": limit_up,
            "limit_down": limit_down,
        }


def get_price_service():
    token = os.environ.get("FINMIND_TOKEN")
    if token:
        return FinMindPriceProvider(token)
    return MockPriceProvider()


def get_quote_cached(stock_id: str) -> Optional[dict]:
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


def clear_quote_cache(stock_id: Optional[str] = None) -> None:
    """清除報價快取：指定 stock_id 只清該檔，None 則清全部。用於「更新即時現價」按鈕。"""
    global _price_cache
    if stock_id is None:
        _price_cache.clear()
    elif stock_id in _price_cache:
        del _price_cache[stock_id]


# ---------- 股票列表 API ----------

def fetch_stock_list_finmind(token: str = None) -> List[dict]:
    """
    從 FinMind 取得台股上市櫃列表（TaiwanStockInfo）。
    回傳 [ {"stock_id", "name", "industry_name", "market", "exchange", "is_etf"}, ... ]
    """
    token = token or os.environ.get("FINMIND_TOKEN")
    try:
        import requests
        url = "https://api.finmindtrade.com/api/v4/data"
        params = {"dataset": "TaiwanStockInfo", "token": token} if token else {"dataset": "TaiwanStockInfo"}
        r = requests.get(url, params=params, timeout=30)
        if r.status_code != 200:
            url3 = "https://api.finmindtrade.com/api/v3/data"
            r = requests.get(url3, params={"dataset": "TaiwanStockInfo"}, timeout=30)
        if r.status_code != 200:
            return []
        data = r.json()
        if not data.get("data"):
            return []
        rows = []
        for row in data["data"]:
            stock_id = str(row.get("stock_id", "")).strip()
            if not stock_id:
                continue
            industry = row.get("industry_category") or row.get("industry_name") or ""
            is_etf = "ETF" in industry.upper() or row.get("type") == "etf"
            name = row.get("stock_name") or row.get("name") or stock_id
            exchange = "TWSE" if (row.get("type") == "twse" or str(row.get("type", "")).lower() == "twse") else "TPEX"
            rows.append({
                "stock_id": stock_id,
                "name": name,
                "industry_name": industry,
                "market": "TW",
                "exchange": exchange,
                "is_etf": is_etf,
            })
        return rows
    except Exception:
        return []


def fetch_stock_list_cached(ttl_seconds: int = 86400) -> List[dict]:
    """帶快取的股票列表（預設 24 小時）"""
    cache_key = "_stock_list_cache"
    cache_ts_key = "_stock_list_cache_ts"
    now = time.time()
    if hasattr(fetch_stock_list_cached, cache_ts_key):
        if now - getattr(fetch_stock_list_cached, cache_ts_key) < ttl_seconds:
            return getattr(fetch_stock_list_cached, cache_key, [])
    lst = fetch_stock_list_finmind()
    setattr(fetch_stock_list_cached, cache_key, lst)
    setattr(fetch_stock_list_cached, cache_ts_key, now)
    return lst
