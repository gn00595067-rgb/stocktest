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


def test_all_trader_names_union_master_and_trades(mem_session):
    """主檔 ∪ 交易出現過：含『剛新增、還沒任何交易』的買賣人，且去重排序。"""
    from datetime import date
    # 交易裡有 Peggy、王小姐；主檔另外新增了還沒交易的雅雲姐
    sess = mem_session()
    sess.add_all([
        Trade(user="Peggy", stock_id="2330", trade_date=date(2026, 1, 2), side="BUY", price=1000, quantity=1000),
        Trade(user="王小姐", stock_id="2317", trade_date=date(2026, 1, 3), side="BUY", price=100, quantity=1000),
    ])
    sess.commit()
    sess.close()
    ts.add_trader("雅雲姐")  # 只在主檔，還沒任何交易

    names = ts.all_trader_names()
    # 三個都要在（含還沒交易的雅雲姐），排序去重
    assert names == sorted({"Peggy", "王小姐", "雅雲姐"})


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


def test_rename_trader_moves_everything(mem_session):
    """Elsa 2026-11-04：陳小姐＝美美，同一個人改名，交易、分頁、折數、質押、書號全部跟著走。"""
    from datetime import date
    from sqlalchemy import text
    from db.models import PortfolioGroup, Trader, UserAccount, UserTraderBinding
    from services.pledge_service import pledges
    from services.trade_ref_service import trade_refs

    s = mem_session()
    s.add(Trader(name="陳小姐", fee_discount=0.25))
    s.add(Trader(name="Peggy姐"))
    for i, u in enumerate(["陳小姐", "陳小姐", "Peggy姐"]):
        s.add(Trade(user=u, stock_id="2303", trade_date=date(2026, 11, 4), side="BUY", price=100, quantity=1000))
    s.add(UserAccount(id=1, username="老闆", password_hash="x", role="admin"))
    s.add(UserTraderBinding(user_id=1, trader_name="陳小姐"))
    s.add(PortfolioGroup(owner="老闆", trader="陳小姐", name="陳小姐"))
    s.add(PortfolioGroup(owner="老闆", trader="陳小姐", name="短線"))
    s.commit()
    pledges.metadata.create_all(s.connection())
    trade_refs.metadata.create_all(s.connection())
    s.execute(text("INSERT INTO pledges (trader, stock_id, shares, updated_at) VALUES ('陳小姐','2303',500,'2026-11-04')"))
    s.execute(text('INSERT INTO trade_refs ("user", trade_date, order_no, trade_id, source, created_at) '
                   "VALUES ('陳小姐','2026-11-04','A1',1,'import','2026-11-04')"))
    s.commit()
    s.close()

    ok, msg = ts.rename_trader("陳小姐", "美美")
    assert ok, msg
    s = mem_session()
    assert sorted(t.user for t in s.query(Trade)) == ["Peggy姐", "美美", "美美"]
    assert s.query(Trader).filter_by(name="美美").one().fee_discount == 0.25
    assert s.query(Trader).filter_by(name="陳小姐").first() is None
    assert [b.trader_name for b in s.query(UserTraderBinding)] == ["美美"]
    assert sorted((g.trader, g.name) for g in s.query(PortfolioGroup)) == [("美美", "短線"), ("美美", "美美")]
    assert s.execute(text("SELECT trader FROM pledges")).scalar() == "美美"
    assert s.execute(text('SELECT "user" FROM trade_refs')).scalar() == "美美"


def test_rename_trader_rejects_existing_or_blank(mem_session):
    ts.add_trader("陳小姐")
    ts.add_trader("Peggy姐")
    assert ts.rename_trader("陳小姐", "Peggy姐")[0] is False   # 合併不是改名
    assert ts.rename_trader("陳小姐", "  ")[0] is False
    assert ts.rename_trader("陳小姐", "陳小姐")[0] is False
    # 沒有質押／書號表也能改（第一次用到才建的表）
    assert ts.rename_trader("陳小姐", "美美")[0] is True
    assert ts.list_trader_names() == ["美美", "Peggy姐"]
