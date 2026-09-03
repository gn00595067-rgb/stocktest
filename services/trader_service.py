# -*- coding: utf-8 -*-
"""買賣人名單服務：列出／新增／刪除，並自動同步到 Google 試算表（透過 commit hook）。"""
from __future__ import annotations

from typing import Tuple

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
