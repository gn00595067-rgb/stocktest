# -*- coding: utf-8 -*-
"""種子資料：2330, 2317, 3706 等"""
from .database import get_session
from .models import StockMaster

SEED_STOCKS = [
    {"stock_id": "2330", "name": "台積電", "industry_name": "半導體", "market": "TW", "exchange": "TWSE", "is_etf": False},
    {"stock_id": "2317", "name": "鴻海", "industry_name": "電腦及週邊", "market": "TW", "exchange": "TWSE", "is_etf": False},
    {"stock_id": "3706", "name": "神達", "industry_name": "電腦及週邊", "market": "TW", "exchange": "TWSE", "is_etf": False},
    {"stock_id": "2454", "name": "聯發科", "industry_name": "半導體", "market": "TW", "exchange": "TWSE", "is_etf": False},
    {"stock_id": "2881", "name": "富邦金", "industry_name": "金控", "market": "TW", "exchange": "TWSE", "is_etf": False},
]


def run_seed():
    session = get_session()
    try:
        for s in SEED_STOCKS:
            st = session.query(StockMaster).filter(StockMaster.stock_id == s["stock_id"]).first()
            if not st:
                session.add(StockMaster(**s))
        session.commit()
    finally:
        session.close()
