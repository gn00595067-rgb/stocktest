# -*- coding: utf-8 -*-
"""操作紀錄（舊系統這邊的寫入）：誰在什麼時候新增／修改／刪除了哪筆交易。

資料表 trade_audit 與新系統共用；快照格式同新系統「復原」用的 Snapshot（camelCase JSON），
所以舊系統刪掉的交易也能在新系統「操作紀錄」頁還原。規格：docs/specs/操作紀錄.md
一律用呼叫端的 session（同一個資料庫交易）：操作失敗紀錄不會留下；紀錄寫不進去，操作也不成功。
"""
import json
from typing import Iterable, List, Optional

from sqlalchemy import text

_PG_DDL = """create table if not exists trade_audit (
    id bigserial primary key, at timestamptz not null default now(), actor text not null, source text not null,
    action text not null, trade_id integer, trader text, stock_id text, trade_date date,
    before jsonb, after jsonb, note text, restored_at timestamptz, restored_by text)"""
_LITE_DDL = """create table if not exists trade_audit (
    id integer primary key autoincrement, at text not null default (datetime('now')), actor text not null, source text not null,
    action text not null, trade_id integer, trader text, stock_id text, trade_date text,
    before text, after text, note text, restored_at text, restored_by text)"""


def _is_pg(sess) -> bool:
    return sess.get_bind().dialect.name == "postgresql"


def ensure_table(sess) -> None:
    sess.execute(text(_PG_DDL if _is_pg(sess) else _LITE_DDL))


def current_actor() -> str:
    try:
        from services.auth_service import get_current_user
        return str((get_current_user() or {}).get("username") or "未知")
    except Exception:
        return "未知"


def snapshot_trades(sess, ids: Iterable[int], kind: str = "deleted") -> List[dict]:
    """多筆交易拍快照（交易＋沖銷規則＋委託書號），格式同新系統 Snapshot。不存在的 id 略過。"""
    from db.models import CustomMatchRule, Trade
    ids = sorted({int(i) for i in ids})
    if not ids:
        return []
    trades = sess.query(Trade).filter(Trade.id.in_(ids)).order_by(Trade.id).all()
    rules = (sess.query(CustomMatchRule)
             .filter(CustomMatchRule.sell_trade_id.in_(ids) | CustomMatchRule.buy_trade_id.in_(ids))
             .order_by(CustomMatchRule.sell_trade_id, CustomMatchRule.buy_trade_id).all())
    refs = []
    try:
        refs = sess.execute(text(
            "select trade_id, \"user\", trade_date, order_no, batch_id, source, created_at from trade_refs "
            "where trade_id in (" + ",".join(str(i) for i in ids) + ") order by trade_date, order_no")).all()
    except Exception:
        refs = []
    out = []
    for t in trades:
        out.append({
            "kind": kind,
            "trade": {
                "id": int(t.id), "user": t.user, "stockId": str(t.stock_id).strip(), "tradeDate": str(t.trade_date)[:10],
                "side": t.side, "price": float(t.price or 0), "quantity": int(t.quantity or 0),
                "isDaytrade": bool(t.is_daytrade), "fee": None if t.fee is None else float(t.fee),
                "tax": None if t.tax is None else float(t.tax), "note": t.note,
            },
            "rules": [{"s": int(r.sell_trade_id), "b": int(r.buy_trade_id), "q": int(r.matched_qty),
                       "createdAt": str(r.created_at) if r.created_at else None}
                      for r in rules if int(r.sell_trade_id) == t.id or int(r.buy_trade_id) == t.id],
            "refs": [{"user": r[1], "tradeDate": str(r[2])[:10], "orderNo": r[3], "batchId": r[4], "source": r[5],
                      "createdAt": str(r[6])} for r in refs if int(r[0]) == t.id],
        })
    return out


def log(sess, action: str, source: str, before: Optional[dict] = None, after: Optional[dict] = None,
        trade: Optional[dict] = None, note: Optional[str] = None, actor: Optional[str] = None) -> None:
    """寫一筆紀錄。trade＝{id, user, stockId, tradeDate}（before 沒有時用）。"""
    ensure_table(sess)
    t = (before or {}).get("trade") or trade or {}
    cast = "cast(:{0} as jsonb)" if _is_pg(sess) else ":{0}"
    sess.execute(text(
        "insert into trade_audit (actor, source, action, trade_id, trader, stock_id, trade_date, before, after, note) "
        f"values (:actor, :source, :action, :tid, :trader, :sid, :d, {cast.format('b')}, {cast.format('a')}, :note)"),
        {"actor": actor or current_actor(), "source": source, "action": action, "tid": t.get("id"),
         "trader": t.get("user"), "sid": t.get("stockId"), "d": t.get("tradeDate"),
         "b": json.dumps(before, ensure_ascii=False) if before else None,
         "a": json.dumps(after, ensure_ascii=False, default=str) if after else None, "note": note})


def log_many(sess, action: str, source: str, befores: List[dict], note: Optional[str] = None,
             actor: Optional[str] = None) -> None:
    for b in befores:
        log(sess, action, source, before=b, note=note, actor=actor)
