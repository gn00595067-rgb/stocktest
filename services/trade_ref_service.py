# -*- coding: utf-8 -*-
"""委託書號紀錄：記住哪張委託已經進系統，防止對帳單重複匯入（docs/specs/對帳單匯入.md §4）。

比照 pledge_service／sim_state：刻意不放進 db/models.py，第一次用到才建表（部署免 Reboot）。
寫入走呼叫端的 ORM Session，跟新增交易、沖銷規則同一個交易（transaction），失敗一起 rollback。
"""
from datetime import date, datetime
from typing import Dict, Iterable, List, Tuple

from sqlalchemy import Column, Date, DateTime, Integer, MetaData, String, Table, delete, insert, select

_md = MetaData()
trade_refs = Table(
    "trade_refs", _md,
    Column("user", String, primary_key=True),        # 買賣人
    Column("trade_date", Date, primary_key=True),    # 委託書號每天重編，要帶日期
    Column("order_no", String, primary_key=True),    # 委託書號（大小寫有別）
    Column("trade_id", Integer, nullable=False),     # 對到哪筆交易（合併 key 時多個書號對同一筆）
    Column("batch_id", String, nullable=True),       # 哪一次匯入
    Column("source", String, nullable=False),        # import＝匯入新增；backfill＝認出是手 key 的、補上書號
    Column("created_at", DateTime, nullable=False),
)
_ready = set()


def ensure_table(session) -> None:
    """用 session 自己的連線建表：記憶體 SQLite 只有一條共用連線，另開連線歸還時會把未 commit 的改動 rollback。"""
    bind = session.get_bind()
    if id(bind) not in _ready:
        _md.create_all(session.connection(), checkfirst=True)
        _ready.add(id(bind))


def load_refs(session, user: str) -> List[Tuple[date, str, int]]:
    """這個買賣人的所有書號 [(成交日, 書號, 交易 id)]（含交易已刪除的，由呼叫端過濾）。"""
    ensure_table(session)
    rows = session.execute(
        select(trade_refs.c.trade_date, trade_refs.c.order_no, trade_refs.c.trade_id)
        .where(trade_refs.c.user == user)
        .order_by(trade_refs.c.trade_date, trade_refs.c.order_no)
    ).all()
    return [(d if isinstance(d, date) else datetime.strptime(str(d)[:10], "%Y-%m-%d").date(), str(o), int(t))
            for d, o, t in rows]


def add_refs(session, user: str, refs: Iterable[Tuple[date, str, int, str]], batch_id: str) -> int:
    """refs = [(成交日, 書號, 交易 id, source)]。同鍵已存在（指向已刪交易的舊紀錄）會先刪掉再寫。不 commit。"""
    ensure_table(session)
    n = 0
    now = datetime.utcnow()
    for d, order_no, trade_id, source in refs:
        session.execute(delete(trade_refs).where(
            (trade_refs.c.user == user) & (trade_refs.c.trade_date == d) & (trade_refs.c.order_no == order_no)))
        session.execute(insert(trade_refs).values(
            user=user, trade_date=d, order_no=order_no, trade_id=int(trade_id),
            batch_id=batch_id, source=source, created_at=now))
        n += 1
    return n


def refs_by_trade(session, trade_ids: Iterable[int]) -> Dict[int, List[str]]:
    """{交易 id: [書號…]}，給畫面顯示用。"""
    ids = [int(i) for i in trade_ids]
    if not ids:
        return {}
    ensure_table(session)
    out: Dict[int, List[str]] = {}
    for tid, o in session.execute(
            select(trade_refs.c.trade_id, trade_refs.c.order_no).where(trade_refs.c.trade_id.in_(ids))).all():
        out.setdefault(int(tid), []).append(str(o))
    return out


def load_live_refs_all_users(session) -> List[Tuple[date, str, str, str, str]]:
    """所有買賣人、交易還在的書號 [(成交日, 書號, 買賣人, 股號, BUY/SELL)]，給跨人防重複用。"""
    from db.models import Trade
    ensure_table(session)
    t = Trade.__table__
    rows = session.execute(
        select(trade_refs.c.trade_date, trade_refs.c.order_no, trade_refs.c.user, t.c.stock_id, t.c.side)
        .select_from(trade_refs.join(t, t.c.id == trade_refs.c.trade_id))
        .order_by(trade_refs.c.trade_date, trade_refs.c.order_no)
    ).all()
    return [(d if isinstance(d, date) else datetime.strptime(str(d)[:10], "%Y-%m-%d").date(),
             str(o), str(u), str(sid).strip(), str(sd or "").strip().upper()) for d, o, u, sid, sd in rows]
