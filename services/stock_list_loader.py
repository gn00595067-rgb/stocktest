# -*- coding: utf-8 -*-
"""從 Google Sheet 載入股票清單並寫入 stock_master，供 app 啟動與主檔設定頁使用。"""
import io
import time
from typing import List, Optional, Tuple

import pandas as pd
import requests
from sqlalchemy.exc import OperationalError, IntegrityError

# Google Sheet 股票清單：請將試算表設為「知道連結的任何人可檢視」，第一張工作表欄位為 stock_id, name, industry_name, market, exchange, is_etf
STOCK_LIST_GOOGLE_SHEET_ID = "1MwFZ1W_CJ-U1a7YEmu4dDgddTywbOuJQKwFamAbVd-8"


def _parse_is_etf(v) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().upper() in ("TRUE", "1", "YES", "Y")
    return bool(v)


def _parse_row_to_item(row) -> Optional[dict]:
    sid = str(row["stock_id"]).strip()
    if not sid:
        return None
    return {
        "stock_id": sid,
        "name": (str(row.get("name", "") or sid))[:100],
        "industry_name": (str(row.get("industry_name", "")) or "")[:100],
        "market": str(row.get("market", "TW")),
        "exchange": str(row.get("exchange", "TWSE")),
        "is_etf": _parse_is_etf(row.get("is_etf", False)),
    }


def load_from_google_sheet() -> Tuple[List[dict], Optional[str]]:
    """
    從設定的 Google Sheet 匯出 CSV 讀取股票清單。
    回傳 (items, None) 成功；([], error_str) 失敗。
    """
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://docs.google.com/",
            "Accept": "text/csv,text/plain,*/*",
        }
        session = requests.Session()
        session.headers.update(headers)
        url = f"https://docs.google.com/spreadsheets/d/{STOCK_LIST_GOOGLE_SHEET_ID}/export?format=csv"
        r = session.get(url, timeout=15)
        r.raise_for_status()
        text = r.content.decode("utf-8-sig")
        df = pd.read_csv(io.StringIO(text))
        df.columns = df.columns.str.strip().str.lower().str.replace(" ", "_")
        if df.empty or "stock_id" not in df.columns:
            return [], "試算表無 stock_id 欄位或為空"
        out = []
        for _, row in df.iterrows():
            item = _parse_row_to_item(row)
            if item:
                out.append(item)
        return out, None
    except Exception as e:
        return [], f"{type(e).__name__}: {e}"


def write_to_stock_master(items: List[dict]) -> Tuple[bool, Optional[str]]:
    """
    將 [{"stock_id", "name", ...}, ...] 寫入 stock_master（upsert）。
    回傳 (True, None) 成功；(False, error_str) 失敗。
    """
    if not items:
        return True, None
    from db.database import get_session
    from db.models import StockMaster

    sess = get_session()
    try:
        for item in items:
            existing = sess.query(StockMaster).filter(StockMaster.stock_id == item["stock_id"]).first()
            if existing:
                existing.name = item["name"]
                existing.industry_name = item["industry_name"]
                existing.market = item["market"]
                existing.exchange = item["exchange"]
                existing.is_etf = item["is_etf"]
            else:
                sess.add(StockMaster(
                    stock_id=item["stock_id"],
                    name=item["name"],
                    industry_name=item["industry_name"],
                    market=item["market"],
                    exchange=item["exchange"],
                    is_etf=item["is_etf"],
                ))
        sess.commit()
        return True, None
    except OperationalError as e:
        sess.rollback()
        return False, f"無法寫入資料庫（唯讀環境）: {e}"
    except IntegrityError:
        sess.rollback()
        return False, "主鍵重複（IntegrityError）"
    finally:
        sess.close()


def sync_google_sheet_to_db() -> Tuple[bool, int, Optional[str]]:
    """
    從 Google Sheet 取得清單並寫入 stock_master。
    回傳 (success, count, error_str)。count 為去重後寫入的筆數；失敗時 count=0、error_str 為錯誤訊息。
    """
    items, err = load_from_google_sheet()
    if err:
        return False, 0, err
    n_raw = len(items)
    by_id = {x["stock_id"]: x for x in items}
    items = list(by_id.values())
    ok, err = write_to_stock_master(items)
    if not ok:
        return False, 0, err
    return True, len(items), None


def ensure_google_sheet_loaded() -> None:
    """
    若本 session 尚未載入過 Google Sheet 股票清單，則執行一次同步並寫入 stock_master。
    供 app.py 與各 pages 在載入時呼叫，使「左側欄一出現」就會自動載入，無需點進主檔設定。
    """
    import streamlit as st
    from db.database import get_session
    from db.models import StockMaster

    # 以短間隔節流，避免每次 rerun 都重打 Google Sheet。
    now = time.time()
    last_at = float(st.session_state.get("gs_auto_loaded_at", 0) or 0)
    last_ok = bool(st.session_state.get("gs_auto_loaded_ok", False))
    min_retry_sec = 20
    if (now - last_at) < min_retry_sec and last_ok:
        return

    # 若主檔已有資料且曾成功同步，就不重複同步。
    stock_count = 0
    try:
        sess = get_session()
        stock_count = int(sess.query(StockMaster).count())
    except Exception:
        stock_count = 0
    finally:
        try:
            sess.close()
        except Exception:
            pass

    if stock_count > 0 and last_ok:
        st.session_state["gs_auto_loaded_at"] = now
        return

    # 失敗過也會在後續頁面自動重試（不需要回主檔設定手動按）。
    try:
        ok, _n, err = sync_google_sheet_to_db()
        st.session_state["gs_auto_loaded_at"] = now
        st.session_state["gs_auto_loaded_ok"] = bool(ok)
        st.session_state["gs_auto_loaded_err"] = None if ok else (err or "unknown error")
    except Exception as e:
        st.session_state["gs_auto_loaded_at"] = now
        st.session_state["gs_auto_loaded_ok"] = False
        st.session_state["gs_auto_loaded_err"] = f"{type(e).__name__}: {e}"
