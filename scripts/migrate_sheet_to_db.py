# -*- coding: utf-8 -*-
"""把 Google 試算表的正式資料搬到資料庫（Neon Postgres），並逐筆比對。

只讀試算表、絕不寫回。詳細流程見 docs/specs/改用資料庫.md。

用法（目標預設讀 .env 的 NEON_DATABASE_URL；刻意不用 DATABASE_URL，避免本機 app 誤連正式資料庫）：
    python scripts/migrate_sheet_to_db.py                # 目標是空的才搬
    python scripts/migrate_sheet_to_db.py --replace      # 清空目標後重灌（正式切換時用）
    python scripts/migrate_sheet_to_db.py --verify-only  # 只比對試算表與資料庫是否一致
    python scripts/migrate_sheet_to_db.py --target postgresql://...
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
# 這支腳本自己管理連線，不讓 db.database 用 .env 的試算表模式（避免任何寫回試算表的可能）
os.environ["USE_GOOGLE_SHEET"] = "false"
os.environ.pop("DATABASE_URL", None)

from sqlalchemy import create_engine  # noqa: E402

import services.sheet_sync as ss  # noqa: E402
from db.database import normalize_database_url  # noqa: E402
from services.db_migration import (  # noqa: E402
    load_sheet_into_memory, copy_all, compare, report_ok, format_report,
)


def _forbid_sheet_write(*a, **k):
    raise RuntimeError("搬家腳本禁止寫回試算表")


def main() -> int:
    ap = argparse.ArgumentParser(description="Google 試算表 → 資料庫")
    ap.add_argument("--target", default=os.environ.get("NEON_DATABASE_URL"), help="目標資料庫連線字串")
    ap.add_argument("--replace", action="store_true", help="目標已有資料時清空重灌")
    ap.add_argument("--verify-only", action="store_true", help="只比對，不寫入")
    args = ap.parse_args()
    if not args.target:
        print("❌ 沒有目標資料庫：請在 .env 設 NEON_DATABASE_URL，或用 --target 指定。")
        return 2

    ss.sync_db_to_sheet = _forbid_sheet_write  # 保險：整支腳本不可能寫試算表

    dst = create_engine(normalize_database_url(args.target), pool_pre_ping=True)
    print(f"目標資料庫：{dst.url.render_as_string(hide_password=True)}")
    print("從 Google 試算表唯讀載入…")
    src = load_sheet_into_memory()

    if not args.verify_only:
        written = copy_all(src, dst, replace=args.replace)
        print("已寫入：" + "、".join(f"{k} {v} 筆" for k, v in written.items()))

    print("逐筆比對：")
    rep = compare(src, dst)
    print(format_report(rep))
    if report_ok(rep):
        print("✅ 試算表與資料庫完全一致。")
        return 0
    print("❌ 有差異，請勿切換；把上面的訊息給 Claude。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
