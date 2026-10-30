# -*- coding: utf-8 -*-
"""ETF 股號掉前導 0 的修正（docs/specs/對帳單匯入.md §0）。

股票清單試算表的 stock_id 欄被當數字存，0056 變 56、00878 變 878、006208 變 6208（還跟日揚科技撞號）。
台股 ETF 代號一律 00 開頭（0050～0061 四碼、00xxx 五碼、006xxx／009xxx 六碼），
所以「標成 ETF、純數字、不是 0 開頭」的代號前面補 00 就是原本的代號（2026-10-30 對證交所官方清單核對過）。
個股（is_etf=False）不動：6208 日揚科技本來就是四碼。
"""
import json
from typing import Dict, Iterable

from sqlalchemy import inspect, text


def normalize_stock_id(stock_id, is_etf: bool) -> str:
    sid = str(stock_id or "").strip()
    if is_etf and sid.isdigit() and len(sid) <= 4 and not sid.startswith("0"):
        return "00" + sid
    return sid


def short_etf_id_map(masters, incoming_ids: Iterable[str]) -> Dict[str, str]:
    """主檔（StockMaster 列）裡掉了 0 的 ETF 代號 → 正確代號；只收「正確代號這次確實有送進來」的，避免憑空改名。"""
    incoming = set(incoming_ids)
    out = {}
    for m in masters:
        if not m.is_etf:
            continue
        new = normalize_stock_id(m.stock_id, True)
        if new != m.stock_id and new in incoming:
            out[m.stock_id] = new
    return out


def migrate_stock_ids(session, id_map: Dict[str, str]) -> Dict[str, int]:
    """把交易、配息、分頁、質押、模擬裡的舊代號改成新代號（不 commit，由呼叫端一起 commit）。
    主檔本身由呼叫端處理（刪舊列、upsert 新列）。回傳各表改了幾列。"""
    if not id_map:
        return {}
    from db.models import Cashflow, PortfolioGroup, Trade
    counts = {"trades": 0, "cashflows": 0, "portfolio_groups": 0, "pledges": 0, "sim_states": 0}
    for old, new in id_map.items():
        counts["trades"] += session.query(Trade).filter(Trade.stock_id == old).update(
            {Trade.stock_id: new}, synchronize_session=False)
        counts["cashflows"] += session.query(Cashflow).filter(Cashflow.stock_id == old).update(
            {Cashflow.stock_id: new}, synchronize_session=False)

    # 仿Yahoo 分頁：逗號分隔的股票清單
    for g in session.query(PortfolioGroup).filter(PortfolioGroup.stock_ids.isnot(None)).all():
        parts = [p.strip() for p in str(g.stock_ids).split(",")]
        fixed = [id_map.get(p, p) for p in parts]
        if fixed != parts:
            g.stock_ids = ",".join(fixed)
            counts["portfolio_groups"] += 1

    # 第一次用到才建的表：存在才改
    # 要用 session 自己的連線查：記憶體 SQLite 只有一條共用連線，另開會在歸還時把還沒 commit 的改動 rollback
    tables = set(inspect(session.connection()).get_table_names())
    if "pledges" in tables:
        for old, new in id_map.items():
            # 新代號已有同一人的質押就保留新的（不會發生，保險起見不撞主鍵）
            session.execute(text(
                "DELETE FROM pledges WHERE stock_id = :old AND trader IN "
                "(SELECT trader FROM pledges WHERE stock_id = :new)"), {"old": old, "new": new})
            counts["pledges"] += session.execute(text(
                "UPDATE pledges SET stock_id = :new WHERE stock_id = :old"), {"old": old, "new": new}).rowcount or 0
    if "sim_states" in tables:
        for owner, raw in session.execute(text("SELECT owner, data FROM sim_states")).all():
            try:
                data = json.loads(raw)
            except (TypeError, ValueError):
                continue
            inputs = data.get("inputs") if isinstance(data, dict) else None
            if not isinstance(inputs, dict) or not any(k in id_map for k in inputs):
                continue
            data["inputs"] = {id_map.get(k, k): v for k, v in inputs.items()}
            session.execute(text("UPDATE sim_states SET data = :d WHERE owner = :o"),
                            {"d": json.dumps(data, ensure_ascii=False, sort_keys=True), "o": owner})
            counts["sim_states"] += 1
    return counts
