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
        # 一次撈出全部既有主檔再比對：逐筆查詢在記憶體 SQLite 不花錢，
        # 換成雲端資料庫每筆都是一次網路來回，3000 多檔股票會卡上好幾分鐘。
        # 值沒變的欄位 SQLAlchemy 不會發 UPDATE，所以沒變動時這裡只有 1 次查詢。
        existing_by_id = {m.stock_id: m for m in sess.query(StockMaster).all()}
        changed = 0
        for item in items:
            existing = existing_by_id.get(item["stock_id"])
            if existing:
                # 只改有變的欄位；全都沒變就不存檔（每位使用者開 app 都會跑這裡，
                # 無謂的存檔會讓「交易資料快取」失效，下一頁又得整批重讀）
                for f in ("name", "industry_name", "market", "exchange", "is_etf"):
                    if getattr(existing, f) != item[f]:
                        setattr(existing, f, item[f])
                        changed += 1
            else:
                changed += 1
                sess.add(StockMaster(
                    stock_id=item["stock_id"],
                    name=item["name"],
                    industry_name=item["industry_name"],
                    market=item["market"],
                    exchange=item["exchange"],
                    is_etf=item["is_etf"],
                ))
        if changed:
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


# ---------- 股票搜尋（新增股票用；2026-10-19 Peggy 找不到「貿聯-KY」） ----------
def _norm(s: str) -> str:
    """比對用：全形轉半形、英文轉大寫、去掉空白與各種破折號（iPad 中文鍵盤常打出全形「－」或「—」）。"""
    import unicodedata
    s = unicodedata.normalize("NFKC", str(s or "")).upper()
    for ch in " 　-‐‑‒–—―－_":
        s = s.replace(ch, "")
    return s


def search_stocks(stock_list, keyword: str, limit: int = 60):
    """依代號或名稱搜尋股票清單（[{stock_id, name, ...}]），回傳 (符合清單, 你是不是要找清單)。

    同一代號只回一筆。找不到時用相近代號／名稱給建議（例：打成 3655 → 建議 3665 貿聯-KY）。
    """
    import difflib
    k = _norm(keyword)
    if not k:
        return [], []
    seen, uniq = set(), []
    for s in stock_list or []:
        sid = str(s.get("stock_id") or "").strip()
        if sid and sid not in seen:
            seen.add(sid)
            uniq.append(s)
    hits = [s for s in uniq if k in _norm(s.get("stock_id")) or k in _norm(s.get("name"))]
    hits.sort(key=lambda s: (not _norm(s.get("stock_id")).startswith(k), str(s.get("stock_id"))))
    if hits:
        return hits[:limit], []
    if k.isdigit():
        # 代號：只差一碼（打錯、少打、多打、相鄰對調）的都列出，例：3655 → 3665
        sug = [s for s in uniq if _code_distance(k, _norm(s.get("stock_id"))) <= 1]

        def _same_prefix(s):
            c = _norm(s.get("stock_id"))
            return next((i for i, (x, y) in enumerate(zip(k, c)) if x != y), min(len(k), len(c)))
        sug.sort(key=lambda s: (-_same_prefix(s), str(s.get("stock_id"))))   # 前面幾碼一樣的排前面
        return [], sug[:40]
    names = {}
    for s in uniq:
        names.setdefault(_norm(s.get("name")), s)
    close = difflib.get_close_matches(k, list(names), n=5, cutoff=0.6)
    return [], [names[c] for c in close][:5]


def _code_distance(a: str, b: str) -> int:
    """兩個代號差幾碼（含相鄰兩碼對調算 1）；差超過 1 就回 2（只需要知道是不是只差一碼）。"""
    if a == b:
        return 0
    if abs(len(a) - len(b)) > 1:
        return 2
    if len(a) == len(b):
        diff = [i for i in range(len(a)) if a[i] != b[i]]
        if len(diff) == 1:
            return 1
        if len(diff) == 2 and diff[1] == diff[0] + 1 and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]]:
            return 1
        return 2
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    return 1 if any(long_[:i] + long_[i + 1:] == short for i in range(len(long_))) else 2
