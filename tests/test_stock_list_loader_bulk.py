# -*- coding: utf-8 -*-
"""主檔寫入不能逐筆查詢：換成雲端資料庫後每次查詢都是一次網路來回（3000 檔會卡好幾分鐘）。"""
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import db.database as dbm
from db.models import Base, StockMaster
from services.stock_list_loader import write_to_stock_master


def _items(n, name="名稱"):
    return [{"stock_id": f"{i:04d}", "name": f"{name}{i}", "industry_name": "電子", "market": "TW",
             "exchange": "TWSE", "is_etf": False} for i in range(n)]


def test_write_uses_few_queries(monkeypatch):
    eng = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    monkeypatch.setattr(dbm, "get_session", sessionmaker(bind=eng))
    assert write_to_stock_master(_items(300)) == (True, None)

    stmts = []
    event.listen(eng, "before_cursor_execute", lambda *a: stmts.append(a[2]))
    items = _items(300)
    items[5]["name"] = "改名"
    assert write_to_stock_master(items) == (True, None)
    selects = [s for s in stmts if s.lstrip().upper().startswith("SELECT")]
    updates = [s for s in stmts if s.lstrip().upper().startswith("UPDATE")]
    assert len(selects) == 1          # 一次撈全部，不是 300 次
    assert len(updates) == 1          # 只有真的改名的那一檔
    s = sessionmaker(bind=eng)()
    assert s.get(StockMaster, "0005").name == "改名"
    assert s.query(StockMaster).count() == 300
