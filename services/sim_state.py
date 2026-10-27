# -*- coding: utf-8 -*-
"""庫存模擬頁的「最後狀態」存檔（docs/specs/庫存模擬.md §6）：每個登入帳號一份 JSON。

刻意不放進 db/models.py：用自己的 Table 第一次用到時才建，部署後不必 Reboot；
也刻意用 engine 直接寫（不經 ORM Session），不會觸發資料版本 +1、不會讓交易快取失效。
只存模擬設定與手填的值，絕不碰交易、沖銷。
"""
import json
from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime, MetaData, String, Table, Text, delete, insert, select

_md = MetaData()
sim_states = Table(
    "sim_states", _md,
    Column("owner", String, primary_key=True),   # 登入帳號
    Column("data", Text, nullable=False),         # JSON：people、who、disc、mode、inputs
    Column("updated_at", DateTime, nullable=False),
)
_ready = set()


def _ensure_table(engine) -> None:
    if id(engine) not in _ready:
        _md.create_all(engine, checkfirst=True)
        _ready.add(id(engine))


def load_sim_state(engine, owner: str) -> Optional[dict]:
    _ensure_table(engine)
    with engine.connect() as conn:
        raw = conn.execute(select(sim_states.c.data).where(sim_states.c.owner == owner)).scalar()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def save_sim_state(engine, owner: str, state: dict) -> None:
    """整份覆蓋（先刪再寫，SQLite／Postgres 通用）。"""
    _ensure_table(engine)
    data = json.dumps(state, ensure_ascii=False, sort_keys=True)
    with engine.begin() as conn:
        conn.execute(delete(sim_states).where(sim_states.c.owner == owner))
        conn.execute(insert(sim_states).values(owner=owner, data=data, updated_at=datetime.utcnow()))


def inputs_to_json(inputs: dict) -> dict:
    """{股票代號: (張數, 價格)} → 可存 JSON 的 {代號: [張數, 價格]}；兩個都空的不存。"""
    return {s: [q, p] for s, (q, p) in inputs.items() if q is not None or p is not None}


def inputs_from_json(raw) -> dict:
    out = {}
    for s, v in (raw or {}).items():
        try:
            q, p = v
        except (TypeError, ValueError):
            continue
        out[str(s)] = (None if q is None else float(q), None if p is None else float(p))
    return out
