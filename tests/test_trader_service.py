# -*- coding: utf-8 -*-
"""買賣人名單服務：新增／刪除／列出／首次補齊（以記憶體 DB 隔離，不動真實資料庫）。"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import services.trader_service as ts
from db.models import Base, Trade


@pytest.fixture
def mem_session(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(ts, "get_session", lambda: Session())
    return Session


def test_add_list_delete(mem_session):
    assert ts.list_trader_names() == []

    ok, _ = ts.add_trader("Jonathan")
    assert ok
    ok, _ = ts.add_trader("Peggy")
    assert ok
    # 依建立順序（id）
    assert ts.list_trader_names() == ["Jonathan", "Peggy"]

    ok, msg = ts.delete_trader("Jonathan")
    assert ok
    assert ts.list_trader_names() == ["Peggy"]


def test_add_duplicate_rejected(mem_session):
    assert ts.add_trader("Jonathan")[0] is True
    ok, msg = ts.add_trader("Jonathan")
    assert ok is False
    assert "已存在" in msg


def test_add_blank_rejected(mem_session):
    ok, _ = ts.add_trader("   ")
    assert ok is False
    assert ts.list_trader_names() == []


def test_delete_missing_rejected(mem_session):
    ok, _ = ts.delete_trader("nobody")
    assert ok is False


def test_seed_from_existing_trades(mem_session):
    from datetime import date
    sess = mem_session()
    sess.add_all([
        Trade(user="Jonathan", stock_id="2330", trade_date=date(2026, 1, 2), side="BUY", price=1000, quantity=1000),
        Trade(user="Peggy", stock_id="2317", trade_date=date(2026, 1, 3), side="BUY", price=100, quantity=1000),
        Trade(user="Jonathan", stock_id="2454", trade_date=date(2026, 1, 4), side="BUY", price=900, quantity=1000),
    ])
    sess.commit()
    sess.close()

    ts.ensure_traders_seeded()
    assert sorted(ts.list_trader_names()) == ["Jonathan", "Peggy"]

    # 已有名單時再呼叫不應重複補
    ts.ensure_traders_seeded()
    assert sorted(ts.list_trader_names()) == ["Jonathan", "Peggy"]
