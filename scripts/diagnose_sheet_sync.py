# -*- coding: utf-8 -*-
"""診斷 Google 試算表同步失敗原因（逐步測試，不輸出金鑰）。"""
import os
import sys
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
except ImportError:
    pass

os.environ.setdefault("USE_GOOGLE_SHEET", "true")

import gspread
from sqlalchemy import text

from db.database import engine, get_session
from db.models import Trade, CustomMatchRule, UserAccount, UserTraderBinding
from services import sheet_sync
from services.sheet_sync import (
    _open_spreadsheet,
    sync_db_to_sheet,
    SHEET_TRADES,
    SHEET_RULES,
    SHEET_USERS,
    SHEET_USER_BINDINGS,
    is_google_sheet_enabled,
)


def _count_db():
    s = get_session()
    try:
        return {
            "trades": s.query(Trade).count(),
            "rules": s.query(CustomMatchRule).count(),
            "users": s.query(UserAccount).count(),
            "bindings": s.query(UserTraderBinding).count(),
        }
    finally:
        s.close()


def _worksheet_info(spread, title: str):
    try:
        ws = spread.worksheet(title)
        try:
            n = len(ws.get_all_records())
        except Exception as e:
            n = f"讀取失敗: {e}"
        return {"exists": True, "rows": ws.row_count, "cols": ws.col_count, "records": n}
    except gspread.WorksheetNotFound:
        return {"exists": False}


def _sync_one_sheet(spread, sheet_name: str, build_data_fn):
    """複製 sync_db_to_sheet 單表邏輯，回傳 (ok, err, rows, cells)"""
    headers_map = {
        SHEET_TRADES: sheet_sync.TRADES_HEADERS,
        SHEET_RULES: sheet_sync.RULES_HEADERS,
        SHEET_USERS: sheet_sync.USERS_HEADERS,
        SHEET_USER_BINDINGS: sheet_sync.USER_BINDINGS_HEADERS,
    }
    headers = headers_map[sheet_name]
    data = build_data_fn()
    n_rows = len(data) - 1 if data else 0
    n_cells = len(data) * len(headers) if data else 0

    try:
        try:
            ws = spread.worksheet(sheet_name)
        except gspread.WorksheetNotFound:
            ws = spread.add_worksheet(title=sheet_name, rows=max(100, n_rows + 10), cols=len(headers))
        if not data:
            return True, None, n_rows, n_cells
        t0 = time.perf_counter()
        ws.clear()
        ws.update(data, value_input_option="USER_ENTERED")
        return True, None, n_rows, n_cells, time.perf_counter() - t0
    except Exception as e:
        return False, f"{type(e).__name__}: {e}", n_rows, n_cells, None


def _build_all_data():
    """與 sync_db_to_sheet 相同查詢與列轉換"""
    with engine.connect() as conn:
        r_trades = conn.execute(text("""
            SELECT id, user, stock_id, trade_date, side, price, quantity, is_daytrade, fee, tax, note
            FROM trades ORDER BY id
        """)).fetchall()
        r_rules = conn.execute(text("""
            SELECT sell_trade_id, buy_trade_id, matched_qty, created_at FROM custom_match_rules
        """)).fetchall()
        r_users = conn.execute(text("""
            SELECT id, username, password_hash, role, is_active, created_at FROM user_accounts ORDER BY id
        """)).fetchall()
        r_bindings = conn.execute(text("""
            SELECT user_id, trader_name, created_at FROM user_trader_bindings ORDER BY user_id, trader_name
        """)).fetchall()

    def _date_str(v):
        if v is None or (isinstance(v, str) and not v.strip()):
            return ""
        if hasattr(v, "isoformat"):
            return v.isoformat()[:10]
        return str(v).strip()[:10]

    def _datetime_str(v):
        if v is None or (isinstance(v, str) and not v.strip()):
            return ""
        if hasattr(v, "strftime"):
            return v.strftime("%Y-%m-%d %H:%M:%S")
        return str(v).strip()[:19]

    trades_data = [sheet_sync.TRADES_HEADERS] + [
        [r[0], r[1], r[2], _date_str(r[3]), r[4], r[5], r[6],
         bool(r[7]) if r[7] is not None else False,
         r[8] if r[8] is not None else "", r[9] if r[9] is not None else "", r[10] or ""]
        for r in r_trades
    ]
    rules_data = [sheet_sync.RULES_HEADERS] + [
        [r[0], r[1], r[2], _datetime_str(r[3])] for r in r_rules
    ]
    users_data = [sheet_sync.USERS_HEADERS] + [
        [r[0], r[1], r[2], r[3], bool(r[4]) if r[4] is not None else False, _datetime_str(r[5])]
        for r in r_users
    ]
    bindings_data = [sheet_sync.USER_BINDINGS_HEADERS] + [
        [r[0], r[1], _datetime_str(r[2])] for r in r_bindings
    ]
    return trades_data, rules_data, users_data, bindings_data


def main():
    print("Google 試算表同步診斷\n")

    print("=== 1. 設定 ===")
    print(f"  USE_GOOGLE_SHEET: {os.environ.get('USE_GOOGLE_SHEET', '')}")
    print(f"  GOOGLE_SHEET_ID: {'已設定 (' + os.environ.get('GOOGLE_SHEET_ID', '')[:8] + '...)' if os.environ.get('GOOGLE_SHEET_ID') else '未設定'}")
    cred = os.environ.get("GOOGLE_SHEET_CREDENTIALS") or os.environ.get("GOOGLE_SHEET_CREDENTIALS_B64")
    print(f"  憑證: {'已設定' if cred else '未設定'}")
    print(f"  enabled: {is_google_sheet_enabled()}")

    counts = _count_db()
    print(f"\n  本機 DB: trades={counts['trades']}, rules={counts['rules']}, "
          f"users={counts['users']}, bindings={counts['bindings']}")

    print("\n=== 2. 開啟試算表 ===")
    spread, err = _open_spreadsheet()
    if err:
        print(f"  失敗: {err}")
        return 1
    print(f"  成功: 「{spread.title}」")

    print("\n=== 3. 試算表現況（只讀）===")
    for name in (SHEET_TRADES, SHEET_RULES, SHEET_USERS, SHEET_USER_BINDINGS):
        info = _worksheet_info(spread, name)
        if info.get("exists"):
            print(f"  {name}: {info['rows']}x{info['cols']} 格, 資料約 {info['records']} 筆")
        else:
            print(f"  {name}: 尚無工作表")

    trades_data, rules_data, users_data, bindings_data = _build_all_data()
    payloads = [
        (SHEET_TRADES, trades_data),
        (SHEET_RULES, rules_data),
        (SHEET_USERS, users_data),
        (SHEET_USER_BINDINGS, bindings_data),
    ]
    print("\n=== 4. 待寫入 payload 大小 ===")
    for name, data in payloads:
        cells = len(data) * (len(data[0]) if data else 0)
        print(f"  {name}: {max(0, len(data)-1)} 筆, 約 {cells} 儲存格")

    print("\n=== 5. 逐表 sync（與正式邏輯相同：clear + update）===")
    failed = []
    for name, data in payloads:
        print(f"\n  >> {name} ...", end=" ", flush=True)
        result = _sync_one_sheet(spread, name, lambda d=data: d)
        if result[0]:
            elapsed = result[4] if result[4] is not None else 0
            print(f"OK — {result[2]} 筆, ~{result[3]} 格, {elapsed:.2f}s")
        else:
            print(f"失敗 — {result[1]}")
            print(f"     (筆數 {result[2]}, 儲存格約 {result[3]})")
            failed.append((name, result[1]))

    print("\n=== 6. 官方 sync_db_to_sheet 整體 ===")
    t0 = time.perf_counter()
    ok, err = sync_db_to_sheet(engine)
    print(f"  {'成功' if ok else '失敗'} ({time.perf_counter()-t0:.2f}s)" + (f": {err}" if err else ""))

    print("\n=== 7. 結論 ===")
    if failed:
        for name, e in failed:
            print(f"  失敗工作表: {name}")
            print(f"    錯誤: {e}")
        if any("500" in str(e) for _, e in failed):
            big = max((len(d) - 1, n) for (n, d) in payloads)
            print("  含 HTTP 500：多為 Google 端暫障或單次 bulk 過大。")
            if big[0] > 300:
                print(f"  最大表 {big[1]} 有 {big[0]} 筆，建議分批寫入或稍後重試。")
    elif ok:
        print("  逐表與整體同步皆成功。若 Streamlit 仍偶發 500，可能是雲端網路/重試時機問題。")
    else:
        print(f"  整體失敗: {err}")

    return 1 if failed or not ok else 0


if __name__ == "__main__":
    sys.exit(main())
