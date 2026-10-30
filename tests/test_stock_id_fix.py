# -*- coding: utf-8 -*-
"""ETF 代號掉 0（0056→56、006208→6208）的修正與資料搬移。"""
import json
from datetime import date

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import db.database as dbm
from db.models import Base, Cashflow, PortfolioGroup, StockMaster, Trade
from services.stock_id_fix import normalize_stock_id
from services.stock_list_loader import _parse_row_to_item, write_to_stock_master


def test_normalize_rules():
    assert normalize_stock_id("56", True) == "0056"
    assert normalize_stock_id("878", True) == "00878"
    assert normalize_stock_id("6208", True) == "006208"     # 富邦台50
    assert normalize_stock_id("6208", False) == "6208"      # 日揚科技（個股不動）
    assert normalize_stock_id("0056", True) == "0056"       # 試算表改成文字後不會再補一次
    assert normalize_stock_id("00878", True) == "00878"
    assert normalize_stock_id("00981A", True) == "00981A"
    assert normalize_stock_id("2330", False) == "2330"


def test_parse_row_from_sheet():
    assert _parse_row_to_item({"stock_id": "56", "name": "元大高股息", "is_etf": "TRUE"})["stock_id"] == "0056"
    assert _parse_row_to_item({"stock_id": "6208", "name": "日揚", "is_etf": "FALSE"})["stock_id"] == "6208"


def _item(sid, name, etf):
    return {"stock_id": sid, "name": name, "industry_name": "", "market": "TW", "exchange": "TWSE", "is_etf": etf}


def test_master_sync_migrates_old_short_ids(monkeypatch):
    eng = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    S = sessionmaker(bind=eng)
    monkeypatch.setattr(dbm, "get_session", S)
    s = S()
    s.add_all([
        StockMaster(stock_id="56", name="元大高股息", is_etf=True),
        StockMaster(stock_id="6208", name="富邦台50", is_etf=True),
        StockMaster(stock_id="81", name="已下市", is_etf=True),     # 這次沒送新代號進來 → 不動
        StockMaster(stock_id="2330", name="台積電", is_etf=False),
        Trade(user="Peggy姐", stock_id="56", trade_date=date(2026, 10, 1), side="BUY", price=36, quantity=1000),
        Trade(user="Peggy姐", stock_id="6208", trade_date=date(2026, 10, 1), side="BUY", price=120, quantity=1000),
        Trade(user="Peggy姐", stock_id="2330", trade_date=date(2026, 10, 1), side="BUY", price=1000, quantity=1000),
        Cashflow(user="Peggy姐", stock_id="56", date=date(2026, 10, 2), type="DIVIDEND", amount=500),
        PortfolioGroup(trader="Peggy姐", name="存股", stock_ids="56,2330"),
    ])
    s.commit()
    s.execute(text("CREATE TABLE pledges (trader VARCHAR, stock_id VARCHAR, shares INTEGER, updated_at DATETIME)"))
    s.execute(text("INSERT INTO pledges VALUES ('Peggy姐', '56', 500, '2026-10-01')"))
    s.execute(text("CREATE TABLE sim_states (owner VARCHAR, data TEXT, updated_at DATETIME)"))
    s.execute(text("INSERT INTO sim_states VALUES ('老闆', :d, '2026-10-01')"),
              {"d": json.dumps({"inputs": {"56": [1, 2], "2330": [3, 4]}})})
    s.commit()
    s.close()

    items = [_item("0056", "元大高股息", True), _item("006208", "富邦台50", True),
             _item("6208", "日揚", False), _item("2330", "台積電", False)]
    assert write_to_stock_master(items) == (True, None)

    s = S()
    ids = {m.stock_id: m.name for m in s.query(StockMaster).all()}
    assert ids == {"0056": "元大高股息", "006208": "富邦台50", "6208": "日揚", "81": "已下市", "2330": "台積電"}
    assert sorted(t.stock_id for t in s.query(Trade).all()) == ["0056", "006208", "2330"]
    assert s.query(Cashflow).one().stock_id == "0056"
    assert s.query(PortfolioGroup).one().stock_ids == "0056,2330"
    assert s.execute(text("SELECT stock_id FROM pledges")).scalar() == "0056"
    assert set(json.loads(s.execute(text("SELECT data FROM sim_states")).scalar())["inputs"]) == {"0056", "2330"}

    # 第二次同步：沒有舊代號了，什麼都不改
    s.close()
    assert write_to_stock_master(items) == (True, None)
    assert S().query(Trade).filter(Trade.stock_id == "6208").count() == 0
