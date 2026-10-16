# -*- coding: utf-8 -*-
"""資料庫額度監控（docs/specs/資料庫額度監控.md）。"""
import services.neon_usage as nu


def test_is_quota_error():
    assert nu.is_quota_error(Exception(
        "ERROR: Your account or project has exceeded the compute time quota. Upgrade your plan to increase limits."))
    assert nu.is_quota_error(Exception("data transfer quota exceeded"))
    assert not nu.is_quota_error(Exception("could not connect to server: Connection timed out"))
    assert not nu.is_quota_error(Exception("password authentication failed"))


def test_summarize_project_cu_hours_and_period():
    u = nu.summarize_project({
        "id": "p1", "name": "stocktest", "compute_time_seconds": 257472, "active_time_seconds": 900000,
        "consumption_period_start": "2026-10-01T00:00:00Z", "consumption_period_end": "2026-11-01T00:00:00Z",
    })
    assert u["cu_hours"] == 71.52
    assert u["active_hours"] == 250.0
    assert u["period_end"].strftime("%m/%d") == "11/01"


def test_summarize_project_missing_fields():
    u = nu.summarize_project({"id": "p1"})
    assert u["cu_hours"] == 0 and u["period_end"] is None


def test_level():
    assert nu.level(10) == "ok"
    assert nu.level(70) == "warn"
    assert nu.level(95) == "danger"
    assert nu.level(100) == "over"


def test_fetch_usage_picks_only_project(monkeypatch):
    calls = []

    def fake_get(path, key, timeout=10):
        calls.append(path)
        if path == "/projects":
            return {"projects": [{"id": "abc", "name": "stocktest"}]}
        return {"project": {"id": "abc", "name": "stocktest", "compute_time_seconds": 3600}}

    monkeypatch.setattr(nu, "_get", fake_get)
    assert nu.fetch_usage("k")["cu_hours"] == 1.0
    assert calls == ["/projects", "/projects/abc"]


def test_check_script_exit_code(monkeypatch):
    import scripts.check_neon_usage as chk
    monkeypatch.setattr(chk, "fetch_usage", lambda key, pid=None: nu.summarize_project(
        {"name": "stocktest", "compute_time_seconds": 85 * 3600}))
    monkeypatch.setenv("NEON_API_KEY", "k")
    monkeypatch.delenv("NEON_ALERT_CU", raising=False)
    assert chk.main() == 1          # 85 ≥ 80：失敗、寄信
    monkeypatch.setenv("NEON_ALERT_CU", "90")
    assert chk.main() == 0
    monkeypatch.delenv("NEON_API_KEY")
    assert chk.main() == 0          # 沒設 key：略過
