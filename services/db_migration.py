# -*- coding: utf-8 -*-
"""試算表 → 資料庫（Postgres）轉移：複製、逐筆比對、調整自動編號。

設計原則（見 docs/specs/改用資料庫.md）：
  - 只讀試算表，絕不寫回。
  - 整批寫入包在同一個 transaction：中途失敗，目標資料庫維持原狀。
  - 目標已有資料時預設拒絕，要明確指定 replace 才會清空重灌。
  - 搬完逐表、逐筆比對內容，完全一致才算成功。
"""
from typing import Dict, List, Tuple

from sqlalchemy import create_engine, select, text, func
from sqlalchemy.pool import StaticPool

from db.models import Base

# 正式資料表（與試算表五個分頁對應）。依相依順序排列：先父後子。
TABLES = ["traders", "user_accounts", "trades", "custom_match_rules", "user_trader_bindings"]

# 有自動編號 id 的表：搬完要把編號調到最大 id 之後，新交易才不會撞號
SERIAL_TABLES = ["traders", "user_accounts", "trades"]


def load_sheet_into_memory():
    """從 Google 試算表唯讀載入到一個記憶體 SQLite，回傳 engine。載入失敗直接拋錯（不會給半套資料）。"""
    from services.sheet_sync import sync_from_sheet_to_db

    eng = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    ok, err = sync_from_sheet_to_db(eng)
    if not ok:
        raise RuntimeError(f"從試算表載入失敗：{err}")
    return eng


def _pk_cols(table):
    return list(table.primary_key.columns)


def table_rows(engine, name: str) -> List[Tuple]:
    """讀出整張表，依主鍵排序，回傳 tuple 清單（欄位順序同模型定義）。"""
    table = Base.metadata.tables[name]
    with engine.connect() as conn:
        rows = conn.execute(select(table).order_by(*_pk_cols(table))).fetchall()
    return [tuple(r) for r in rows]


def count_rows(engine) -> Dict[str, int]:
    out = {}
    with engine.connect() as conn:
        for name in TABLES:
            table = Base.metadata.tables[name]
            out[name] = int(conn.execute(select(func.count()).select_from(table)).scalar() or 0)
    return out


def compare(src, dst) -> Dict[str, dict]:
    """逐表、逐筆比對。回傳 {表名: {"src": 筆數, "dst": 筆數, "only_src": [...], "only_dst": [...]}}。"""
    report = {}
    for name in TABLES:
        a, b = table_rows(src, name), table_rows(dst, name)
        sa, sb = set(a), set(b)
        report[name] = {
            "src": len(a),
            "dst": len(b),
            "only_src": sorted(sa - sb, key=str)[:10],
            "only_dst": sorted(sb - sa, key=str)[:10],
            "same": a == b,
        }
    return report


def report_ok(report: Dict[str, dict]) -> bool:
    return all(r["same"] for r in report.values())


def format_report(report: Dict[str, dict]) -> str:
    lines = []
    for name, r in report.items():
        mark = "✅ 一致" if r["same"] else "❌ 不一致"
        lines.append(f"  {name:<22} 試算表 {r['src']:>5} 筆｜資料庫 {r['dst']:>5} 筆　{mark}")
        for row in r["only_src"]:
            lines.append(f"      只在試算表：{row}")
        for row in r["only_dst"]:
            lines.append(f"      只在資料庫：{row}")
    return "\n".join(lines)


def reset_sequences(conn) -> None:
    """Postgres：把自動編號調到「最大 id 之後」。SQLite 會自己處理，不必動。"""
    if conn.dialect.name != "postgresql":
        return
    for name in SERIAL_TABLES:
        conn.execute(text(
            f"SELECT setval(pg_get_serial_sequence('{name}', 'id'), "
            f"COALESCE((SELECT MAX(id) FROM {name}), 0) + 1, false)"
        ))


def copy_all(src, dst, replace: bool = False) -> Dict[str, int]:
    """把 src 的五張表整批複製到 dst（同一個 transaction）。回傳各表寫入筆數。

    dst 任一張表已有資料且 replace=False → 拋錯，不動任何資料。
    """
    from db.database import create_schema

    create_schema(dst)
    existing = count_rows(dst)
    if any(existing.values()) and not replace:
        raise RuntimeError(
            "目標資料庫已有資料，為避免覆蓋已中止："
            + "、".join(f"{k} {v} 筆" for k, v in existing.items() if v)
            + "。確定要清空重灌請加 --replace。"
        )

    data = {name: table_rows(src, name) for name in TABLES}
    written = {}
    with dst.begin() as conn:  # 任何一步失敗整批回滾
        for name in reversed(TABLES):
            conn.execute(Base.metadata.tables[name].delete())
        for name in TABLES:
            table = Base.metadata.tables[name]
            cols = [c.name for c in table.columns]
            rows = [dict(zip(cols, r)) for r in data[name]]
            if rows:
                conn.execute(table.insert(), rows)
            written[name] = len(rows)
        reset_sequences(conn)
    return written
