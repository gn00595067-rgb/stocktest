# -*- coding: utf-8 -*-
"""危險操作保護：正式資料判斷、刪除前備份（失敗就不准刪）。"""
import db.database as dbm
import services.data_guard as dg
import services.sheet_sync as ss


def test_is_production_data(monkeypatch):
    monkeypatch.setattr(dbm, "DATABASE_URL", None)
    monkeypatch.setattr(dbm, "USE_GOOGLE_SHEET", False)
    assert dg.is_production_data() is False          # 本機開發資料庫
    monkeypatch.setattr(dbm, "USE_GOOGLE_SHEET", True)
    assert dg.is_production_data() is True           # 試算表模式
    monkeypatch.setattr(dbm, "USE_GOOGLE_SHEET", False)
    monkeypatch.setattr(dbm, "DATABASE_URL", "postgresql://x")
    assert dg.is_production_data() is True           # 資料庫模式


def test_backup_not_needed_without_database(monkeypatch):
    monkeypatch.setattr(dbm, "DATABASE_URL", None)
    assert dg.backup_before_destructive() == (True, None)


def test_backup_failure_blocks_delete(monkeypatch):
    monkeypatch.setattr(dbm, "DATABASE_URL", "postgresql://x")
    monkeypatch.setattr(ss, "is_google_sheet_enabled", lambda: True)
    monkeypatch.setattr(ss, "sync_db_to_sheet", lambda *a, **k: (False, "429 配額"))
    ok, err = dg.backup_before_destructive()
    assert ok is False and "取消刪除" in err and "429" in err


def test_backup_without_credentials_blocks_delete(monkeypatch):
    monkeypatch.setattr(dbm, "DATABASE_URL", "postgresql://x")
    monkeypatch.setattr(ss, "is_google_sheet_enabled", lambda: False)
    ok, err = dg.backup_before_destructive()
    assert ok is False and "取消刪除" in err


def test_backup_success_allows_delete(monkeypatch):
    called = {}
    monkeypatch.setattr(dbm, "DATABASE_URL", "postgresql://x")
    monkeypatch.setattr(ss, "is_google_sheet_enabled", lambda: True)
    monkeypatch.setattr(ss, "sync_db_to_sheet", lambda eng, force=False: called.setdefault("force", force) and (True, None) or (True, None))
    assert dg.backup_before_destructive() == (True, None)
    assert called["force"] is True                   # 一定要強制寫，不能因「內容沒變」略過
