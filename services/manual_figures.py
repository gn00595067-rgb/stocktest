# -*- coding: utf-8 -*-
"""手動數字（期間獲利表）的讀寫。資料表 manual_figures，主鍵 (trader, period_key)。"""
from datetime import datetime
from typing import Dict, Optional


def load_manual_figures(sess, trader: str) -> Dict[str, Optional[float]]:
    from db.models import ManualFigure
    rows = sess.query(ManualFigure).filter(ManualFigure.trader == trader).all()
    return {r.period_key: r.amount for r in rows}


def save_manual_figures(sess, trader: str, values: Dict[str, Optional[float]]) -> int:
    """逐筆 upsert（清空＝存 None，不刪列）；回傳有變動的筆數。呼叫端負責 commit。"""
    from db.models import ManualFigure
    existing = {r.period_key: r for r in
                sess.query(ManualFigure).filter(ManualFigure.trader == trader).all()}
    changed = 0
    for key, val in values.items():
        val = None if val is None or val != val else float(val)   # NaN（表格清空）→ None
        row = existing.get(key)
        if row is None:
            if val is None:
                continue
            sess.add(ManualFigure(trader=trader, period_key=key, amount=val, updated_at=datetime.utcnow()))
            changed += 1
        elif row.amount != val:
            row.amount = val
            row.updated_at = datetime.utcnow()
            changed += 1
    return changed
