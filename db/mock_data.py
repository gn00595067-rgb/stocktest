# -*- coding: utf-8 -*-
"""開發用：一鍵產生大量模擬交易，方便預覽介面。正式版可移除此檔及主檔設定中的按鈕。"""
import random
from datetime import date, timedelta
from db.database import get_session
from db.models import Trade, StockMaster
from db.seed_data import SEED_STOCKS

# 模擬用買賣人
MOCK_USERS = ["張三", "李四", "王五", "趙六", "陳七"]

# 股票代號與參考價（供隨機波動）
STOCK_PRICES = {"2330": 580, "2317": 105, "3706": 52, "2454": 920, "2881": 68}


def generate_mock_trades(
    num_trades: int = 300,
    start_date: date = None,
    end_date: date = None,
    seed: int = 42,
) -> int:
    """
    產生 num_trades 筆模擬交易寫入 trades 表。
    回傳實際寫入筆數。
    """
    random.seed(seed)
    end_date = end_date or date.today()
    start_date = start_date or (end_date - timedelta(days=90))
    if start_date > end_date:
        start_date, end_date = end_date, start_date

    session = get_session()
    stocks = session.query(StockMaster).all()
    stock_ids = [s.stock_id for s in stocks] if stocks else list(STOCK_PRICES.keys())
    if not stock_ids:
        for s in SEED_STOCKS:
            session.add(StockMaster(**s))
        session.flush()
        stock_ids = [s["stock_id"] for s in SEED_STOCKS]

    base_prices = STOCK_PRICES.copy()
    for sid in stock_ids:
        if sid not in base_prices:
            base_prices[sid] = 50 + random.randint(10, 500)
    count = 0
    total_days = (end_date - start_date).days + 1
    for _ in range(num_trades):
        stock_id = random.choice(stock_ids)
        base = base_prices[stock_id]
        # 模擬時「買」多於「賣」，庫存才會明顯
        side = random.choice(["BUY", "BUY", "BUY", "SELL", "SELL"])
        # 買價偏低位、賣價偏高位，讓損益較平衡（否則隨機同區間易造成虧損嚴重）
        if side == "BUY":
            price = round(base * (0.93 + random.random() * 0.10), 2)   # 93%～103%
        else:
            price = round(base * (0.98 + random.random() * 0.14), 2)   # 98%～112%
        quantity = random.choice([1000, 2000, 3000, 5000, 10000])
        user = random.choice(MOCK_USERS)
        trade_date = start_date + timedelta(days=random.randint(0, max(0, total_days - 1)))
        is_daytrade = random.random() < 0.15
        note = "模擬" if random.random() < 0.3 else None
        t = Trade(
            user=user,
            stock_id=stock_id,
            trade_date=trade_date,
            side=side,
            price=price,
            quantity=quantity,
            is_daytrade=is_daytrade,
            note=note,
        )
        session.add(t)
        count += 1

    try:
        session.commit()
        return count
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
