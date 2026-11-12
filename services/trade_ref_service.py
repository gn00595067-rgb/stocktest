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
    seen: Dict[tuple, int] = {}
    for d, order_no, trade_id, source in refs:
        # 同一批裡同一天同書號出現第二次（券商把一張委託拆兩列，例：奇鋐 k02Zc 37 股＋13 股）：
        # 存成「書號#2」，每筆交易都記得到批次（復原匯入才刪得乾淨）；比對重複仍用原書號
        k = (d, order_no)
        seen[k] = seen.get(k, 0) + 1
        if seen[k] > 1:
            order_no = f"{order_no}#{seen[k]}"
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


def _unref_in_range(session, user: str, ids) -> set:
    """舊批次補漏：同一批 id 範圍內、這個人、完全沒有書號紀錄的交易（2026-11-12 前同批重複書號只記到最後一筆）。
    匯入是同一個交易連續寫入，id 連號，中間不會夾別的匯入。"""
    from db.models import Trade
    ids = [int(i) for i in ids]
    if not ids:
        return set()
    cand = [int(r[0]) for r in session.query(Trade.id).filter(
        Trade.user == user, Trade.id >= min(ids), Trade.id <= max(ids)).all()]
    has_ref = {int(r[0]) for r in session.execute(
        select(trade_refs.c.trade_id).where(trade_refs.c.trade_id.in_(cand))).all()} if cand else set()
    return {i for i in cand if i not in has_ref and i not in set(ids)}


def list_import_batches(session, days: int = 14) -> List[dict]:
    """最近的匯入批次（只算 source=import、交易還在的），新到舊。
    每批：batch_id、買賣人、筆數、成交日範圍、股票、跟「別批或手 key 的交易」內容一模一樣的筆數（疑似重複）。"""
    from db.models import Trade
    ensure_table(session)
    t = Trade.__table__
    rows = session.execute(
        select(trade_refs.c.batch_id, trade_refs.c.user, t.c.id, t.c.trade_date, t.c.stock_id, t.c.side,
               t.c.price, t.c.quantity)
        .select_from(trade_refs.join(t, t.c.id == trade_refs.c.trade_id))
        .where(trade_refs.c.source == "import")
    ).all()
    batches: Dict[tuple, dict] = {}
    for b, u, tid, d, sid, side, price, qty in rows:
        if not b:
            continue
        x = batches.setdefault((b, u), {"batch_id": b, "user": u, "ids": set(), "days": set(), "stocks": set()})
        x["ids"].add(int(tid))
        x["days"].add(d)
        x["stocks"].add(str(sid).strip())
    if not batches:
        return []
    for x in batches.values():
        x["ids"] |= _unref_in_range(session, x["user"], x["ids"])
    cutoff = (datetime.now().date().toordinal() - days)
    out = [x for x in batches.values() if datetime.strptime(x["batch_id"][:8], "%Y%m%d").date().toordinal() >= cutoff]
    # 疑似重複：同人、同日、同股、同買賣、同價、同股數的交易不只這批有
    users = {x["user"] for x in out}
    all_days = {d for x in out for d in x["days"]}
    if out:
        trs = session.query(Trade).filter(Trade.user.in_(users), Trade.trade_date.in_(all_days)).all()
        by_key: Dict[tuple, List[int]] = {}
        for tr in trs:
            k = (tr.user, tr.trade_date, str(tr.stock_id).strip(), str(tr.side).upper(), float(tr.price), int(tr.quantity))
            by_key.setdefault(k, []).append(int(tr.id))
        key_of = {i: k for k, ids in by_key.items() for i in ids}
        for x in out:
            x["dup"] = sum(1 for i in x["ids"] if i in key_of and any(j not in x["ids"] for j in by_key[key_of[i]]))
    for x in out:
        x["n"] = len(x["ids"])
    return sorted(out, key=lambda x: x["batch_id"], reverse=True)


def undo_batch(session, batch_id: str, user: str) -> int:
    """刪掉這批匯入新增的交易（連同沖銷規則、書號紀錄）。補書號／修正／手續費更新動到的既有交易不動。不 commit。"""
    from db.models import CustomMatchRule, Trade
    ensure_table(session)
    ids = [int(r[0]) for r in session.execute(
        select(trade_refs.c.trade_id).where((trade_refs.c.batch_id == batch_id) & (trade_refs.c.user == user)
                                            & (trade_refs.c.source == "import"))).all()]
    if not ids:
        return 0
    ids = list(set(ids) | _unref_in_range(session, user, ids))
    session.query(CustomMatchRule).filter(CustomMatchRule.sell_trade_id.in_(ids)
                                          | CustomMatchRule.buy_trade_id.in_(ids)).delete(synchronize_session=False)
    session.query(Trade).filter(Trade.id.in_(ids)).delete(synchronize_session=False)
    session.execute(delete(trade_refs).where(trade_refs.c.trade_id.in_(ids)))
    return len(set(ids))


def lock_imports(session) -> None:
    """同一時間只讓一個匯入寫入（Postgres advisory lock，交易結束自動放開）。
    2026-11-12：Elsa 按了確認匯入後以為卡住、重整又匯一次，兩邊在對方還沒存好前都判斷成新增 → 重複。
    SQLite（試算表模式、測試）只有一條連線，本來就不會同時寫。"""
    from sqlalchemy import text
    if session.get_bind().dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(20261112)"))


def already_imported(session, refs: Iterable[Tuple[str, date, str]]) -> int:
    """[(買賣人, 成交日, 書號)] 裡有幾個已經有書號紀錄、而且交易還在（上鎖後再檢查一次用）。"""
    from db.models import Trade
    ensure_table(session)
    keys = {(u, d, o) for u, d, o in refs if o}
    if not keys:
        return 0
    t = Trade.__table__
    rows = session.execute(
        select(trade_refs.c.user, trade_refs.c.trade_date, trade_refs.c.order_no)
        .select_from(trade_refs.join(t, t.c.id == trade_refs.c.trade_id))
        .where(trade_refs.c.order_no.in_({o for _u, _d, o in keys}))
    ).all()
    found = {(u, d if isinstance(d, date) else datetime.strptime(str(d)[:10], "%Y-%m-%d").date(), o)
             for u, d, o in rows}
    return len(keys & found)
