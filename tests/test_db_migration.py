# -*- coding: utf-8 -*-
"""試算表 → 資料庫轉移：複製、逐筆比對、拒絕覆蓋、建表規則。

預設用 SQLite 跑；設環境變數 TEST_PG_URL（空的 Postgres 資料庫）會另外在真的 Postgres 上跑，
驗證 "user" 保留字、外鍵、文字長度、自動編號等 Postgres 才有的差異。
"""
import os
from datetime import date, datetime

import pytest
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.pool import StaticPool

from db.database import create_schema
from db.models import Base
import services.db_migration as dm
import services.sheet_sync as ss

PG_URL = os.environ.get("TEST_PG_URL")


def _mem():
    e = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(e)
    return e


def _seed(e):
    with e.begin() as c:
        c.execute(text("INSERT INTO traders (id, name, created_at) VALUES (1, 'Peggy姐', '2026-01-01 00:00:00')"))
        c.execute(text(
            "INSERT INTO user_accounts (id, username, password_hash, role, is_active, created_at) "
            "VALUES (1, 'admin', 'h', 'admin', 1, '2026-01-01 00:00:00')"
        ))
        for i, side in [(1, "BUY"), (2, "SELL"), (5, "BUY")]:
            c.execute(text(
                'INSERT INTO trades (id, "user", stock_id, trade_date, side, price, quantity, is_daytrade, fee, tax, note) '
                f"VALUES ({i}, 'Peggy姐', '2330', '2026-01-02', '{side}', 1000.5, 1000, 0, 1.5, NULL, '備註')"
            ))
        # 失效規則：指向不存在的交易 99（正式資料裡有 26 條這種）
        c.execute(text("INSERT INTO custom_match_rules (sell_trade_id, buy_trade_id, matched_qty) VALUES (2, 1, 1000)"))
        c.execute(text("INSERT INTO custom_match_rules (sell_trade_id, buy_trade_id, matched_qty) VALUES (2, 99, 10)"))


@pytest.fixture(params=["sqlite"] + (["postgres"] if PG_URL else []))
def target(request):
    if request.param == "sqlite":
        e = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
        yield e
        return
    e = create_engine(PG_URL)
    with e.begin() as c:
        for t in reversed(Base.metadata.sorted_tables):
            c.execute(text(f'DROP TABLE IF EXISTS "{t.name}" CASCADE'))
    yield e
    with e.begin() as c:
        for t in reversed(Base.metadata.sorted_tables):
            c.execute(text(f'DROP TABLE IF EXISTS "{t.name}" CASCADE'))


def test_copy_then_identical(target):
    src = _mem()
    _seed(src)
    written = dm.copy_all(src, target)
    assert written["trades"] == 3 and written["custom_match_rules"] == 2
    rep = dm.compare(src, target)
    assert dm.report_ok(rep), dm.format_report(rep)


def test_refuses_non_empty_target_without_replace(target):
    src = _mem()
    _seed(src)
    dm.copy_all(src, target)
    with pytest.raises(RuntimeError, match="已有資料"):
        dm.copy_all(src, target)
    # --replace：清空重灌後仍一致
    dm.copy_all(src, target, replace=True)
    assert dm.report_ok(dm.compare(src, target))


def test_compare_detects_new_trade(target):
    """切換期間有人在舊模式記帳 → --verify-only 要抓得到。"""
    src = _mem()
    _seed(src)
    dm.copy_all(src, target)
    with src.begin() as c:
        c.execute(text(
            'INSERT INTO trades (id, "user", stock_id, trade_date, side, price, quantity, is_daytrade) '
            "VALUES (6, 'Peggy姐', '2330', '2026-01-03', 'BUY', 1, 1, 0)"
        ))
    rep = dm.compare(src, target)
    assert not dm.report_ok(rep)
    assert rep["trades"]["src"] == 4 and rep["trades"]["dst"] == 3
    assert rep["trades"]["only_src"][0][0] == 6


def test_new_ids_continue_after_max(target):
    """搬完後新增交易，id 要接在最大 id（5）之後，不能撞號。"""
    src = _mem()
    _seed(src)
    dm.copy_all(src, target)
    with target.begin() as c:
        c.execute(text(
            'INSERT INTO trades ("user", stock_id, trade_date, side, price, quantity, is_daytrade) '
            "VALUES ('x', '2330', '2026-01-03', 'BUY', 1, 1, false)"
        ))
        new_id = c.execute(text("SELECT MAX(id) FROM trades")).scalar()
    assert new_id == 6


def test_export_payload_reads_trader_not_db_login(target):
    """寫回試算表的 SQL 讀 "user" 欄：Postgres 沒加引號會讀成資料庫登入帳號（如 postgres）。"""
    src = _mem()
    _seed(src)
    dm.copy_all(src, target)
    r_trades, sheets = ss._read_db_payload(target)
    assert {r[1] for r in r_trades} == {"Peggy姐"}
    assert sheets[0][1][1][1] == "Peggy姐"


def test_long_stock_id_allowed(target):
    """主檔有超過 20 字的代號（類股指數），SQLite 一直存得進去，換 Postgres 也要能存。"""
    create_schema(target)
    with target.begin() as c:
        c.execute(text(
            "INSERT INTO stock_master (stock_id, name, market, exchange, is_etf) "
            "VALUES ('BiotechnologyMedicalCare', '生技醫療類指數', 'TW', 'TWSE', false)"
        ))


def test_schema_has_no_foreign_keys_on_postgres(target):
    create_schema(target)
    if target.dialect.name == "sqlite":
        pytest.skip("SQLite 本來就不檢查外鍵")
    insp = inspect(target)
    assert all(not insp.get_foreign_keys(t) for t in insp.get_table_names())


def test_database_url_disables_sheet_mode(monkeypatch):
    """設了 DATABASE_URL 就不能再把試算表當正式資料（兩邊互相覆蓋）。"""
    import importlib
    import db.database as dbmod
    monkeypatch.setenv("USE_GOOGLE_SHEET", "true")
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    try:
        mod = importlib.reload(dbmod)
        assert mod.USE_GOOGLE_SHEET is False
        assert mod.DATABASE_URL == "sqlite://"
    finally:
        monkeypatch.delenv("DATABASE_URL")
        monkeypatch.delenv("USE_GOOGLE_SHEET")
        importlib.reload(dbmod)


def test_normalize_database_url():
    from db.database import normalize_database_url
    assert normalize_database_url("postgres://a@b/c") == "postgresql://a@b/c"
    assert normalize_database_url(" postgresql://a@b/c ") == "postgresql://a@b/c"
