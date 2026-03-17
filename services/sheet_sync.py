# -*- coding: utf-8 -*-
"""
Google 試算表與 SQLite 雙向同步：交易、自定沖銷規則。
啟用後，持倉與沖銷資料以試算表為長期儲存，程式重啟時從試算表載入。
"""
from datetime import date, datetime
from typing import Optional, Tuple, List, Any
import os

# 依賴 gspread、google-auth（optional）
try:
    import gspread
    from google.oauth2.service_account import Credentials
    _HAS_GSPREAD = True
except ImportError:
    _HAS_GSPREAD = False

# 試算表內工作表名稱
SHEET_TRADES = "trades"
SHEET_RULES = "custom_match_rules"

# 欄位順序（與 DB 對應）
TRADES_HEADERS = ["id", "user", "stock_id", "trade_date", "side", "price", "quantity", "is_daytrade", "fee", "tax", "note"]
RULES_HEADERS = ["sell_trade_id", "buy_trade_id", "matched_qty", "created_at"]

# 需寫入試算表時用的範圍（Scopes）
SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive.file"]


def _get_credentials_and_sheet_id():
    """從 st.secrets 或環境變數取得憑證與試算表 ID。支援 JSON 字串、dict、或 base64 編碼。"""
    creds_json = None
    sheet_id = None
    try:
        import streamlit as st
        if hasattr(st, "secrets"):
            creds_json = st.secrets.get("GOOGLE_SHEET_CREDENTIALS") or st.secrets.get("GOOGLE_SHEET_CREDENTIALS_B64")
            sheet_id = st.secrets.get("GOOGLE_SHEET_ID")
    except Exception:
        pass
    if not creds_json:
        creds_json = os.environ.get("GOOGLE_SHEET_CREDENTIALS") or os.environ.get("GOOGLE_SHEET_CREDENTIALS_B64")
    if not sheet_id:
        sheet_id = os.environ.get("GOOGLE_SHEET_ID")
    # 字串：先嘗試 JSON，失敗再嘗試 base64（Secrets 貼 base64 可避免引號/換行問題）
    if isinstance(creds_json, str):
        import json
        s = creds_json.strip()
        if s.startswith("{"):
            try:
                creds_json = json.loads(s)
            except json.JSONDecodeError:
                creds_json = None
        else:
            try:
                import base64
                decoded = base64.b64decode(s).decode("utf-8")
                creds_json = json.loads(decoded)
            except Exception:
                creds_json = None
    if isinstance(creds_json, str):
        creds_json = None
    sheet_id = str(sheet_id).strip() if sheet_id else ""
    return creds_json, sheet_id


def is_google_sheet_enabled() -> bool:
    """是否已設定並啟用 Google 試算表後端。"""
    if not _HAS_GSPREAD:
        return False
    creds, sheet_id = _get_credentials_and_sheet_id()
    return bool(creds and sheet_id)


def _open_spreadsheet():
    """開啟試算表，回傳 (gspread Spreadsheet, None) 或 (None, error_message)。"""
    if not _HAS_GSPREAD:
        return None, "未安裝 gspread 或 google-auth"
    creds_dict, sheet_id = _get_credentials_and_sheet_id()
    if not creds_dict:
        return None, "GOOGLE_SHEET_CREDENTIALS 未設定或格式錯誤（請貼完整 JSON 或改用 GOOGLE_SHEET_CREDENTIALS_B64 貼 base64）"
    if not sheet_id or not str(sheet_id).strip():
        return None, "GOOGLE_SHEET_ID 未設定"
    try:
        creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
        gc = gspread.authorize(creds)
        spread = gc.open_by_key(str(sheet_id).strip())
        return spread, None
    except Exception as e:
        err = str(e).strip() or type(e).__name__
        if "404" in err or "not found" in err.lower():
            return None, f"試算表不存在或未共用給服務帳號：{err}"
        if "403" in err or "permission" in err.lower() or "forbidden" in err.lower():
            return None, f"無權限（請將試算表共用給 {creds_dict.get('client_email', '')} 編輯者）：{err}"
        return None, f"無法開啟試算表：{err}"


def _parse_date(v) -> Optional[date]:
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    if isinstance(v, date):
        return v
    if hasattr(v, "date"):
        return v.date()
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except ValueError:
            continue
    return None


def _parse_datetime(v) -> Optional[datetime]:
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    if isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return datetime.combine(v, datetime.min.time())
    s = str(v).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(s[:19], fmt)
        except ValueError:
            continue
    return None


def _parse_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    s = str(v).strip().upper()
    return s in ("TRUE", "1", "YES", "Y", "是")


def sync_from_sheet_to_db(engine) -> Tuple[bool, Optional[str]]:
    """
    從 Google 試算表讀取「交易」「自定沖銷規則」並寫入 DB（覆寫現有資料）。
    回傳 (True, None) 成功；(False, error_msg) 失敗。
    """
    if not _HAS_GSPREAD:
        return False, "未安裝 gspread 或 google-auth"
    spread, err = _open_spreadsheet()
    if err:
        return False, err

    from sqlalchemy import text
    from db.models import Trade, CustomMatchRule

    try:
        # --- 讀取 trades ---
        try:
            ws_trades = spread.worksheet(SHEET_TRADES)
            rows_trades = ws_trades.get_all_records()
        except gspread.WorksheetNotFound:
            rows_trades = []

        # --- 讀取 custom_match_rules ---
        try:
            ws_rules = spread.worksheet(SHEET_RULES)
            rows_rules = ws_rules.get_all_records()
        except gspread.WorksheetNotFound:
            rows_rules = []

        with engine.connect() as conn:
            conn.execute(text("DELETE FROM custom_match_rules"))
            conn.execute(text("DELETE FROM trades"))
            conn.commit()

        # 插入 trades（保留 id）
        if rows_trades:
            with engine.connect() as conn:
                for r in rows_trades:
                    tid = r.get("id")
                    if tid is None or (isinstance(tid, str) and not tid.strip()):
                        continue
                    try:
                        tid = int(float(tid))
                    except (ValueError, TypeError):
                        continue
                    user = str(r.get("user") or "").strip() or "匯入"
                    stock_id = str(r.get("stock_id") or "").strip()
                    trade_date = _parse_date(r.get("trade_date"))
                    if not trade_date:
                        continue
                    side = str(r.get("side") or "BUY").strip().upper()
                    if side not in ("BUY", "SELL"):
                        continue
                    try:
                        price = float(r.get("price") or 0)
                        quantity = int(float(r.get("quantity") or 0))
                    except (ValueError, TypeError):
                        continue
                    is_daytrade = _parse_bool(r.get("is_daytrade"))
                    fee = r.get("fee")
                    fee = float(fee) if fee is not None and str(fee).strip() else None
                    tax = r.get("tax")
                    tax = float(tax) if tax is not None and str(tax).strip() else None
                    note = str(r.get("note") or "").strip() or None
                    conn.execute(text("""
                        INSERT INTO trades (id, user, stock_id, trade_date, side, price, quantity, is_daytrade, fee, tax, note)
                        VALUES (:id, :user, :stock_id, :trade_date, :side, :price, :quantity, :is_daytrade, :fee, :tax, :note)
                    """), {
                        "id": tid, "user": user, "stock_id": stock_id, "trade_date": trade_date,
                        "side": side, "price": price, "quantity": quantity, "is_daytrade": is_daytrade,
                        "fee": fee, "tax": tax, "note": note,
                    })
                conn.commit()

        # 插入 custom_match_rules
        if rows_rules:
            with engine.connect() as conn:
                for r in rows_rules:
                    try:
                        sell_id = int(float(r.get("sell_trade_id") or 0))
                        buy_id = int(float(r.get("buy_trade_id") or 0))
                        qty = int(float(r.get("matched_qty") or 0))
                    except (ValueError, TypeError):
                        continue
                    if sell_id <= 0 or buy_id <= 0 or qty <= 0:
                        continue
                    created = _parse_datetime(r.get("created_at"))
                    conn.execute(text("""
                        INSERT INTO custom_match_rules (sell_trade_id, buy_trade_id, matched_qty, created_at)
                        VALUES (:sell_trade_id, :buy_trade_id, :matched_qty, :created_at)
                    """), {
                        "sell_trade_id": sell_id, "buy_trade_id": buy_id, "matched_qty": qty,
                        "created_at": created,
                    })
                conn.commit()

        # 讓 SQLite 下次自動 id 從 max(id)+1 開始（無交易時為 0，下次新增會從 1 開始）
        if engine.dialect.name == "sqlite":
            with engine.connect() as conn:
                conn.execute(text("UPDATE sqlite_sequence SET seq = (SELECT COALESCE(MAX(id),0) FROM trades) WHERE name = 'trades'"))
                conn.commit()

        return True, None
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def sync_db_to_sheet(engine) -> Tuple[bool, Optional[str]]:
    """
    將 DB 的「交易」「自定沖銷規則」寫回 Google 試算表（整表覆寫）。
    回傳 (True, None) 成功；(False, error_msg) 失敗。
    """
    if not _HAS_GSPREAD:
        return False, "未安裝 gspread 或 google-auth"
    spread, err = _open_spreadsheet()
    if err:
        return False, err

    from sqlalchemy import text

    try:
        with engine.connect() as conn:
            r_trades = conn.execute(text("""
                SELECT id, user, stock_id, trade_date, side, price, quantity, is_daytrade, fee, tax, note
                FROM trades ORDER BY id
            """)).fetchall()
            r_rules = conn.execute(text("""
                SELECT sell_trade_id, buy_trade_id, matched_qty, created_at
                FROM custom_match_rules
            """)).fetchall()

        def row_trade(r):
            return [
                r[0], r[1], r[2], r[3].isoformat() if r[3] else "",
                r[4], r[5], r[6], bool(r[7]) if r[7] is not None else False,
                r[8] if r[8] is not None else "", r[9] if r[9] is not None else "",
                r[10] or "",
            ]

        def row_rule(r):
            return [
                r[0], r[1], r[2],
                r[3].strftime("%Y-%m-%d %H:%M:%S") if r[3] else "",
            ]

        # 寫入 trades 工作表
        try:
            ws_trades = spread.worksheet(SHEET_TRADES)
        except gspread.WorksheetNotFound:
            ws_trades = spread.add_worksheet(title=SHEET_TRADES, rows=1000, cols=len(TRADES_HEADERS))
        trades_data = [TRADES_HEADERS] + [row_trade(r) for r in r_trades]
        if trades_data:
            ws_trades.clear()
            ws_trades.update(trades_data, value_input_option="USER_ENTERED")

        # 寫入 custom_match_rules 工作表
        try:
            ws_rules = spread.worksheet(SHEET_RULES)
        except gspread.WorksheetNotFound:
            ws_rules = spread.add_worksheet(title=SHEET_RULES, rows=500, cols=len(RULES_HEADERS))
        rules_data = [RULES_HEADERS] + [row_rule(r) for r in r_rules]
        if rules_data:
            ws_rules.clear()
            ws_rules.update(rules_data, value_input_option="USER_ENTERED")

        return True, None
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
