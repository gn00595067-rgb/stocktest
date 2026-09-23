# -*- coding: utf-8 -*-
"""資料保護守門的純函式測試（不連網）。

對應 services/sheet_sync 的三道防線：
  - backup_prune_plan：滾動備份修剪
  - shrink_is_suspicious：縮水守門
  - _env_flag / _env_int：環境變數解析
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.sheet_sync import (
    backup_prune_plan,
    _env_flag,
    _env_int,
    BACKUP_PREFIX,
)


def _bak(*stamps):
    return [BACKUP_PREFIX + s for s in stamps]


def test_prune_keeps_newest_and_deletes_oldest():
    titles = ["trades", "user_accounts"] + _bak("20260101_000000", "20260102_000000", "20260103_000000")
    # 保留 2 份 → 刪最舊 1 份
    assert backup_prune_plan(titles, keep=2) == _bak("20260101_000000")


def test_prune_noop_when_under_limit():
    titles = ["trades"] + _bak("20260101_000000", "20260102_000000")
    assert backup_prune_plan(titles, keep=5) == []


def test_prune_ignores_non_backup_tabs():
    titles = ["trades", "custom_match_rules", "traders"]
    assert backup_prune_plan(titles, keep=0) == []  # 沒有備份分頁


def test_prune_keep_zero_deletes_all_backups():
    titles = ["trades"] + _bak("20260101_000000", "20260102_000000")
    assert set(backup_prune_plan(titles, keep=0)) == set(_bak("20260101_000000", "20260102_000000"))


def test_env_flag_default_and_override():
    os.environ.pop("X_TEST_FLAG", None)
    assert _env_flag("X_TEST_FLAG", True) is True
    assert _env_flag("X_TEST_FLAG", False) is False
    os.environ["X_TEST_FLAG"] = "false"
    assert _env_flag("X_TEST_FLAG", True) is False
    os.environ["X_TEST_FLAG"] = "1"
    assert _env_flag("X_TEST_FLAG", False) is True
    os.environ.pop("X_TEST_FLAG", None)


def test_env_int_default_and_override():
    os.environ.pop("X_TEST_INT", None)
    assert _env_int("X_TEST_INT", 10) == 10
    os.environ["X_TEST_INT"] = "3"
    assert _env_int("X_TEST_INT", 10) == 3
    os.environ["X_TEST_INT"] = "notanumber"
    assert _env_int("X_TEST_INT", 10) == 10
    os.environ.pop("X_TEST_INT", None)
