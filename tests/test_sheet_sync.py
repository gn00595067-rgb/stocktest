# -*- coding: utf-8 -*-
"""Google Sheet 同步：批次寫入、429 重試、防呆（逐筆 id）、回讀驗證、自動備份。"""
import pytest
import gspread
from sqlalchemy import create_engine, text

from db.models import Base
import services.sheet_sync as ss


class _FakeWS:
    """假工作表：記住寫入的內容，供回讀驗證與備份測試。"""

    def __init__(self, title, values=None):
        self.title = title
        self.values = [list(r) for r in (values or [])]

    def get_all_values(self):
        return [list(r) for r in self.values]

    def clear(self):
        self.values = []

    def update(self, data, value_input_option=None):
        self.values = [list(r) for r in data]


class _FakeSpread:
    def __init__(self, preset=None):
        # 預先建立 5 張主表（空），backup 表故意不建（測試 add_worksheet 路徑）
        self.wss = {}
        for t in ["trades", "custom_match_rules", "user_accounts",
                  "user_trader_bindings", "traders"]:
            self.wss[t] = _FakeWS(t, (preset or {}).get(t))
        self.batch_update_calls = 0
        self.batch_clear_calls = 0

    def worksheet(self, title):
        if title not in self.wss:
            raise gspread.WorksheetNotFound(title)
        return self.wss[title]

    def add_worksheet(self, title, rows, cols):
        ws = _FakeWS(title)
        self.wss[title] = ws
        return ws

    def values_batch_update(self, body=None):
        self.batch_update_calls += 1
        for d in body["data"]:
            title = d["range"].split("!")[0]
            self.wss.setdefault(title, _FakeWS(title)).values = [list(r) for r in d["values"]]
        return {}

    def values_batch_clear(self, params=None, body=None):
        self.batch_clear_calls += 1
        return {}


def _mk_engine(n=1):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with engine.connect() as conn:
        for i in range(1, n + 1):
            conn.execute(text(
                "INSERT INTO trades (id,user,stock_id,trade_date,side,price,quantity,is_daytrade) "
                f"VALUES ({i},'Peggy','2330','2026-01-02','BUY',1000,1000,0)"
            ))
        conn.commit()
    return engine


@pytest.fixture
def engine_with_data():
    return _mk_engine(1)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    import time
    monkeypatch.setattr(time, "sleep", lambda *a, **k: None)


def test_sync_uses_two_batched_writes(engine_with_data, monkeypatch):
    fake = _FakeSpread()
    monkeypatch.setattr(ss, "_HAS_GSPREAD", True)
    monkeypatch.setattr(ss, "_open_spreadsheet", lambda: (fake, None))

    ok, err = ss.sync_db_to_sheet(engine_with_data)
    assert ok, err
    assert fake.batch_update_calls == 1   # 主寫入 1 個 batch
    assert fake.batch_clear_calls == 1     # 修剪 1 個 batch


def test_retries_on_429_then_succeeds(engine_with_data, monkeypatch):
    fake = _FakeSpread()
    calls = {"n": 0}
    orig = fake.values_batch_update

    def flaky(body=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise Exception("APIError: [429]: Quota exceeded for quota metric 'Write requests'")
        return orig(body)

    fake.values_batch_update = flaky
    monkeypatch.setattr(ss, "_HAS_GSPREAD", True)
    monkeypatch.setattr(ss, "_open_spreadsheet", lambda: (fake, None))

    ok, err = ss.sync_db_to_sheet(engine_with_data)
    assert ok, err
    assert calls["n"] == 2


def test_persistent_429_returns_friendly_message(engine_with_data, monkeypatch):
    fake = _FakeSpread()
    fake.values_batch_update = lambda body=None: (_ for _ in ()).throw(Exception("APIError: [429]: Quota exceeded"))
    monkeypatch.setattr(ss, "_HAS_GSPREAD", True)
    monkeypatch.setattr(ss, "_open_spreadsheet", lambda: (fake, None))

    ok, err = ss.sync_db_to_sheet(engine_with_data)
    assert not ok
    assert "配額" in err and "不會遺失" in err


def test_guard_aborts_on_partial_loss(engine_with_data, monkeypatch):
    """試算表已有 100 筆、記憶體只剩 1 筆 → 中止，訊息列出少了哪些 id。"""
    preset_trades = [ss.TRADES_HEADERS] + [
        [i, "Peggy", "2330", "2026-01-02", "BUY", 1000, 1000, False, "", "", ""]
        for i in range(1, 101)
    ]
    fake = _FakeSpread(preset={"trades": preset_trades})
    monkeypatch.setattr(ss, "_HAS_GSPREAD", True)
    monkeypatch.setattr(ss, "_open_spreadsheet", lambda: (fake, None))

    ok, err = ss.sync_db_to_sheet(engine_with_data)  # 記憶體只有 1 筆
    assert not ok
    assert "已中止寫回以保護資料" in err
    assert "少了" in err
    assert fake.batch_update_calls == 0  # 沒有真的寫入（被擋下）


def test_normal_small_delete_allowed(monkeypatch):
    """正常小量刪除（20 筆→18 筆）不該被防呆擋。"""
    preset_trades = [ss.TRADES_HEADERS] + [
        [i, "Peggy", "2330", "2026-01-02", "BUY", 1000, 1000, False, "", "", ""]
        for i in range(1, 21)
    ]
    fake = _FakeSpread(preset={"trades": preset_trades})
    monkeypatch.setattr(ss, "_HAS_GSPREAD", True)
    monkeypatch.setattr(ss, "_open_spreadsheet", lambda: (fake, None))

    ok, err = ss.sync_db_to_sheet(_mk_engine(18))
    assert ok, err
    assert fake.batch_update_calls == 1


def test_auto_backup_written(engine_with_data, monkeypatch):
    """健康同步後，trades_backup 工作表要有資料。"""
    fake = _FakeSpread()
    monkeypatch.setattr(ss, "_HAS_GSPREAD", True)
    monkeypatch.setattr(ss, "_open_spreadsheet", lambda: (fake, None))

    ok, err = ss.sync_db_to_sheet(engine_with_data)
    assert ok, err
    assert ss.SHEET_TRADES_BACKUP in fake.wss
    bak = fake.wss[ss.SHEET_TRADES_BACKUP].get_all_values()
    assert len(bak) - 1 == 1  # 表頭 + 1 筆交易
