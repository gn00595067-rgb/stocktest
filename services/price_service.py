# -*- coding: utf-8 -*-
"""即時股價 API 抽象介面：FinMind 實作（含漲跌停價、股票列表）、Fugle 預留、15 秒快取、Mock"""
import os
import time
from abc import ABC, abstractmethod
from datetime import date
from typing import List, Optional

_price_cache = {}
CACHE_SECONDS = 60  # 報價快取秒數（每檔股票每 60 秒最多打 1 次 API，避免持倉/儀表板多檔時爆量）

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
        """取得最近幾日收盤。先試 v4，若 402（需付費）則改試 v3 免費 API。"""
        if not self.token:
            return []
        try:
            from datetime import timedelta
            import requests
            today = date.today().isoformat()
            start = (date.today() - timedelta(days=30)).isoformat()
            # 先試 v4
            url4 = "https://api.finmindtrade.com/api/v4/data"
            params4 = {"dataset": "TaiwanStockPrice", "data_id": stock_id, "start_date": start, "end_date": today}
            r = requests.get(url4, params=params4, headers={"Authorization": f"Bearer {self.token}"}, timeout=8)
            if r.status_code == 200:
                data = r.json()
                if data.get("data"):
                    rows = data["data"]
                    return rows[-last_n_days:] if len(rows) >= last_n_days else rows
            # v4 回 402/403 等時改試 v3（免費層）
            if r.status_code in (402, 403, 400):
                url3 = "https://api.finmindtrade.com/api/v3/data"
                params3 = {"dataset": "TaiwanStockPrice", "stock_id": stock_id, "date": start, "end_date": today}
                if self.token:
                    params3["token"] = self.token
                r3 = requests.get(url3, params=params3, timeout=8)
                if r3.status_code == 200:
                    data = r3.json()
                    if data.get("data"):
                        rows = data["data"]
                        return rows[-last_n_days:] if len(rows) >= last_n_days else rows
            return []
        except Exception:
            return []

    def _fetch_daily_price_debug(self, stock_id: str) -> tuple:
        """同 _fetch_daily_price，但回傳 (rows, error_message)。"""
        if not self.token:
            return [], "FINMIND_TOKEN 未設定"
        try:
            from datetime import timedelta
            import requests
            today = date.today().isoformat()
            start = (date.today() - timedelta(days=30)).isoformat()
            url4 = "https://api.finmindtrade.com/api/v4/data"
            params4 = {"dataset": "TaiwanStockPrice", "data_id": stock_id, "start_date": start, "end_date": today}
            r = requests.get(url4, params=params4, headers={"Authorization": f"Bearer {self.token}"}, timeout=8)
            if r.status_code == 200:
                data = r.json()
                if data.get("data"):
                    return data["data"], None
                return [], "API 回傳無資料"
            if r.status_code in (402, 403, 400):
                url3 = "https://api.finmindtrade.com/api/v3/data"
                params3 = {"dataset": "TaiwanStockPrice", "stock_id": stock_id, "date": start, "end_date": today, "token": self.token}
                r3 = requests.get(url3, params=params3, timeout=8)
                if r3.status_code == 200:
                    data = r3.json()
                    if data.get("data"):
                        return data["data"], None
                    return [], "API 回傳無資料"
                return [], f"v3 狀態碼 {r3.status_code}"
            return [], f"API 狀態碼 {r.status_code}"
        except Exception as e:
            return [], str(e)

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
                    "source": "finmind",
                    "data_date": str(tick.get("date", ""))[:10],
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
                "source": "finmind",
                "data_date": str(last.get("date", ""))[:10],
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
            "source": "mock",
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


def fetch_daily_prices(stock_id: str, start_date: date, end_date: date) -> List[dict]:
    """
    取得區間內每日收盤價，供走勢圖使用。
    回傳 [ {"date": "YYYY-MM-DD", "close": float}, ... ]，依日期排序。
    """
    token = os.environ.get("FINMIND_TOKEN")
    if not token:
        return []
    try:
        import requests
        start_s = start_date.isoformat()
        end_s = end_date.isoformat()
        url4 = "https://api.finmindtrade.com/api/v4/data"
        params4 = {"dataset": "TaiwanStockPrice", "data_id": stock_id, "start_date": start_s, "end_date": end_s}
        r = requests.get(url4, params=params4, headers={"Authorization": f"Bearer {token}"}, timeout=15)
        if r.status_code == 200:
            data = r.json()
            if data.get("data"):
                out = []
                for row in data["data"]:
                    d = str(row.get("date", ""))[:10]
                    close = float(row.get("close", 0))
                    if d:
                        out.append({"date": d, "close": close})
                out.sort(key=lambda x: x["date"])
                return out
        if r.status_code in (402, 403, 400):
            url3 = "https://api.finmindtrade.com/api/v3/data"
            params3 = {"dataset": "TaiwanStockPrice", "stock_id": stock_id, "date": start_s, "end_date": end_s, "token": token}
            r3 = requests.get(url3, params=params3, timeout=15)
            if r3.status_code == 200:
                data = r3.json()
                if data.get("data"):
                    out = []
                    for row in data["data"]:
                        d = str(row.get("date", ""))[:10]
                        close = float(row.get("close", 0))
                        if d:
                            out.append({"date": d, "close": close})
                    out.sort(key=lambda x: x["date"])
                    return out
        return []
    except Exception:
        return []


_debug_cache = {}
DEBUG_CACHE_SECONDS = 60


def get_finmind_debug(stock_id: str = "2330") -> dict:
    """
    除錯用：回傳 Token 是否讀到、以及呼叫 FinMind API 的結果。
    若 v4 回 402，會解析回傳訊息並查詢 API 使用量（user_info），協助判斷是「請求次數達上限」或「方案權限」。
    結果快取 60 秒，避免同一頁面每次 rerun 都再打 3 次 API。
    """
    now = time.time()
    if stock_id in _debug_cache:
        data, ts = _debug_cache[stock_id]
        if now - ts < DEBUG_CACHE_SECONDS:
            return data
    token = os.environ.get("FINMIND_TOKEN")
    if not token or not str(token).strip():
        return {"token_set": False, "error": "FINMIND_TOKEN 未讀到", "message": "請確認主檔/設定顯示「已設定」，或 Cloud 的 Secrets 已存檔並重新部署。"}
    try:
        import requests
        from datetime import timedelta
        today = date.today().isoformat()
        start = (date.today() - timedelta(days=30)).isoformat()
        # 先試 v4
        url4 = "https://api.finmindtrade.com/api/v4/data"
        params4 = {"dataset": "TaiwanStockPrice", "data_id": stock_id, "start_date": start, "end_date": today}
        r = requests.get(url4, params=params4, headers={"Authorization": f"Bearer {token}"}, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if data.get("data"):
                return {"token_set": True, "error": None, "message": "FinMind 連線正常（v4）"}
            return {"token_set": True, "error": "API 回傳無資料", "message": f"可能股票 {stock_id} 無該日資料。"}
        if r.status_code in (402, 403, 400):
            # 解析 v4 回傳內容（402 常見：Requests reach the upper limit 或方案權限不足）
            v4_msg = ""
            try:
                body = r.json()
                v4_msg = body.get("msg") or body.get("message") or str(body)
            except Exception:
                v4_msg = r.text or f"狀態碼 {r.status_code}"
            # 402 時查詢 API 使用量，協助判斷是「次數上限」還是「方案權限」
            usage_msg = ""
            used = None
            limit = None
            if r.status_code == 402:
                try:
                    ui = requests.get(
                        "https://api.web.finmindtrade.com/v2/user_info",
                        headers={"Authorization": f"Bearer {token}"},
                        timeout=8,
                    )
                    if ui.status_code == 200:
                        j = ui.json()
                        used = j.get("user_count")
                        limit = j.get("api_request_limit")
                        if used is not None and limit is not None:
                            usage_msg = f"目前使用次數：{used} / 上限 {limit}（每小時）。"
                        else:
                            usage_msg = "user_info 回傳：" + str(j)
                    else:
                        usage_msg = f"user_info 狀態碼 {ui.status_code}，無法取得使用量。"
                except Exception as e:
                    usage_msg = f"查詢使用量失敗：{e}"
            # 依 API 回傳與使用量判斷可能情況
            situation = ""
            v4_lower = (v4_msg or "").lower()
            if "upper limit" in v4_lower or "reach the upper limit" in v4_lower or "請求" in (v4_msg or "") and "上限" in (v4_msg or ""):
                if used is not None and limit is not None:
                    if used >= limit:
                        situation = "**判斷：每小時請求次數已達上限** — 請等下一小時後再試，或減少開關「庫存損益」「投資績效」等頁面。"
                    elif limit >= 500 and used < 50:
                        situation = "**判斷：可能是「每分鐘」或短時間內請求次數上限** — 顯示的「使用次數」為 user_info 回傳值，若遠低於每小時上限仍出現 402，多半是短區間限流。建議 1～2 分鐘後再試。"
                    else:
                        situation = "**判斷：請求次數接近或觸及上限** — 建議稍後再試或減少報價相關操作。"
                else:
                    situation = "**判斷：API 回傳為請求次數達上限** — 建議幾分鐘後再試；若持續發生，請至 https://finmindtrade.com/ 查看用量與方案。"
            elif "付費" in (v4_msg or "") or "sponsor" in v4_lower or "plan" in v4_lower or "subscription" in v4_lower:
                situation = "**判斷：該 API 可能需要付費方案** — 請至 https://finmindtrade.com/ 查看方案說明。"
            elif r.status_code == 402:
                situation = "**判斷：無法從回傳內容判斷具體原因** — 請見下方詳細訊息，或至 https://finmindtrade.com/ 查看用量與方案。"
            url3 = "https://api.finmindtrade.com/api/v3/data"
            params3 = {"dataset": "TaiwanStockPrice", "stock_id": stock_id, "date": start, "end_date": today, "token": token}
            r3 = requests.get(url3, params=params3, timeout=10)
            if r3.status_code == 200:
                data = r3.json()
                if data.get("data"):
                    return {"token_set": True, "error": None, "message": "FinMind 連線正常（v3 免費層，v4 為 402 已自動改用 v3）"}
                return {"token_set": True, "error": "v3 回傳無資料", "message": f"可能股票 {stock_id} 無該日資料。"}
            v3_msg = ""
            try:
                v3_msg = r3.json().get("msg") or r3.json().get("message") or ""
            except Exception:
                pass
            full_msg = f"v4 回傳：{v4_msg}"
            if usage_msg:
                full_msg += " " + usage_msg
            if v3_msg:
                full_msg += f" v3：{v3_msg}"
            result = {
                "token_set": True,
                "error": f"v4={r.status_code}，v3={r3.status_code}",
                "message": full_msg or "402 表示請求次數達上限或該 API 需付費方案，請至 https://finmindtrade.com/ 查看方案與用量。",
                "situation": situation,
            }
            _debug_cache[stock_id] = (result, time.time())
            return result
        result = {"token_set": True, "error": f"API 狀態碼 {r.status_code}", "message": "402 表示 v4 需付費或達請求上限，程式已自動改試 v3；若 v3 也失敗請見上方除錯訊息。"}
    except Exception as e:
        result = {"token_set": True, "error": str(e), "message": "網路或連線異常，請稍後再試。"}
    _debug_cache[stock_id] = (result, time.time())
    return result


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
        params = {"dataset": "TaiwanStockInfo"}
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        r = requests.get(url, params=params, headers=headers or None, timeout=30)
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
