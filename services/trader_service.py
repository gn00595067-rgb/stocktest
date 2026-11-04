# -*- coding: utf-8 -*-
"""買賣人名單服務：列出／新增／刪除，並自動同步到 Google 試算表（透過 commit hook）。"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

from db.database import get_session
from db.models import Trader, Trade


def list_trader_names() -> list[str]:
    """依建立順序回傳買賣人名稱清單。"""
    sess = get_session()
    try:
        rows = sess.query(Trader).order_by(Trader.id).all()
        return [r.name for r in rows if r.name]
    finally:
        sess.close()


def all_trader_names() -> list[str]:
    """買賣人主檔 ∪ 交易中曾出現的買賣人（依名稱排序、去重）。

    供管理者在「權限綁定 / 各頁篩選」時使用：連「剛在主檔新增、還沒有任何
    交易」的買賣人也選得到（否則只列 Trade.user 會漏掉新名字）。
    非管理者請勿使用此清單（會外洩全部買賣人）；非管理者一律用 get_allowed_traders()。
    """
    sess = get_session()
    try:
        master = [r.name for r in sess.query(Trader).order_by(Trader.id).all() if r.name]
        used = [u[0] for u in sess.query(Trade.user).distinct().all() if u[0]]
    finally:
        sess.close()
    seen: set[str] = set()
    out: list[str] = []
    for n in list(master) + list(used):
        n2 = (n or "").strip()
        if n2 and n2 not in seen:
            seen.add(n2)
            out.append(n2)
    return sorted(out)


def ensure_traders_seeded() -> None:
    """名單為空時，用既有交易中出現過的買賣人自動補齊（保留原有資料，方便首次使用）。"""
    sess = get_session()
    try:
        if sess.query(Trader.id).first() is not None:
            return
        seen: set[str] = set()
        for (name,) in sess.query(Trade.user).distinct().all():
            n = (name or "").strip()
            if n and n not in seen:
                seen.add(n)
                sess.add(Trader(name=n))
        if seen:
            sess.commit()
    except Exception:
        sess.rollback()
    finally:
        sess.close()


def add_trader(name: str) -> Tuple[bool, str]:
    name = (name or "").strip()
    if not name:
        return False, "請輸入買賣人名稱"
    if len(name) > 50:
        return False, "名稱過長（上限 50 字）"
    sess = get_session()
    try:
        if sess.query(Trader).filter(Trader.name == name).first():
            return False, f"「{name}」已存在"
        sess.add(Trader(name=name))
        sess.commit()
        return True, f"已新增買賣人「{name}」"
    except Exception as e:
        sess.rollback()
        return False, str(e)
    finally:
        sess.close()


def delete_trader(name: str) -> Tuple[bool, str]:
    name = (name or "").strip()
    sess = get_session()
    try:
        row = sess.query(Trader).filter(Trader.name == name).first()
        if not row:
            return False, "找不到該買賣人"
        sess.delete(row)
        sess.commit()
        return True, f"已刪除「{name}」（既有交易不受影響）"
    except Exception as e:
        sess.rollback()
        return False, str(e)
    finally:
        sess.close()


# ---------- 手續費折數（docs/specs/買賣人手續費折數.md） ----------
_discount_cache: dict = {"at": 0.0, "data": None}
_DISCOUNT_TTL = 60   # 秒；別人改了折數，最慢一分鐘內各頁生效（自己存檔會立刻清快取）


def load_fee_discounts() -> Dict[str, Tuple[Optional[float], Optional[float]]]:
    """{買賣人: (折數, 另一個折數)}。每次輸入都要用，快取 60 秒避免每列都查資料庫。"""
    import time
    if _discount_cache["data"] is not None and time.monotonic() - _discount_cache["at"] < _DISCOUNT_TTL:
        return _discount_cache["data"]
    sess = get_session()
    try:
        out = {r.name: (r.fee_discount, r.fee_discount_alt) for r in sess.query(Trader).all() if r.name}
    except Exception:
        out = {}   # 欄位還沒遷移（舊版資料庫）：全部用預設
    finally:
        sess.close()
    _discount_cache.update(at=time.monotonic(), data=out)
    return out


def clear_fee_discount_cache() -> None:
    _discount_cache.update(at=0.0, data=None)


def trader_discounts(trader: str) -> Tuple[float, Optional[float]]:
    """(主要折數, 另一個折數或 None)。沒設定的人＝一般人 2.8 折、不可切換。"""
    from services.trade_fees import DEFAULT_DISCOUNT
    main, alt = load_fee_discounts().get((trader or "").strip(), (None, None))
    main = float(main) if main else DEFAULT_DISCOUNT
    alt = float(alt) if alt and abs(float(alt) - main) > 1e-9 else None
    return main, alt


def trader_fee_rate(trader: str, discount: Optional[float] = None) -> float:
    """該買賣人的手續費率；discount 有給（每筆選的折數）就用它。"""
    from services.trade_fees import fee_rate_for_discount
    return fee_rate_for_discount(discount if discount else trader_discounts(trader)[0])


def save_fee_discounts(values: Dict[str, Tuple[Optional[float], Optional[float]]]) -> int:
    """寫回折數；回傳有變動的人數。"""
    sess = get_session()
    changed = 0
    try:
        for r in sess.query(Trader).all():
            if r.name not in values:
                continue
            main, alt = values[r.name]
            if (r.fee_discount, r.fee_discount_alt) != (main, alt):
                r.fee_discount, r.fee_discount_alt = main, alt
                changed += 1
        sess.commit()
    except Exception:
        sess.rollback()
        raise
    finally:
        sess.close()
    clear_fee_discount_cache()
    return changed


def discount_label(d: Optional[float]) -> str:
    """0.28 → 「2.8 折」。"""
    from services.trade_fees import DEFAULT_DISCOUNT
    d = d or DEFAULT_DISCOUNT
    return f"{d * 10:g} 折"


# ---------- 買賣人改名（docs/specs/買賣人改名.md） ----------
# (表, 欄位, 這張表是不是第一次用到才建、可能還不存在)
_RENAME_COLUMNS = [
    ("trades", '"user"', False),
    ("cashflows", '"user"', False),
    ("traders", "name", False),
    ("user_trader_bindings", "trader_name", False),
    ("portfolio_groups", "trader", False),
    ("manual_figures", "trader", False),
    ("pledges", "trader", True),
    ("trade_refs", '"user"', True),
]


def rename_trader(old: str, new: str, session=None) -> Tuple[bool, str]:
    """同一個人改名：交易、主檔（含手續費折數）、權限綁定、分頁、手填數字、質押、書號全部改成新名字。

    新名字已經有人用就擋下（那是合併兩個人，不是改名）。全部在同一個交易裡，失敗一起還原。
    session 給測試用；沒給就自己開、自己關。
    """
    from sqlalchemy import inspect, text

    old, new = (old or "").strip(), (new or "").strip()
    if not new:
        return False, "請輸入新的名字"
    if len(new) > 50:
        return False, "名稱過長（上限 50 字）"
    if new == old:
        return False, "新名字跟原本一樣"
    own = session is None
    sess = session or get_session()
    try:
        if sess.query(Trader.id).filter(Trader.name == new).first() is not None \
                or sess.query(Trade.id).filter(Trade.user == new).first() is not None:
            return False, f"「{new}」已經有人用了；這是把兩個人合併，不是改名，請換一個名字"
        conn = sess.connection()   # 用 session 自己的連線：試算表模式另開連線會 rollback 掉未 commit 的改動
        insp = inspect(conn)
        n = {}
        for table, col, optional in _RENAME_COLUMNS:
            if optional and not insp.has_table(table):
                continue
            n[table] = conn.execute(text(f"UPDATE {table} SET {col} = :new WHERE {col} = :old"),
                                    {"new": new, "old": old}).rowcount
        conn.execute(text("UPDATE portfolio_groups SET name = :new WHERE trader = :new AND name = :old"),
                     {"new": new, "old": old})
        # 多人分頁「也一起看的人」是逗號清單，逐列換掉名字
        if "extra_traders" in {c["name"] for c in insp.get_columns("portfolio_groups")}:
            for gid, extra in conn.execute(text("SELECT id, extra_traders FROM portfolio_groups "
                                                "WHERE extra_traders IS NOT NULL AND extra_traders <> ''")).all():
                names = [x.strip() for x in str(extra).split(",") if x.strip()]
                if old in names:
                    names = [new if x == old else x for x in names]
                    conn.execute(text("UPDATE portfolio_groups SET extra_traders = :e WHERE id = :i"),
                                 {"e": ",".join(names), "i": gid})
        sess.commit()
        clear_fee_discount_cache()
        return True, f"已把「{old}」改成「{new}」（交易 {n.get('trades', 0)} 筆、分頁 {n.get('portfolio_groups', 0)} 個一起改）"
    except Exception as e:
        sess.rollback()
        return False, str(e)
    finally:
        if own:
            sess.close()
