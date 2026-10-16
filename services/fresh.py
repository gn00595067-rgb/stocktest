# -*- coding: utf-8 -*-
"""部署後自動重新載入過期的 services／reports 模組。

Streamlit Cloud 拉到新版程式後常常只重跑頁面、不重新載入已 import 的模組：
頁面 `from services.x import 新函式` 會 ImportError（2026-10-15、10-17 都發生過，只能手動 Reboot）。
每頁最前面呼叫 reload_stale_modules()：發現程式檔比記憶體裡的版本新，就把 services／reports 全部重新載入。

判斷「比記憶體新」：
- 這個行程第一次看到某模組時記下它的檔案修改時間，之後檔案時間變了就是新版；
- 或 .pyc 記錄的原始檔時間／大小 ≠ 現在的檔案（模組在本檔第一次執行前就已載入的情況）。
db.* 不重載（SQLAlchemy 資料模型重定義會壞），資料表有改仍需 Reboot。
"""
import importlib
import os
import sys

_PREFIXES = ("services.", "reports.")
_seen = {}   # 模組名 → (mtime, size)


def _stat(path):
    st = os.stat(path)
    return int(st.st_mtime), st.st_size


def _pyc_says_stale(mod, src_stat) -> bool:
    """pyc 標頭記著編譯時原始檔的時間與大小；與現在不同＝模組是舊版載入的。"""
    pyc = getattr(mod, "__cached__", None)
    if not pyc or not os.path.exists(pyc):
        return False
    try:
        with open(pyc, "rb") as f:
            head = f.read(16)
        if len(head) < 16 or int.from_bytes(head[4:8], "little") != 0:   # 非時間戳型 pyc：不判斷
            return False
        mtime = int.from_bytes(head[8:12], "little")
        size = int.from_bytes(head[12:16], "little")
        return (mtime, size) != (src_stat[0] & 0xFFFFFFFF, src_stat[1] & 0xFFFFFFFF)
    except OSError:
        return False


def find_stale(prefixes=_PREFIXES) -> list:
    stale = []
    for name, mod in list(sys.modules.items()):
        if not name.startswith(prefixes) or name == __name__:
            continue
        path = getattr(mod, "__file__", None)
        if not path or not path.endswith(".py") or not os.path.exists(path):
            continue
        cur = _stat(path)
        prev = _seen.get(name)
        if (prev is not None and prev != cur) or (prev is None and _pyc_says_stale(mod, cur)):
            stale.append(name)
        _seen[name] = cur
    return stale


def reload_stale_modules(prefixes=_PREFIXES) -> list:
    """有任何過期模組就把 services／reports 全部重新載入（彼此會 from-import，只重載一部分可能留舊函式）。"""
    stale = find_stale(prefixes)
    if not stale:
        return []
    names = sorted(n for n in sys.modules if n.startswith(prefixes) and n != __name__)
    for _ in range(2):   # 跑兩輪：第一輪讓被依賴的模組更新，第二輪讓 from-import 的人拿到新版
        for n in names:
            try:
                importlib.reload(sys.modules[n])
            except Exception as e:   # 單一模組重載失敗不擋整頁；照舊用原模組
                print(f"[fresh] 重新載入 {n} 失敗：{type(e).__name__}: {e}")
    for n in names:
        p = getattr(sys.modules.get(n), "__file__", None)
        if p and os.path.exists(p):
            _seen[n] = _stat(p)
    print(f"[fresh] 偵測到新版程式，已重新載入 {len(names)} 個模組（過期：{', '.join(stale)}）")
    return stale
