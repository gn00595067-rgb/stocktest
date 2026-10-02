# -*- coding: utf-8 -*-
"""
Google 試算表與 SQLite 雙向同步：交易、自定沖銷規則。
啟用後，持倉與沖銷資料以試算表為長期儲存，程式重啟時從試算表載入。
"""
from datetime import date, datetime
from typing import Optional, Tuple, List, Any
import hashlib
import json
import os
import threading
import time

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
SHEET_USERS = "user_accounts"
SHEET_USER_BINDINGS = "user_trader_bindings"
SHEET_TRADERS = "traders"
SHEET_TRADES_BACKUP = "trades_backup"  # 自動備份：每次健康同步後保存 trades 快照

# 欄位順序（與 DB 對應）
TRADES_HEADERS = ["id", "user", "stock_id", "trade_date", "side", "price", "quantity", "is_daytrade", "fee", "tax", "note"]
RULES_HEADERS = ["sell_trade_id", "buy_trade_id", "matched_qty", "created_at"]
USERS_HEADERS = ["id", "username", "password_hash", "role", "is_active", "created_at"]
USER_BINDINGS_HEADERS = ["user_id", "trader_name", "created_at"]
TRADERS_HEADERS = ["id", "name", "created_at"]

# 需寫入試算表時用的範圍（Scopes）
SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive.file"]

# ─────────────────────────────────────────────────────────────────────────────
# 資料保護（可用環境變數覆寫）。設計目標：即使程式更新/同步出錯，也不易遺失或弄錯任何資料。
# 與寫回流程既有的三道防呆（0 筆守門、id 逐筆縮水守門、寫入後回讀驗證）搭配，另加：
#   1) 滾動時間戳備份：每次健康寫回後，把 trades 快照存成 trades_bak_<時間> 分頁（保留最近 N 份、
#      自動修剪）。單一固定備份會被下次覆寫蓋掉，滾動備份才留得住歷史（Google 版本紀錄只留少數幾版）。
#   2) 嚴格載入：從試算表載入時，凡「有 id 卻解析失敗」的資料列一律視為錯誤、不再靜默跳過；
#      發生時該次載入以失敗計，呼叫端不會標記為已同步，也就不會用不完整的記憶體覆寫試算表。
#      （本次聯電 16 張買進於 9/2 轉移遺失，即屬「載入掉列→覆寫成殘缺版」這一類。）
# ─────────────────────────────────────────────────────────────────────────────
BACKUP_PREFIX = "trades_bak_"

# 上次寫回（或剛從試算表載入）時的內容指紋；相同就略過寫回。每個行程（記憶體 DB）各自一份。
_last_synced_fingerprint: Optional[str] = None

# 已開啟的試算表（同一行程重用，省掉每次驗證＋抓試算表資訊的連線）
_spread_cache = None

# 寫回鎖：同一時間只允許一個寫回在跑。兩位同事同時送出時，若兩個寫回交錯，
# 較舊的快照可能在較新的之後寫入並修剪掉新列（交易從試算表消失）；排隊執行、
# 且在鎖內才讀 DB，就保證最後寫進去的一定是最新內容。
_sync_lock = threading.RLock()

# 滾動時間戳備份的最短間隔（分鐘）：連續送出時不必每筆都複製一整張分頁；
# 最新狀態另有固定備份分頁 trades_backup 每次都更新。
BACKUP_MIN_INTERVAL_MIN_DEFAULT = 10


def _env_flag(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "y", "是")


def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.environ.get(name, "")).strip())
    except (ValueError, TypeError):
        return default


def backup_prune_plan(titles, keep: int, prefix: str = BACKUP_PREFIX):
    """純函式：給定所有分頁標題，回傳「應刪除的舊備份標題」清單（保留最新 keep 份）。

    備份標題格式 trades_bak_YYYYmmdd_HHMM，字典序即時間序，故可直接排序。
    """
    baks = sorted([t for t in titles if str(t).startswith(prefix)])
    if keep <= 0:
        return baks
    if len(baks) <= keep:
        return []
    return baks[: len(baks) - keep]


def backup_is_due(titles, now: datetime, min_interval_min: int, prefix: str = BACKUP_PREFIX) -> bool:
    """純函式：最新一份滾動備份距今已達 min_interval_min 分鐘（或還沒有任何備份）才需要再備份。

    備份名的時間無法解析時一律視為需要備份（寧可多備一份）。
    """
    baks = sorted(t for t in titles if str(t).startswith(prefix))
    if not baks or min_interval_min <= 0:
        return True
    try:
        last = datetime.strptime(baks[-1][len(prefix):], "%Y%m%d_%H%M%S")
    except ValueError:
        return True
    return (now - last).total_seconds() >= min_interval_min * 60


def _rolling_backup(spread, source_ws, keep: int, worksheets=None) -> str:
    """把 source_ws 複製成 trades_bak_<時間> 分頁，並修剪舊備份到最多 keep 份。回傳備份分頁名（失敗回傳空字串）。

    worksheets：呼叫端剛抓過的分頁清單，可省掉再抓一次；沒給就自己抓。
    """
    name = BACKUP_PREFIX + datetime.now().strftime("%Y%m%d_%H%M%S")
    try:
        spread.duplicate_sheet(source_sheet_id=source_ws.id, new_sheet_name=name)
    except Exception:
        return ""
    try:
        wss = list(worksheets) if worksheets is not None else spread.worksheets()
        by_title = {w.title: w for w in wss}
        for t in backup_prune_plan(list(by_title) + [name], keep):
            try:
                spread.del_worksheet(by_title[t])
            except Exception:
                pass
    except Exception:
        pass
    return name


def _is_quota_error(e) -> bool:
    """判斷是否為 Google Sheets 配額／限流錯誤（429 或 5xx 暫時性）。"""
    msg = str(e)
    if "429" in msg or "Quota exceeded" in msg or "RESOURCE_EXHAUSTED" in msg.upper():
        return True
    resp = getattr(e, "response", None)
    try:
        if resp is not None and getattr(resp, "status_code", None) in (429, 500, 503):
            return True
    except Exception:
        pass
    return False


def _retry_on_quota(fn, attempts: int = 3, backoff=(3, 8)):
    """呼叫 fn；遇到配額/限流（429）時退避重試，其餘錯誤直接拋出。

    寫入請求已批次化（每次同步僅 2 個 write），故只需輕量重試吸收短暫尖峰；
    若仍失敗，交由呼叫端回報友善訊息（資料已存 DB，不會遺失）。
    """
    import time
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:
            if not _is_quota_error(e) or i == attempts - 1:
                raise
            time.sleep(backoff[min(i, len(backoff) - 1)])


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
    """開啟試算表（同一行程重用已開啟的連線），回傳 (gspread Spreadsheet, None) 或 (None, error_message)。

    每次重開要驗證憑證＋抓整份試算表資訊，等於多 1～2 次連線；同步出錯時會呼叫
    _forget_spreadsheet() 清掉，下次重開，不會一直用壞掉的連線。
    """
    global _spread_cache
    if _spread_cache is not None:
        return _spread_cache, None
    spread, err = _open_spreadsheet_fresh()
    if spread is not None:
        _spread_cache = spread
    return spread, err


def _forget_spreadsheet() -> None:
    global _spread_cache
    _spread_cache = None


def _open_spreadsheet_fresh():
    """實際連線開啟試算表，回傳 (gspread Spreadsheet, None) 或 (None, error_message)。"""
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
    從 Google 試算表讀取「交易」「自定沖銷規則」「帳號」「權限綁定」並寫入 DB（覆寫現有資料）。
    回傳 (True, None) 成功；(False, error_msg) 失敗。
    """
    if not _HAS_GSPREAD:
        return False, "未安裝 gspread 或 google-auth"
    spread, err = _open_spreadsheet()
    if err:
        return False, err

    from sqlalchemy import text
    from db.models import Trade, CustomMatchRule, UserAccount, UserTraderBinding, Trader

    try:
        # --- 讀取 trades ---
        try:
            ws_trades = spread.worksheet(SHEET_TRADES)
            rows_trades = ws_trades.get_all_records()
        except gspread.WorksheetNotFound:
            rows_trades = []

        # --- 讀取 traders（買賣人名單） ---
        try:
            ws_traders = spread.worksheet(SHEET_TRADERS)
            rows_traders = ws_traders.get_all_records()
        except gspread.WorksheetNotFound:
            rows_traders = []

        # --- 讀取 custom_match_rules ---
        try:
            ws_rules = spread.worksheet(SHEET_RULES)
            rows_rules = ws_rules.get_all_records()
        except gspread.WorksheetNotFound:
            rows_rules = []

        # --- 讀取 user_accounts ---
        try:
            ws_users = spread.worksheet(SHEET_USERS)
            rows_users = ws_users.get_all_records()
        except gspread.WorksheetNotFound:
            rows_users = []

        # --- 讀取 user_trader_bindings ---
        try:
            ws_user_bindings = spread.worksheet(SHEET_USER_BINDINGS)
            rows_user_bindings = ws_user_bindings.get_all_records()
        except gspread.WorksheetNotFound:
            rows_user_bindings = []

        with engine.connect() as conn:
            conn.execute(text("DELETE FROM user_trader_bindings"))
            conn.execute(text("DELETE FROM user_accounts"))
            conn.execute(text("DELETE FROM custom_match_rules"))
            conn.execute(text("DELETE FROM trades"))
            conn.execute(text("DELETE FROM traders"))
            conn.commit()

        # 插入 traders（保留 id）
        if rows_traders:
            with engine.connect() as conn:
                for r in rows_traders:
                    tid = r.get("id")
                    name = str(r.get("name") or "").strip()
                    if not name:
                        continue
                    try:
                        tid = int(float(tid)) if tid is not None and str(tid).strip() else None
                    except (ValueError, TypeError):
                        tid = None
                    created_at = _parse_datetime(r.get("created_at"))
                    if tid is not None:
                        conn.execute(text(
                            "INSERT INTO traders (id, name, created_at) VALUES (:id, :name, :created_at)"
                        ), {"id": tid, "name": name, "created_at": created_at})
                    else:
                        conn.execute(text(
                            "INSERT INTO traders (name, created_at) VALUES (:name, :created_at)"
                        ), {"name": name, "created_at": created_at})
                conn.commit()

        # 插入 trades（保留 id）
        # skipped_data：有 id（＝真實資料列）卻解析失敗而被跳過的筆數。這種列絕不能靜默丟掉，
        # 否則就是這次聯電掉單的重演——載入少了列、下次存檔就把試算表覆寫成殘缺版。
        skipped_data = []
        inserted_trades = 0
        if rows_trades:
            with engine.connect() as conn:
                for r in rows_trades:
                    tid = r.get("id")
                    if tid is None or (isinstance(tid, str) and not tid.strip()):
                        continue  # 完全空白列，正常略過
                    try:
                        tid = int(float(tid))
                    except (ValueError, TypeError):
                        skipped_data.append(f"id={tid!r} 非數字")
                        continue
                    user = str(r.get("user") or "").strip() or "匯入"
                    stock_id = str(r.get("stock_id") or "").strip()
                    trade_date = _parse_date(r.get("trade_date"))
                    if not trade_date:
                        skipped_data.append(f"id={tid} 日期無法解析({r.get('trade_date')!r})")
                        continue
                    side = str(r.get("side") or "BUY").strip().upper()
                    if side not in ("BUY", "SELL"):
                        skipped_data.append(f"id={tid} side異常({r.get('side')!r})")
                        continue
                    try:
                        price = float(r.get("price") or 0)
                        quantity = int(float(r.get("quantity") or 0))
                    except (ValueError, TypeError):
                        skipped_data.append(f"id={tid} 價格/股數非數字")
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
                    inserted_trades += 1
                conn.commit()

        # 嚴格載入：有資料列解析失敗 → 該次載入視為失敗，呼叫端不標記已同步、也不會用殘缺記憶體覆寫試算表。
        if skipped_data and _env_flag("SHEET_STRICT_IMPORT", True):
            sample = "；".join(skipped_data[:5])
            more = f"（另有 {len(skipped_data) - 5} 筆）" if len(skipped_data) > 5 else ""
            return False, (
                f"從試算表載入時有 {len(skipped_data)} 筆交易資料無法解析，為保護資料已中止載入（不會覆寫試算表）。"
                f"請修正試算表 trades 分頁後重試：{sample}{more}"
            )

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

        # 插入 user_accounts（保留 id）
        if rows_users:
            with engine.connect() as conn:
                for r in rows_users:
                    uid = r.get("id")
                    if uid is None or (isinstance(uid, str) and not uid.strip()):
                        continue
                    try:
                        uid = int(float(uid))
                    except (ValueError, TypeError):
                        continue
                    username = str(r.get("username") or "").strip()
                    password_hash = str(r.get("password_hash") or "").strip()
                    role = str(r.get("role") or "user").strip().lower()
                    if role not in ("admin", "user"):
                        role = "user"
                    if not username or not password_hash:
                        continue
                    is_active = _parse_bool(r.get("is_active"))
                    created_at = _parse_datetime(r.get("created_at"))
                    conn.execute(text("""
                        INSERT INTO user_accounts (id, username, password_hash, role, is_active, created_at)
                        VALUES (:id, :username, :password_hash, :role, :is_active, :created_at)
                    """), {
                        "id": uid,
                        "username": username,
                        "password_hash": password_hash,
                        "role": role,
                        "is_active": is_active,
                        "created_at": created_at,
                    })
                conn.commit()

        # 插入 user_trader_bindings
        if rows_user_bindings:
            with engine.connect() as conn:
                for r in rows_user_bindings:
                    try:
                        user_id = int(float(r.get("user_id") or 0))
                    except (ValueError, TypeError):
                        continue
                    trader_name = str(r.get("trader_name") or "").strip()
                    created_at = _parse_datetime(r.get("created_at"))
                    if user_id <= 0 or not trader_name:
                        continue
                    conn.execute(text("""
                        INSERT INTO user_trader_bindings (user_id, trader_name, created_at)
                        VALUES (:user_id, :trader_name, :created_at)
                    """), {
                        "user_id": user_id,
                        "trader_name": trader_name,
                        "created_at": created_at,
                    })
                conn.commit()

        # 讓 SQLite 下次自動 id 從 max(id)+1 開始（無交易時 sqlite_sequence 可能尚不存在，略過即可）
        if engine.dialect.name == "sqlite":
            try:
                with engine.connect() as conn:
                    conn.execute(text("UPDATE sqlite_sequence SET seq = (SELECT COALESCE(MAX(id),0) FROM trades) WHERE name = 'trades'"))
                    conn.execute(text("UPDATE sqlite_sequence SET seq = (SELECT COALESCE(MAX(id),0) FROM user_accounts) WHERE name = 'user_accounts'"))
                    conn.execute(text("UPDATE sqlite_sequence SET seq = (SELECT COALESCE(MAX(id),0) FROM traders) WHERE name = 'traders'"))
                    conn.commit()
            except Exception:
                pass

        # 剛載入完：DB 內容＝試算表內容，之後沒有實際變更就不必寫回
        remember_db_as_synced(engine)
        return True, None
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def _read_db_payload(engine):
    """從 DB 讀出要寫回試算表的五張表（含表頭），回傳 (r_trades 原始列, [(工作表名, 資料), ...])。"""
    from sqlalchemy import text

    with engine.connect() as conn:
        r_trades = conn.execute(text("""
            SELECT id, user, stock_id, trade_date, side, price, quantity, is_daytrade, fee, tax, note
            FROM trades ORDER BY id
        """)).fetchall()
        r_rules = conn.execute(text("""
            SELECT sell_trade_id, buy_trade_id, matched_qty, created_at
            FROM custom_match_rules
        """)).fetchall()
        r_users = conn.execute(text("""
            SELECT id, username, password_hash, role, is_active, created_at
            FROM user_accounts
            ORDER BY id
        """)).fetchall()
        r_user_bindings = conn.execute(text("""
            SELECT user_id, trader_name, created_at
            FROM user_trader_bindings
            ORDER BY user_id, trader_name
        """)).fetchall()
        r_traders = conn.execute(text("""
            SELECT id, name, created_at
            FROM traders
            ORDER BY id
        """)).fetchall()

    def _date_str(v):
        """將 date/datetime 或字串轉成 YYYY-MM-DD 字串；DB 有時回傳 str。"""
        if v is None or (isinstance(v, str) and not v.strip()):
            return ""
        if hasattr(v, "isoformat"):
            return v.isoformat()[:10]
        return str(v).strip()[:10]

    def _datetime_str(v):
        """將 datetime 或字串轉成 YYYY-MM-DD HH:MM:SS；DB 有時回傳 str。"""
        if v is None or (isinstance(v, str) and not v.strip()):
            return ""
        if hasattr(v, "strftime"):
            return v.strftime("%Y-%m-%d %H:%M:%S")
        return str(v).strip()[:19]

    def row_trade(r):
        return [
            r[0], r[1], r[2], _date_str(r[3]),
            r[4], r[5], r[6], bool(r[7]) if r[7] is not None else False,
            r[8] if r[8] is not None else "", r[9] if r[9] is not None else "",
            r[10] or "",
        ]

    def row_rule(r):
        return [
            r[0], r[1], r[2],
            _datetime_str(r[3]),
        ]

    def row_user(r):
        return [
            r[0], r[1], r[2], r[3],
            bool(r[4]) if r[4] is not None else False,
            _datetime_str(r[5]),
        ]

    def row_user_binding(r):
        return [
            r[0], r[1], _datetime_str(r[2]),
        ]

    def row_trader(r):
        return [
            r[0], r[1], _datetime_str(r[2]),
        ]

    # 準備各表資料（含表頭）
    trades_data = [TRADES_HEADERS] + [row_trade(r) for r in r_trades]
    rules_data = [RULES_HEADERS] + [row_rule(r) for r in r_rules]
    users_data = [USERS_HEADERS] + [row_user(r) for r in r_users]
    user_bindings_data = [USER_BINDINGS_HEADERS] + [row_user_binding(r) for r in r_user_bindings]
    traders_data = [TRADERS_HEADERS] + [row_trader(r) for r in r_traders]
    sheets = [
        (SHEET_TRADES, trades_data),
        (SHEET_RULES, rules_data),
        (SHEET_USERS, users_data),
        (SHEET_USER_BINDINGS, user_bindings_data),
        (SHEET_TRADERS, traders_data),
    ]
    return r_trades, sheets


def _payload_fingerprint(sheets) -> str:
    """寫回內容的指紋：內容相同 → 指紋相同，用來判斷「資料其實沒變」可略過寫回。"""
    raw = json.dumps(sheets, ensure_ascii=False, default=str, sort_keys=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def remember_db_as_synced(engine) -> None:
    """記下「目前 DB 內容＝試算表內容」（剛從試算表載入完時呼叫），之後內容沒變就不寫回。"""
    global _last_synced_fingerprint
    try:
        _, sheets = _read_db_payload(engine)
        _last_synced_fingerprint = _payload_fingerprint(sheets)
    except Exception:
        _last_synced_fingerprint = None


def _norm_id(v) -> str:
    """id 正規化成字串：12、12.0、"12" 都變 "12"；空值回傳空字串，非數字原樣去空白。"""
    s = str(v).strip() if v is not None else ""
    if not s:
        return ""
    try:
        return str(int(float(s.replace(",", ""))))
    except ValueError:
        return s


def _read_sheet_ids(spread, title) -> List[str]:
    """只讀某分頁的 A 欄（id），回傳表頭以下「非空白」的 id 清單。

    防呆與回讀驗證只需要 id，不必把整張表（11 欄 × 全部交易）抓回來。
    用 UNFORMATTED_VALUE 讀原始數值，不受儲存格顯示格式（如千分位）影響。
    """
    res = _retry_on_quota(lambda: spread.values_get(
        f"{title}!A:A", params={"valueRenderOption": "UNFORMATTED_VALUE"}
    ))
    vals = (res or {}).get("values", []) if isinstance(res, dict) else []
    ids = [_norm_id(r[0]) if r else "" for r in vals[1:]]
    return [i for i in ids if i]


def sync_db_to_sheet(engine, force: bool = False) -> Tuple[bool, Optional[str]]:
    """
    將 DB 的「交易」「自定沖銷規則」「帳號」「權限綁定」寫回 Google 試算表（整表覆寫）。
    內容與上次寫回相同時略過（force=True 強制寫回）。
    回傳 (True, None) 成功；(False, error_msg) 失敗。

    同一時間只跑一個寫回（_sync_lock），避免兩人同時送出時舊快照蓋掉新資料。
    """
    if not _HAS_GSPREAD:
        return False, "未安裝 gspread 或 google-auth"
    t0 = time.monotonic()
    with _sync_lock:
        ok, err, wrote = _sync_db_to_sheet_locked(engine, force)
    if wrote:
        # 用 print：Streamlit Cloud 的 log 預設看不到 INFO 等級，方便實際量測寫回耗時
        print(f"[sheet_sync] 寫回試算表 {'成功' if ok else '失敗'}，耗時 {time.monotonic() - t0:.1f} 秒", flush=True)
    return ok, err


def _sync_db_to_sheet_locked(engine, force: bool) -> Tuple[bool, Optional[str], bool]:
    """sync_db_to_sheet 的本體（呼叫端已持有 _sync_lock）。回傳 (ok, err, 是否有連線試算表)。"""
    global _last_synced_fingerprint

    # 在鎖內才讀 DB：排在後面的寫回一定拿到最新內容
    try:
        r_trades, sheets = _read_db_payload(engine)
    except Exception as e:
        return False, f"{type(e).__name__}: {e}", False
    trades_data = sheets[0][1]

    # 內容和上次寫回（或剛載入）時完全一樣 → 不寫回、不產生備份，也不連線試算表。
    # 只是打開頁面也會 commit（例如更新股票主檔），以前每次都整表覆寫並輪替備份，
    # 會把真正有用的歷史備份擠掉。
    fingerprint = _payload_fingerprint(sheets)
    if not force and fingerprint == _last_synced_fingerprint:
        return True, None, False

    spread, err = _open_spreadsheet()
    if err:
        return False, err, True

    try:
        # 一次抓回全部分頁（1 次連線），缺的才建；不再逐張 worksheet() 各連線一次
        wss = list(_retry_on_quota(lambda: spread.worksheets()))
        by_title = {w.title: w for w in wss}

        def _ensure_ws(title, cols):
            if title not in by_title:
                ws = spread.add_worksheet(title=title, rows=1000, cols=cols)
                by_title[title] = ws
                wss.append(ws)
            return by_title[title]
        ws_trades = _ensure_ws(SHEET_TRADES, len(TRADES_HEADERS))
        _ensure_ws(SHEET_RULES, len(RULES_HEADERS))
        _ensure_ws(SHEET_USERS, len(USERS_HEADERS))
        _ensure_ws(SHEET_USER_BINDINGS, len(USER_BINDINGS_HEADERS))
        _ensure_ws(SHEET_TRADERS, len(TRADERS_HEADERS))

        # ③ 防呆（逐筆 id 比對）：抓試算表現有交易 id 與記憶體比對。記憶體若明顯變少，
        # 代表載入不完整/記憶體過期，強行寫回會用殘缺資料覆蓋掉試算表——這是交易一批批
        # 消失的根因。此時直接中止，並列出「少了哪幾筆 id」方便核對。
        # 讀不到現有 id 時一律不寫（以前讀失敗會略過防呆直接寫，等於把保護拿掉）：
        # 例外直接交給最外層，回報失敗、不記成已同步，下次存檔再試。
        sheet_id_list = _read_sheet_ids(spread, SHEET_TRADES)
        sheet_ids = set(sheet_id_list)
        mem_ids = {_norm_id(r[0]) for r in r_trades}
        missing_ids = [i for i in sheet_ids if i not in mem_ids]
        existing_rows = len(sheet_id_list)
        mem_rows = len(r_trades)
        if existing_rows > 0 and (
            mem_rows == 0
            or (mem_rows < existing_rows * 0.85 and (existing_rows - mem_rows) >= 10)
        ):
            _eg = "、".join(sorted(missing_ids, key=lambda x: (len(x), x))[:8])
            return False, (
                f"已中止寫回以保護資料：記憶體 {mem_rows} 筆、試算表 {existing_rows} 筆"
                f"（記憶體少了 {len(missing_ids)} 筆交易，例如 id {_eg}…）。"
                f"這通常代表 app 記憶體不是最新，強行寫回會蓋掉試算表。"
                f"請先『Reboot』重新載入最新資料再操作；若你確實剛大量刪除，Reboot 後再刪一次即可。"
            ), True

        # 安全寫回：先「寫入」再「修剪」（寫失敗不清空），批次 2 個 write 請求避開 429。
        body = {
            "valueInputOption": "USER_ENTERED",
            "data": [{"range": f"{title}!A1", "values": vals} for title, vals in sheets],
        }
        _retry_on_quota(lambda: spread.values_batch_update(body))

        trim_ranges = [f"{title}!A{len(vals) + 1}:Z" for title, vals in sheets]
        try:
            _retry_on_quota(lambda: spread.values_batch_clear(body={"ranges": trim_ranges}))
        except Exception:
            pass

        # ② 寫入後回讀驗證：讀回 trades 的 id，必須和記憶體「完全相同」（不只筆數夠）。
        #    讀不回來也算失敗：不記成已同步，下次存檔會整份重寫一次。
        try:
            back_list = _read_sheet_ids(spread, SHEET_TRADES)
        except Exception as e:
            if not _is_quota_error(e):
                _forget_spreadsheet()
            return False, (
                f"已寫入試算表，但寫入後無法讀回核對（{type(e).__name__}: {e}）。"
                f"資料已存在 app 內、不會遺失；下次存檔會再整份寫回一次。"
            ), True
        if len(back_list) != mem_rows or set(back_list) != mem_ids:
            lost = sorted(mem_ids - set(back_list), key=lambda x: (len(x), x))[:8]
            return False, (
                f"寫入後回讀 {len(back_list)} 筆、預期 {mem_rows} 筆，內容不一致"
                f"{'（缺 id ' + '、'.join(lost) + '）' if lost else ''}，可能未完整寫入。"
                f"請 Reboot 後核對交易明細；若不符請告知，資料在版本紀錄與備份中皆可還原。"
            ), True

        _last_synced_fingerprint = fingerprint

        # ① 自動備份：通過防呆與回讀驗證的健康資料才會來到這。
        #   a) 固定備份分頁 trades_backup：永遠保有「最近一次健康快照」，一鍵可救。
        #      先寫入再修剪（以前是先清空再寫，中途失敗會留下空的備份）。
        #   b) 滾動時間戳備份 trades_bak_<時間>：保留最近 N 份歷史，避免單一快照被下一次覆寫蓋掉
        #      （Google 版本紀錄對這種表只留少數幾版，不足以回溯，故自建滾動備份）。
        #      距上一份不到 SHEET_BACKUP_MIN_INTERVAL 分鐘就不再複製，連續送出時不必每筆都備份一次。
        #   （備份失敗不影響主流程。）
        try:
            _ensure_ws(SHEET_TRADES_BACKUP, len(TRADES_HEADERS))
            _retry_on_quota(lambda: spread.values_batch_update({
                "valueInputOption": "USER_ENTERED",
                "data": [{"range": f"{SHEET_TRADES_BACKUP}!A1", "values": trades_data}],
            }))
            _retry_on_quota(lambda: spread.values_batch_clear(
                body={"ranges": [f"{SHEET_TRADES_BACKUP}!A{len(trades_data) + 1}:Z"]}
            ))
        except Exception:
            pass
        try:
            if backup_is_due(
                list(by_title), datetime.now(),
                _env_int("SHEET_BACKUP_MIN_INTERVAL", BACKUP_MIN_INTERVAL_MIN_DEFAULT),
            ):
                _rolling_backup(spread, ws_trades, _env_int("SHEET_BACKUP_KEEP", 10), worksheets=wss)
        except Exception:
            pass

        return True, None, True
    except Exception as e:
        if _is_quota_error(e):
            return False, (
                "Google Sheet 寫入配額暫時用盡（429：每分鐘上限）。"
                "你的變更已存進資料庫、不會遺失；請稍等約 1 分鐘再操作，"
                "或下次任何存檔時會一併把它寫回試算表。"
            ), True
        # 非配額錯誤可能是連線失效：丟掉已開啟的試算表，下次重新連線
        _forget_spreadsheet()
        return False, f"{type(e).__name__}: {e}", True
