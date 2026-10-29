# -*- coding: utf-8 -*-
"""股票質借：記「某人某檔目前有幾股質押中」（docs/specs/質借.md）。

質借不是交易：不碰交易、沖銷、損益，只用來顯示可動用股數、賣出時提醒。
比照 sim_state：刻意不放進 db/models.py，第一次用到才建表（部署免 Reboot）；
用 engine 直接寫、不經 ORM Session，不會讓交易快取失效。
"""
from datetime import datetime
from typing import Dict

from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, delete, insert, select

_md = MetaData()
pledges = Table(
    "pledges", _md,
    Column("trader", String, primary_key=True),     # 買賣人
    Column("stock_id", String, primary_key=True),
    Column("shares", Integer, nullable=False),      # 目前質押中的股數
    Column("updated_at", DateTime, nullable=False),
)
_ready = set()


def _ensure_table(engine) -> None:
    if id(engine) not in _ready:
        _md.create_all(engine, checkfirst=True)
        _ready.add(id(engine))


def load_pledges(engine, trader: str) -> Dict[str, int]:
    """{股票代號: 質押股數}；沒有質押的不列。"""
    _ensure_table(engine)
    with engine.connect() as conn:
        rows = conn.execute(select(pledges.c.stock_id, pledges.c.shares)
                            .where(pledges.c.trader == trader)).all()
    return {str(s): int(n) for s, n in rows if n}


def save_pledge(engine, trader: str, stock_id: str, shares: int) -> None:
    """設定目前質押股數（覆蓋）；0＝解除，刪掉該列。"""
    _ensure_table(engine)
    shares = max(0, int(shares or 0))
    with engine.begin() as conn:
        conn.execute(delete(pledges).where((pledges.c.trader == trader) & (pledges.c.stock_id == stock_id)))
        if shares:
            conn.execute(insert(pledges).values(trader=trader, stock_id=stock_id, shares=shares,
                                                updated_at=datetime.utcnow()))


def pledge_sell_warning(sell_qty: int, holding_qty: int, pledged: int):
    """賣出股數超過可動用（持股－質押）時回傳提醒文字，否則 None。"""
    if not pledged or not sell_qty:
        return None
    usable = max(0, int(holding_qty) - int(pledged))
    if int(sell_qty) <= usable:
        return None
    return (f"這檔有 {int(pledged):,} 股質押中，可動用只有 {usable:,} 股，這次賣出 {int(sell_qty):,} 股。"
            "若已還款解除，記得到上方把質押股數改掉。")


def pledge_label(holding_qty: int, pledged: int) -> list:
    """持股表「持有股數」下的小字（每個元素一行，欄位窄放不下一行）；沒有質押回傳空串列。"""
    if not pledged:
        return []
    if pledged > holding_qty:
        return [f"質押 {pledged:,}", "超過持股，請確認"]
    return [f"質押 {pledged:,}", f"可動用 {holding_qty - pledged:,}"]
