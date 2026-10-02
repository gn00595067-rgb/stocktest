# -*- coding: utf-8 -*-
"""Google Sheet 同步：批次寫入、429 重試、防呆（逐筆 id）、回讀驗證、自動備份。"""
import pytest
import gspread
from sqlalchemy import create_engine, text

from db.models import Base
import services.sheet_sync as ss


class _FakeWS:
    """假工作表：記住寫入的內容，供回讀驗證與備份測試。"""

    _next_id = 1

    def __init__(self, title, values=None):
        self.title = title
        self.id = _FakeWS._next_id
        _FakeWS._next_id += 1
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
        self.values_get_calls = 0
        self.worksheets_calls = 0
        self.duplicate_calls = 0

    def worksheet(self, title):
        if title not in self.wss:
            raise gspread.WorksheetNotFound(title)
        return self.wss[title]

    def worksheets(self):
        self.worksheets_calls += 1
        return list(self.wss.values())

    def values_get(self, range, params=None):
        self.values_get_calls += 1
        title = range.split("!")[0]
        rows = self.wss[title].values if title in self.wss else []
        return {"values": [[r[0]] if r and str(r[0]) != "" else [] for r in rows]}

    def duplicate_sheet(self, source_sheet_id, new_sheet_name):
        self.duplicate_calls += 1
        src = next(w for w in self.wss.values() if w.id == source_sheet_id)
        ws = _FakeWS(new_sheet_name, src.values)
        self.wss[new_sheet_name] = ws
        return ws

    def del_worksheet(self, ws):
        self.wss.pop(ws.title, None)

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
        # 和真的一樣把 A{n}:Z 之後的列清掉，回讀驗證才測得到修剪結果
        self.batch_clear_calls += 1
        for rng in body["ranges"]:
            title, cells = rng.split("!")
            start = int(cells.split(":")[0][1:])
            if title in self.wss:
                self.wss[title].values = self.wss[title].values[: start - 1]
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
def _reset_fingerprint(monkeypatch):
    # 「內容沒變就不寫回」的指紋、已開啟的試算表都是模組層狀態，每個測試從乾淨狀態開始
    monkeypatch.setattr(ss, "_last_synced_fingerprint", None)
    monkeypatch.setattr(ss, "_spread_cache", None)


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
    assert fake.batch_update_calls == 2   # 主寫入 1 個 batch＋固定備份 1 個
    assert fake.batch_clear_calls == 2     # 修剪 1 個 batch＋固定備份修剪 1 個


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
    assert calls["n"] == 3   # 主寫入 429 一次＋重試成功，再加固定備份 1 次


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
    assert fake.batch_update_calls == 2   # 主寫入＋固定備份


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


def test_unchanged_content_skips_write_and_backup(engine_with_data, monkeypatch):
    # 只是打開頁面也會 commit；內容沒變時不應再整表覆寫、也不應產生備份擠掉歷史
    fake = _FakeSpread()
    opened = {"n": 0}

    def _open():
        opened["n"] += 1
        return fake, None

    monkeypatch.setattr(ss, "_HAS_GSPREAD", True)
    monkeypatch.setattr(ss, "_open_spreadsheet", _open)
    backups = []
    monkeypatch.setattr(ss, "_rolling_backup", lambda *a, **k: backups.append(1) or "bak")

    assert ss.sync_db_to_sheet(engine_with_data)[0]
    assert fake.batch_update_calls == 2 and len(backups) == 1   # 主寫入＋固定備份

    assert ss.sync_db_to_sheet(engine_with_data)[0]
    assert fake.batch_update_calls == 2      # 第二次沒寫
    assert len(backups) == 1                  # 也沒新增備份
    assert opened["n"] == 1                   # 連試算表都沒開


def test_changed_content_writes_again(engine_with_data, monkeypatch):
    fake = _FakeSpread()
    monkeypatch.setattr(ss, "_HAS_GSPREAD", True)
    monkeypatch.setattr(ss, "_open_spreadsheet", lambda: (fake, None))

    assert ss.sync_db_to_sheet(engine_with_data)[0]
    with engine_with_data.connect() as conn:
        conn.execute(text("UPDATE trades SET price = 999 WHERE id = 1"))
        conn.commit()
    assert ss.sync_db_to_sheet(engine_with_data)[0]
    assert fake.batch_update_calls == 4   # 兩次各：主寫入＋固定備份


def test_force_writes_even_if_unchanged(engine_with_data, monkeypatch):
    fake = _FakeSpread()
    monkeypatch.setattr(ss, "_HAS_GSPREAD", True)
    monkeypatch.setattr(ss, "_open_spreadsheet", lambda: (fake, None))

    assert ss.sync_db_to_sheet(engine_with_data)[0]
    assert ss.sync_db_to_sheet(engine_with_data, force=True)[0]
    assert fake.batch_update_calls == 4


def test_after_load_no_write_until_changed(engine_with_data, monkeypatch):
    # 剛從試算表載入（remember_db_as_synced）後，沒改任何東西就不寫回
    fake = _FakeSpread()
    monkeypatch.setattr(ss, "_HAS_GSPREAD", True)
    monkeypatch.setattr(ss, "_open_spreadsheet", lambda: (fake, None))

    ss.remember_db_as_synced(engine_with_data)
    assert ss.sync_db_to_sheet(engine_with_data)[0]
    assert fake.batch_update_calls == 0


def test_failed_write_does_not_mark_synced(engine_with_data, monkeypatch):
    # 寫入失敗不能記成已同步，否則下次會誤以為沒變而略過、變更永遠寫不回去
    fake = _FakeSpread()
    fake.values_batch_update = lambda body=None: (_ for _ in ()).throw(Exception("APIError: [429]: Quota exceeded"))
    monkeypatch.setattr(ss, "_HAS_GSPREAD", True)
    monkeypatch.setattr(ss, "_open_spreadsheet", lambda: (fake, None))

    assert not ss.sync_db_to_sheet(engine_with_data)[0]
    assert ss._last_synced_fingerprint is None


# ─── 2026-10-02 減少連線次數後的保護與行為 ─────────────────────────────

def _preset_trades(n):
    return [ss.TRADES_HEADERS] + [
        [i, "Peggy", "2330", "2026-01-02", "BUY", 1000, 1000, False, "", "", ""]
        for i in range(1, n + 1)
    ]


def _use(fake, monkeypatch):
    monkeypatch.setattr(ss, "_HAS_GSPREAD", True)
    monkeypatch.setattr(ss, "_open_spreadsheet", lambda: (fake, None))


def test_request_count_is_small(engine_with_data, monkeypatch):
    """一次寫回的連線數：分頁清單 1＋讀 id 2（防呆、回讀）＋寫入 2＋修剪 2＋複製備份 1。"""
    fake = _FakeSpread()
    _use(fake, monkeypatch)
    assert ss.sync_db_to_sheet(engine_with_data)[0]
    assert fake.worksheets_calls == 1
    assert fake.values_get_calls == 2
    assert fake.batch_update_calls == 2 and fake.batch_clear_calls == 2
    assert fake.duplicate_calls == 1


def test_trailing_rows_trimmed_and_backup_matches(monkeypatch):
    """試算表原有 12 筆、記憶體刪到 11 筆：多出的列被修剪，固定備份也是 11 筆。"""
    fake = _FakeSpread(preset={"trades": _preset_trades(12)})
    _use(fake, monkeypatch)
    assert ss.sync_db_to_sheet(_mk_engine(11))[0]
    assert len(fake.wss["trades"].values) == 12          # 表頭＋11
    assert len(fake.wss[ss.SHEET_TRADES_BACKUP].values) == 12


def test_precheck_read_failure_blocks_write(engine_with_data, monkeypatch):
    """寫回前讀不到現有 id → 不寫（以前會略過防呆直接寫）、不記成已同步。"""
    fake = _FakeSpread(preset={"trades": _preset_trades(100)})
    fake.values_get = lambda *a, **k: (_ for _ in ()).throw(Exception("APIError: [500]: backend error"))
    _use(fake, monkeypatch)
    ok, err = ss.sync_db_to_sheet(engine_with_data)
    assert not ok
    assert fake.batch_update_calls == 0
    assert ss._last_synced_fingerprint is None


def test_readback_mismatch_fails_and_not_marked(engine_with_data, monkeypatch):
    """寫完回讀的 id 和記憶體不同（例如寫入被吃掉）→ 回報失敗，下次存檔會重寫。"""
    fake = _FakeSpread()
    orig_update = fake.values_batch_update

    def lossy(body=None):
        orig_update(body)
        fake.wss["trades"].values = fake.wss["trades"].values[:1]   # 只剩表頭
        return {}

    fake.values_batch_update = lossy
    _use(fake, monkeypatch)
    ok, err = ss.sync_db_to_sheet(engine_with_data)
    assert not ok and "回讀" in err
    assert ss._last_synced_fingerprint is None


def test_readback_ignores_number_formatting(engine_with_data, monkeypatch):
    """試算表回傳 1.0 之類的數值格式，仍視為同一個 id。"""
    fake = _FakeSpread()
    orig_get = fake.values_get

    def float_ids(range, params=None):
        res = orig_get(range, params)
        res["values"] = res["values"][:1] + [[float(r[0])] if r else [] for r in res["values"][1:]]
        return res

    fake.values_get = float_ids
    _use(fake, monkeypatch)
    assert ss.sync_db_to_sheet(engine_with_data)[0]


def test_rolling_backup_throttled(engine_with_data, monkeypatch):
    """10 分鐘內已有滾動備份 → 這次不再複製分頁；固定備份照樣更新。"""
    from datetime import datetime
    fake = _FakeSpread()
    recent = ss.BACKUP_PREFIX + datetime.now().strftime("%Y%m%d_%H%M%S")
    fake.wss[recent] = _FakeWS(recent)
    _use(fake, monkeypatch)
    assert ss.sync_db_to_sheet(engine_with_data)[0]
    assert fake.duplicate_calls == 0
    assert len(fake.wss[ss.SHEET_TRADES_BACKUP].values) == 2


def test_rolling_backup_prunes_without_refetch(engine_with_data, monkeypatch):
    """備份超過保留份數時刪最舊的，且用剛抓的分頁清單，不再多抓一次。"""
    fake = _FakeSpread()
    olds = [ss.BACKUP_PREFIX + f"2026010{i}_000000" for i in range(1, 4)]
    for t in olds:
        fake.wss[t] = _FakeWS(t)
    monkeypatch.setenv("SHEET_BACKUP_KEEP", "3")
    _use(fake, monkeypatch)
    assert ss.sync_db_to_sheet(engine_with_data)[0]
    assert fake.duplicate_calls == 1
    assert olds[0] not in fake.wss and olds[1] in fake.wss
    assert fake.worksheets_calls == 1


def test_backup_is_due():
    from datetime import datetime
    now = datetime(2026, 10, 2, 12, 0, 0)
    p = ss.BACKUP_PREFIX
    assert ss.backup_is_due(["trades"], now, 10)                          # 沒有任何備份
    assert not ss.backup_is_due([p + "20261002_115500"], now, 10)         # 5 分鐘前
    assert ss.backup_is_due([p + "20261002_114900"], now, 10)             # 11 分鐘前
    assert ss.backup_is_due([p + "壞掉的名字"], now, 10)                  # 解析不了 → 備份
    assert ss.backup_is_due([p + "20261002_115959"], now, 0)              # 間隔 0 → 每次都備份


def test_spreadsheet_reused_and_forgotten_on_error(engine_with_data, monkeypatch):
    """試算表連線重用；出現非配額錯誤後丟掉，下次重新連線。"""
    fake = _FakeSpread()
    opened = {"n": 0}

    def _fresh():
        opened["n"] += 1
        return fake, None

    monkeypatch.setattr(ss, "_HAS_GSPREAD", True)
    monkeypatch.setattr(ss, "_open_spreadsheet_fresh", _fresh)
    assert ss.sync_db_to_sheet(engine_with_data)[0]
    assert ss.sync_db_to_sheet(engine_with_data, force=True)[0]
    assert opened["n"] == 1

    fake.worksheets = lambda: (_ for _ in ()).throw(Exception("ConnectionError: reset"))
    assert not ss.sync_db_to_sheet(engine_with_data, force=True)[0]
    assert ss._spread_cache is None


def test_concurrent_syncs_are_serialized(monkeypatch):
    """寫回進行中時，第二個寫回要等前一個結束，且讀到的是最新 DB 內容。"""
    import threading
    from sqlalchemy.pool import StaticPool
    # 跨執行緒共用同一個記憶體 DB（和正式環境多位同事共用一份記憶體 DB 一樣）
    engine_with_data = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine_with_data)
    with engine_with_data.connect() as conn:
        conn.execute(text(
            "INSERT INTO trades (id,user,stock_id,trade_date,side,price,quantity,is_daytrade) "
            "VALUES (1,'Peggy','2330','2026-01-02','BUY',1000,1000,0)"
        ))
        conn.commit()
    fake = _FakeSpread()
    _use(fake, monkeypatch)
    started, release = threading.Event(), threading.Event()
    orig_update = fake.values_batch_update
    first = {"done": False}

    def slow_update(body=None):
        if not first["done"]:
            first["done"] = True
            started.set()
            release.wait(5)
        return orig_update(body)

    fake.values_batch_update = slow_update
    results = []
    t1 = threading.Thread(target=lambda: results.append(ss.sync_db_to_sheet(engine_with_data)))
    t1.start()
    assert started.wait(5)
    # 第一個寫回卡在寫入中，此時新增一筆交易並觸發第二個寫回
    with engine_with_data.connect() as conn:
        conn.execute(text(
            "INSERT INTO trades (id,user,stock_id,trade_date,side,price,quantity,is_daytrade) "
            "VALUES (2,'Peggy','2330','2026-01-03','BUY',1000,1000,0)"
        ))
        conn.commit()
    t2 = threading.Thread(target=lambda: results.append(ss.sync_db_to_sheet(engine_with_data)))
    t2.start()
    t2.join(0.3)
    assert t2.is_alive()          # 被鎖擋住，沒有和第一個交錯
    release.set()
    t1.join(5); t2.join(5)
    assert all(ok for ok, _ in results)
    ids = [r[0] for r in fake.wss["trades"].values[1:]]
    assert ids == [1, 2]          # 最後留在試算表的是最新內容
