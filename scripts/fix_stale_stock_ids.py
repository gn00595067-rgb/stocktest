# -*- coding: utf-8 -*-
"""一次性修正：控股改組後的舊代號（2026-11-18，Jonathan 同意）。

- 神達 2315 → 3706：3 筆交易記在舊代號（Peggy 搜「神達」選到舊的），連帶分頁／質押／模擬一起搬（migrate_stock_ids）。
- 從主檔刪掉已不交易的舊代號：2315 神達、9915 億豐、1716 永信、2463 研揚、2336 致伸（新代號 3706／8464／3705／6579／4915 都在）。

用法：
  python scripts/fix_stale_stock_ids.py --target <DATABASE_URL>           # 乾跑：只印會改什麼
  python scripts/fix_stale_stock_ids.py --target <DATABASE_URL> --apply   # 真的寫入（同一個交易，先備份到 backups/）
"""
import argparse
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MOVE = {"2315": "3706"}
STALE = {"2315": "3706", "9915": "8464", "1716": "3705", "2463": "6579", "2336": "4915"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=True)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from services.stock_id_fix import migrate_stock_ids

    url = a.target.replace("postgres://", "postgresql://", 1)
    eng = create_engine(url)
    sess = sessionmaker(bind=eng)()
    host = eng.url.host
    print(f"目標：{host}（{'寫入' if a.apply else '乾跑'}）")
    try:
        old_ids = list(STALE)
        trades = [dict(r._mapping) for r in sess.execute(
            text('SELECT id, "user", stock_id, trade_date, side, price, quantity FROM trades WHERE stock_id = ANY(:ids) ORDER BY id'),
            {"ids": old_ids})]
        masters = [dict(r._mapping) for r in sess.execute(
            text("SELECT stock_id, name, exchange FROM stock_master WHERE stock_id = ANY(:ids) ORDER BY stock_id"),
            {"ids": old_ids + list(STALE.values())})]
        groups = [dict(r._mapping) for r in sess.execute(
            text("SELECT id, owner, name, stock_ids FROM portfolio_groups WHERE stock_ids IS NOT NULL"))]
        groups = [g for g in groups if set(p.strip() for p in str(g["stock_ids"]).split(",")) & set(old_ids)]
        print("舊代號上的交易：", trades)
        print("主檔（舊＋新）：", masters)
        print("分頁含舊代號：", groups)
        new_present = {m["stock_id"] for m in masters}
        missing = [n for n in STALE.values() if n not in new_present]
        if missing:
            print("新代號不在主檔，停止：", missing)
            return 1
        unexpected = [t for t in trades if t["stock_id"] not in MOVE]
        if unexpected:
            print("有預期外的舊代號交易，停止（需人工判斷）：", unexpected)
            return 1
        if not a.apply:
            print("乾跑結束，未寫入。")
            return 0

        os.makedirs("backups", exist_ok=True)
        bk = os.path.join("backups", f"stale_ids_{host.split('.')[0]}_{datetime.now():%Y%m%d_%H%M%S}.json")
        with open(bk, "w", encoding="utf-8") as f:
            json.dump(dict(trades=trades, masters=masters, groups=groups), f, ensure_ascii=False, default=str, indent=1)
        print("已備份：", bk)

        counts = migrate_stock_ids(sess, MOVE)
        deleted = sess.execute(text("DELETE FROM stock_master WHERE stock_id = ANY(:ids)"), {"ids": old_ids}).rowcount
        sess.commit()
        print("搬移：", counts, "；刪主檔舊代號：", deleted)
        return 0
    except Exception:
        sess.rollback()
        raise
    finally:
        sess.close()


if __name__ == "__main__":
    sys.exit(main())
