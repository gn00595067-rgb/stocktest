# -*- coding: utf-8 -*-
"""Google Sheet 同步：批次寫入（降低 write 請求數）與 429 配額重試／友善訊息。"""
import pytest
from sqlalchemy import create_engine, text

from db.models import Base
import services.sheet_sync as ss


class _FakeWS:
    def get_all_values(self):
        return []


class _FakeSpread:
    """記錄 write 呼叫次數；values_batch_update 可被測試替換成會 429 的版本。"""

    def __init__(self):
        self.clear_calls = 0
        self.update_calls = 0
        self.update_bodies = []

    def worksheet(self, title):
        return _FakeWS()

    def add_worksheet(self, title, rows, cols):
        return _FakeWS()

    def values_batch_clear(self, params=None, body=None):
        self.clear_calls += 1
        return {}

    def values_batch_update(self, body=None):
        self.update_calls += 1
        self.update_bodies.append(body)
        return {}


@pytest.fixture
def engine_with_data():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with engine.connect() as conn:
        conn.execute(text(
            "INSERT INTO trades (id,user,stock_id,trade_date,side,price,quantity,is_daytrade) "
            "VALUES (1,'Peggy','2330','2026-01-02','BUY',1000,1000,0)"
        ))
        conn.commit()
    return engine


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    import time
    monkeypatch.setattr(time, "sleep", lambda *a, **k: None)


def test_sync_uses_two_batched_writes(engine_with_data, monkeypatch):
    """整次同步只發 1 個 batch_clear + 1 個 batch_update（原本 5 表 ×2 ＝ 10 個）。"""
    fake = _FakeSpread()
    monkeypatch.setattr(ss, "_HAS_GSPREAD", True)
    monkeypatch.setattr(ss, "_open_spreadsheet", lambda: (fake, None))

    ok, err = ss.sync_db_to_sheet(engine_with_data)
    assert ok, err
    assert fake.clear_calls == 1
    assert fake.update_calls == 1
    # 5 張表打包在同一個 batch update
    assert len(fake.update_bodies[0]["data"]) == 5


def test_sync_retries_on_429_then_succeeds(engine_with_data, monkeypatch):
    fake = _FakeSpread()
    calls = {"n": 0}

    def flaky(body=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise Exception("APIError: [429]: Quota exceeded for quota metric 'Write requests'")
        return {}

    fake.values_batch_update = flaky
    monkeypatch.setattr(ss, "_HAS_GSPREAD", True)
    monkeypatch.setattr(ss, "_open_spreadsheet", lambda: (fake, None))

    ok, err = ss.sync_db_to_sheet(engine_with_data)
    assert ok, err
    assert calls["n"] == 2  # 第一次 429、重試後成功


def test_sync_persistent_429_returns_friendly_message(engine_with_data, monkeypatch):
    fake = _FakeSpread()

    def always_429(body=None):
        raise Exception("APIError: [429]: Quota exceeded")

    fake.values_batch_update = always_429
    monkeypatch.setattr(ss, "_HAS_GSPREAD", True)
    monkeypatch.setattr(ss, "_open_spreadsheet", lambda: (fake, None))

    ok, err = ss.sync_db_to_sheet(engine_with_data)
    assert not ok
    assert "配額" in err and "不會遺失" in err
