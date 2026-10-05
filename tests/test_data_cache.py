# -*- coding: utf-8 -*-
"""讀取快取：資料沒變就不重讀；任何存檔（commit）後一定重讀，不能讓人看到舊資料。"""
from datetime import date

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import db.database as dbm
import services.data_cache as dc
from db.models import Base, Trade


def test_cache_hits_until_commit(monkeypatch):
    eng = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    S = sessionmaker(bind=eng)
    monkeypatch.setattr(dbm, "get_session", S)
    dc.clear()
    selects = []
    event.listen(eng, "before_cursor_execute", lambda *a: selects.append(a[2]))

    t1, _, _ = dc.load_trades_rules_masters()
    n_after_first = len(selects)
    t2, _, _ = dc.load_trades_rules_masters()
    assert len(selects) == n_after_first          # 第二次完全沒查資料庫
    assert t1 == [] and t2 == []

    s = S()
    s.add(Trade(user="雅雲姐", stock_id="2330", trade_date=date(2026, 10, 5), side="BUY", price=1, quantity=1))
    s.commit(); s.close()                         # 任何存檔 → 資料版本 +1
    t3, _, _ = dc.load_trades_rules_masters()
    assert len(t3) == 1                           # 一定讀到新資料
    dc.clear()
