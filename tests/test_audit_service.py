# -*- coding: utf-8 -*-
"""操作紀錄（舊系統寫入）：快照格式同新系統 Snapshot、刪除與新增都留紀錄。"""
import json
from datetime import date

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from db.models import Base, CustomMatchRule, Trade
from services import audit_service as audit


def _sess():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)()


def test_snapshot_and_log_delete():
    s = _sess()
    b = Trade(user="11/17", stock_id="2303", trade_date=date(2026, 11, 23), side="BUY", price=84.8, quantity=1000, fee=30)
    sl = Trade(user="11/17", stock_id="2303", trade_date=date(2026, 11, 23), side="SELL", price=88.3, quantity=1000,
               fee=31, tax=132, is_daytrade=True)
    s.add_all([b, sl]); s.flush()
    s.add(CustomMatchRule(sell_trade_id=sl.id, buy_trade_id=b.id, matched_qty=1000)); s.flush()
    snap = audit.snapshot_trades(s, [sl.id])[0]
    assert snap["kind"] == "deleted"
    assert snap["trade"] == {"id": sl.id, "user": "11/17", "stockId": "2303", "tradeDate": "2026-11-23", "side": "SELL",
                             "price": 88.3, "quantity": 1000, "isDaytrade": True, "fee": 31.0, "tax": 132.0, "note": None}
    assert [(r["s"], r["b"], r["q"]) for r in snap["rules"]] == [(sl.id, b.id, 1000)]
    audit.log(s, "delete", "舊系統・仿Yahoo", before=snap, actor="老闆", note="測試")
    s.commit()
    row = s.execute(text("select actor, source, action, trade_id, trader, stock_id, trade_date, before, note from trade_audit")).one()
    assert row[:7] == ("老闆", "舊系統・仿Yahoo", "delete", sl.id, "11/17", "2303", "2026-11-23")
    assert json.loads(row[7])["trade"]["id"] == sl.id and row[8] == "測試"


def test_log_add_without_before():
    s = _sess()
    audit.log(s, "add", "舊系統・仿Yahoo", trade={"id": 9, "user": "A", "stockId": "2330", "tradeDate": "2026-11-25"},
              after={"side": "BUY", "quantity": 100, "price": 2150.0}, actor="老闆")
    s.commit()
    row = s.execute(text("select action, trade_id, before, after from trade_audit")).one()
    assert row[0] == "add" and row[1] == 9 and row[2] is None and json.loads(row[3])["quantity"] == 100
