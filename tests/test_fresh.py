# -*- coding: utf-8 -*-
"""部署後自動重新載入過期模組（services/fresh.py）。"""
import os
import sys
import time

import services.fresh as fresh


def test_reload_when_source_changes(tmp_path, monkeypatch):
    pkg = tmp_path / "freshprobe"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    mod = pkg / "m.py"
    mod.write_text("def old():\n    return 1\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    import freshprobe.m as m
    assert fresh.reload_stale_modules(prefixes=("freshprobe.",)) == []      # 第一次只記下時間
    assert not hasattr(m, "new")

    mod.write_text("def old():\n    return 1\n\ndef new():\n    return 2\n", encoding="utf-8")
    t = time.time() + 5
    os.utime(mod, (t, t))                                                     # 模擬部署後檔案變新
    assert fresh.reload_stale_modules(prefixes=("freshprobe.",)) == ["freshprobe.m"]
    assert sys.modules["freshprobe.m"].new() == 2
    assert fresh.reload_stale_modules(prefixes=("freshprobe.",)) == []      # 重載後不再判定過期
    for n in [n for n in sys.modules if n.startswith("freshprobe")]:
        del sys.modules[n]


def test_current_services_are_not_stale():
    import services.trade_fees  # noqa: F401
    assert fresh.find_stale() == []


def test_detects_module_loaded_before_fresh_existed(tmp_path, monkeypatch):
    """模組在 fresh 第一次執行前就載入（這次部署的情況）：靠 .pyc 記的原始檔時間判斷。"""
    if sys.dont_write_bytecode:
        return
    pkg = tmp_path / "freshprobe2"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    mod = pkg / "m.py"
    mod.write_text("X = 1\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    import freshprobe2.m  # noqa: F401  載入時寫出 .pyc
    mod.write_text("X = 2\nY = 3\n", encoding="utf-8")
    t = time.time() + 5
    os.utime(mod, (t, t))
    fresh._seen.pop("freshprobe2.m", None)          # fresh 從沒看過它
    assert fresh.reload_stale_modules(prefixes=("freshprobe2.",)) == ["freshprobe2.m"]
    assert sys.modules["freshprobe2.m"].Y == 3
    for n in [n for n in sys.modules if n.startswith("freshprobe2")]:
        del sys.modules[n]
