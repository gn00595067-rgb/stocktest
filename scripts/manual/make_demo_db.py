# -*- coding: utf-8 -*-
"""建立「示範資料庫」給操作說明截圖用：只有一個「示範帳戶」、幾檔常見股票、假交易，不含任何真實資料與人名。

用法（在專案根目錄）：
    python scripts/manual/make_demo_db.py scripts/manual/demo.db
股票主檔從正式資料庫唯讀複製（需 .env 的 NEON_DATABASE_URL）；登入密碼 demo1234（帳號「老闆」）。
價格參考值（Q）請依當時行情調整，畫面上的未實現損益才會合理。
"""
import os
import random
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pandas as pd
from dotenv import dotenv_values
from sqlalchemy import create_engine, text

from db.models import Base
from services.auth_service import hash_password
from services.trade_fees import fee_rate_for_discount, fees_for_trade

OUT = sys.argv[1] if len(sys.argv) > 1 else "demo.db"
TRADER = "示範帳戶"
Q = {"2330": 2420, "2317": 244, "2454": 4250, "2303": 133, "2603": 265.5, "2881": 161.5, "2412": 153, "0050": 109}
QTY = {"2330": 1000, "2317": 3000, "2454": 200, "2303": 5000, "2603": 2000, "2881": 3000, "2412": 2000, "0050": 5000}

random.seed(7)
if os.path.exists(OUT):
    os.remove(OUT)
dst = create_engine(f"sqlite:///{OUT}")
Base.metadata.create_all(dst)
src = create_engine(dotenv_values(".env")["NEON_DATABASE_URL"])
pd.read_sql("select * from stock_master", src).to_sql("stock_master", dst, if_exists="append", index=False)

today = date.today()
rate = fee_rate_for_discount(0.28)


def tick(p):
    t = 0.01 if p < 10 else 0.05 if p < 50 else 0.1 if p < 100 else 0.5 if p < 500 else 1 if p < 1000 else 5
    return round(round(p / t) * t, 2)


rows = []


def add(d, sid, side, px, q, dt=False):
    px = tick(px)
    f, x = fees_for_trade(side, px, q, is_etf=sid == "0050", is_daytrade=dt, fee_rate=rate)
    rows.append(dict(user=TRADER, stock_id=sid, trade_date=d, side=side, price=px, quantity=q,
                     is_daytrade=dt, fee=f, tax=x if side == "SELL" else 0.0, note=None))


weekdays = [today - timedelta(days=i) for i in range(1, 75) if (today - timedelta(days=i)).weekday() < 5]
for sid, q in Q.items():
    n = random.randint(2, 4)
    ds = sorted(random.sample(weekdays, n + 2))
    for d in ds[:n]:
        add(d, sid, "BUY", q * random.uniform(0.86, 1.02), QTY[sid])
    for d in ds[n:n + random.randint(0, 2)]:
        add(d, sid, "SELL", q * random.uniform(0.97, 1.08), QTY[sid])
# 今天幾筆（當日交易明細才有東西），含一組當沖
add(today, "2303", "BUY", 132.5, 5000)
add(today, "2881", "BUY", 161, 3000)
add(today, "2603", "SELL", 266, 2000)
add(today, "2317", "BUY", 240.5, 1000)
add(today, "2317", "SELL", 243.5, 1000, True)
rows.sort(key=lambda r: (r["trade_date"], r["side"] != "BUY"))
pd.DataFrame(rows).to_sql("trades", dst, if_exists="append", index=False)
with dst.begin() as c:
    c.execute(text("insert into traders (name, created_at) values (:n, '2026-08-01 00:00:00')"), {"n": TRADER})
    c.execute(text("insert into user_accounts (username,password_hash,role,is_active,created_at) "
                   "values ('老闆',:h,'admin',1,'2026-08-01 00:00:00')"), {"h": hash_password("demo1234")})
print(f"已建立 {OUT}：{len(rows)} 筆示範交易")
