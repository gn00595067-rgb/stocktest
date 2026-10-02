# -*- coding: utf-8 -*-
"""把資料庫（正式資料）匯出到 Google 試算表當備份。

沿用 app 寫回試算表的全部防呆：資料庫筆數異常變少就中止、寫完回讀核對、
更新 trades_backup 與滾動備份分頁。失敗時以非 0 結束（GitHub Actions 會寄信通知）。

用法：
    python scripts/export_db_to_sheet.py           # 來源：環境變數 DATABASE_URL，沒有就用 NEON_DATABASE_URL
    python scripts/export_db_to_sheet.py --force   # 內容沒變也照寫（回退到試算表模式前用）
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Windows 命令列預設 cp950，印不出 ✅ 等符號
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))


def main() -> int:
    ap = argparse.ArgumentParser(description="資料庫 → Google 試算表備份")
    ap.add_argument("--source", default=os.environ.get("DATABASE_URL") or os.environ.get("NEON_DATABASE_URL"))
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    if not args.source:
        # 還沒切換到資料庫模式：沒有東西要備份，正常結束
        print("尚未設定 DATABASE_URL，略過（目前仍是試算表模式）。")
        return 0

    # 腳本自己建連線；不讓 db.database 進入試算表模式
    os.environ["USE_GOOGLE_SHEET"] = "false"
    os.environ.pop("DATABASE_URL", None)
    from sqlalchemy import create_engine
    from db.database import normalize_database_url
    from services.sheet_sync import sync_db_to_sheet, is_google_sheet_enabled

    if not is_google_sheet_enabled():
        print("❌ 沒有 Google 試算表憑證（GOOGLE_SHEET_ID / GOOGLE_SHEET_CREDENTIALS_B64）。")
        return 2
    engine = create_engine(normalize_database_url(args.source), pool_pre_ping=True)
    ok, err = sync_db_to_sheet(engine, force=args.force)
    if ok:
        print("✅ 已匯出到 Google 試算表。")
        return 0
    print(f"❌ 匯出失敗：{err}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
