# -*- coding: utf-8 -*-
"""股票輸入（仿 Yahoo）的分頁：存取分頁條件、依條件篩交易、計算分頁內持股與損益。

分頁＝存起來的篩選條件（買賣人、日期範圍、股票清單），交易仍只有一本總帳。
計算沿用全站同一套沖銷引擎（自定沖銷＋其餘先進先出），以該買賣人全部交易配對後，只取「買進符合條件」的批次。
規格：docs/specs/股票輸入_仿Yahoo.md
"""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Dict, List, Optional, Tuple

from services.pnl_engine import Lot, compute_matches, net_pnl_for_match

ALL_GROUP_ID = 0          # 不存表的後備分頁 id
NAME_MAX_LEN = 20


@dataclass
class GroupSpec:
    id: int
    trader: str
    name: str
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    stock_ids: List[str] = field(default_factory=list)
    only_listed: bool = False
    sort_order: int = 0
    owner: Optional[str] = None

    @property
    def is_full(self) -> bool:
        """這個人的全部帳（沒設日期、沒限定股票）＝原本的「全部」。"""
        return not self.start_date and not self.end_date and not self.only_listed

    def describe(self) -> str:
        """一行說明分頁條件，顯示在分頁下方。"""
        if self.is_full:
            return f"{self.trader} 的全部交易"
        parts = [self.trader]
        if self.start_date and self.end_date:
            parts.append(f"{self.start_date:%Y/%m/%d}～{self.end_date:%Y/%m/%d} 買進")
        elif self.start_date:
            parts.append(f"{self.start_date:%Y/%m/%d} 起買進")
        elif self.end_date:
            parts.append(f"到 {self.end_date:%Y/%m/%d} 買進")
        if self.only_listed:
            parts.append(f"只看 {len(self.stock_ids)} 檔")
        return "・".join(parts)


def parse_stock_ids(text: Optional[str]) -> List[str]:
    seen, out = set(), []
    for s in str(text or "").replace("，", ",").split(","):
        s = s.strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _to_spec(row) -> GroupSpec:
    return GroupSpec(
        id=int(row.id), trader=row.trader, name=row.name,
        start_date=row.start_date, end_date=row.end_date,
        stock_ids=parse_stock_ids(row.stock_ids), only_listed=bool(row.only_listed),
        sort_order=int(row.sort_order or 0), owner=getattr(row, "owner", None),
    )


def builtin_all_group(trader: str) -> GroupSpec:
    """不存表的「某人全部帳」（測試與找不到分頁時的後備）。"""
    return GroupSpec(id=ALL_GROUP_ID, trader=trader, name=trader)


# ─── 存取（session 由呼叫端傳入，方便測試）；分頁屬於登入帳號（owner） ──────────

def _owner_rows(sess, owner: str):
    from db.models import PortfolioGroup
    return (sess.query(PortfolioGroup).filter(PortfolioGroup.owner == owner)
            .order_by(PortfolioGroup.sort_order, PortfolioGroup.id).all())


def list_groups(sess, owner: str, can_access: Optional[Callable[[str], bool]] = None) -> List[GroupSpec]:
    """這個登入帳號的分頁（依排序）；沒有權限的買賣人的分頁不列出。"""
    out = [_to_spec(r) for r in _owner_rows(sess, owner)]
    return [g for g in out if can_access is None or can_access(g.trader)]


def ensure_owner_groups(sess, owner: str, default_trader: str,
                        can_access: Optional[Callable[[str], bool]] = None) -> None:
    """帳號還沒有任何（看得到的）分頁時：認領沒有擁有者的舊分頁，並建一個預設買賣人的全部帳分頁放第一個。"""
    from db.models import PortfolioGroup
    if list_groups(sess, owner, can_access):
        return
    ok = (lambda t: True) if can_access is None else can_access
    orphans = (sess.query(PortfolioGroup).filter(PortfolioGroup.owner.is_(None))
               .order_by(PortfolioGroup.sort_order, PortfolioGroup.id).all())
    k = 1
    for r in orphans:
        if ok(r.trader):
            r.owner = owner
            r.sort_order = k
            k += 1
    sess.add(PortfolioGroup(owner=owner, trader=default_trader, name=_free_name(sess, owner, default_trader),
                            stock_ids="", only_listed=False, sort_order=0))
    sess.commit()


def _free_name(sess, owner: str, base: str) -> str:
    names = {r.name for r in _owner_rows(sess, owner)}
    if base not in names:
        return base
    i = 2
    while f"{base}{i}" in names:
        i += 1
    return f"{base}{i}"


def validate_group(sess, owner: str, name: str, start_date, end_date,
                   exclude_id: Optional[int] = None, trader: Optional[str] = None) -> Optional[str]:
    """回傳錯誤訊息；沒問題回傳 None。"""
    from db.models import PortfolioGroup
    name = (name or "").strip()
    if not name:
        return "請輸入分頁名稱。"
    if len(name) > NAME_MAX_LEN:
        return f"分頁名稱最多 {NAME_MAX_LEN} 字。"
    if trader is not None and not (trader or "").strip():
        return "請選擇這個分頁是誰的帳。"
    if start_date and end_date and start_date > end_date:
        return "起始日不能晚於結束日。"
    q = sess.query(PortfolioGroup).filter(PortfolioGroup.owner == owner, PortfolioGroup.name == name)
    if exclude_id is not None:
        q = q.filter(PortfolioGroup.id != exclude_id)
    if q.first() is not None:
        return f"已經有叫「{name}」的分頁了。"
    return None


def create_group(sess, owner: str, trader: str, name: str, start_date=None, end_date=None,
                 stock_ids: Optional[List[str]] = None, only_listed: bool = False) -> Tuple[Optional[int], Optional[str]]:
    from db.models import PortfolioGroup
    err = validate_group(sess, owner, name, start_date, end_date, trader=trader)
    if err:
        return None, err
    last = (sess.query(PortfolioGroup).filter(PortfolioGroup.owner == owner)
            .order_by(PortfolioGroup.sort_order.desc()).first())
    row = PortfolioGroup(
        owner=owner, trader=trader.strip(), name=name.strip(), start_date=start_date, end_date=end_date,
        stock_ids=",".join(parse_stock_ids(",".join(stock_ids or []))),
        only_listed=bool(only_listed) and bool(stock_ids),
        sort_order=(int(last.sort_order) + 1) if last else 1,
    )
    sess.add(row)
    sess.commit()
    return int(row.id), None


def update_group(sess, group_id: int, name: str, start_date, end_date,
                 stock_ids: List[str], only_listed: bool, trader: Optional[str] = None) -> Optional[str]:
    from db.models import PortfolioGroup
    row = sess.get(PortfolioGroup, int(group_id))
    if row is None:
        return "找不到這個分頁（可能已被刪除）。"
    err = validate_group(sess, row.owner, name, start_date, end_date, exclude_id=row.id, trader=trader)
    if err:
        return err
    ids = parse_stock_ids(",".join(stock_ids or []))
    row.name = name.strip()
    if trader:
        row.trader = trader.strip()
    row.start_date = start_date
    row.end_date = end_date
    row.stock_ids = ",".join(ids)
    row.only_listed = bool(only_listed) and bool(ids)
    sess.commit()
    return None


def delete_group(sess, group_id: int) -> None:
    """只刪分頁條件，絕不動交易。"""
    from db.models import PortfolioGroup
    row = sess.get(PortfolioGroup, int(group_id))
    if row is not None:
        sess.delete(row)
        sess.commit()


def move_group(sess, owner: str, group_id: int, direction: int) -> None:
    """direction = -1 往左、+1 往右。"""
    rows = _owner_rows(sess, owner)
    idx = next((i for i, r in enumerate(rows) if r.id == group_id), None)
    if idx is None:
        return
    j = idx + direction
    if not (0 <= j < len(rows)):
        return
    rows[idx], rows[j] = rows[j], rows[idx]
    for k, r in enumerate(rows, start=1):
        r.sort_order = k
    sess.commit()


def add_stock_to_group(sess, group_id: int, stock_id: str) -> None:
    """把股票加進分頁清單（全部帳分頁：多顯示一列 0 股；只看清單的分頁：加入清單）。"""
    from db.models import PortfolioGroup
    row = sess.get(PortfolioGroup, int(group_id))
    if row is None:
        return
    ids = parse_stock_ids(row.stock_ids)
    if stock_id not in ids:
        ids.append(stock_id)
        row.stock_ids = ",".join(ids)
        sess.commit()


# ─── 篩選與計算 ───────────────────────────────────────────────────────────

def trade_in_group(t, group: GroupSpec) -> bool:
    if (getattr(t, "user", "") or "").strip() != (group.trader or "").strip():
        return False
    d = t.trade_date
    if group.start_date and d < group.start_date:
        return False
    if group.end_date and d > group.end_date:
        return False
    if group.only_listed and str(t.stock_id).strip() not in set(group.stock_ids):
        return False
    return True


def filter_trades(trades, group: GroupSpec) -> list:
    return [t for t in trades if trade_in_group(t, group)]


def _is_buy(t) -> bool:
    return (getattr(t, "side", None) or "").strip().upper() in ("BUY", "配股")


def summarize_group(trades, group: GroupSpec, custom_rules, policy: str, quotes: Dict[str, dict],
                    masters: dict, extra_stock_ids: Optional[List[str]] = None) -> dict:
    """分頁內每檔的持股、均價、底價、市值、已實現、未實現。

    口徑「以買進歸屬」（2026-10-07 Peggy 確認）：沖銷用該買賣人「全部」交易照全站引擎配對
    （自定沖銷＋其餘先進先出），分頁只算「買進符合條件」的那幾批——
      - 持股／均價＝這些買進批次剩下的股數與成本；
      - 已實現＝賣出沖到這些批次的部分（不管賣出日期）；沖到分頁以外舊批次的賣出不算。
    所以「全部」分頁與庫存損益頁同數字；「10/06 起」只看 10/06 起買進的那幾批。

    trades：該買賣人的全部交易（不需先篩分頁；這裡會依 group 篩）。
    """
    from services.trade_fees import breakeven_sell_price

    trader = (group.trader or "").strip()
    mine = [t for t in trades if (getattr(t, "user", "") or "").strip() == trader]
    in_group = [t for t in mine if trade_in_group(t, group)]
    group_ids = {t.id for t in in_group}
    by_stock = defaultdict(list)
    for t in mine:
        by_stock[str(t.stock_id).strip()].append(t)
    n_by_stock = defaultdict(int)
    for t in in_group:
        n_by_stock[str(t.stock_id).strip()] += 1
    trade_by_id = {t.id: t for t in mine}

    rows = []
    for sid in set(n_by_stock) | set(extra_stock_ids or []):
        ts = sorted(by_stock.get(sid, []), key=lambda t: (t.trade_date, t.id))
        buys = [Lot(t.id, int(t.quantity or 0), float(t.price or 0), str(t.trade_date)) for t in ts if _is_buy(t)]
        sells = [Lot(t.id, int(t.quantity or 0), float(t.price or 0), str(t.trade_date)) for t in ts if not _is_buy(t)]
        matches = compute_matches(buys, sells, policy, custom_rules=custom_rules or []) if sells else []
        g_matches = [m for m in matches if m[0] in group_ids]
        realized = sum(net_pnl_for_match(m, trade_by_id) for m in g_matches)
        realized_cost = 0.0
        matched_by_buy = defaultdict(int)
        for m in matches:
            matched_by_buy[m[0]] += int(m[2])
        for m in g_matches:
            bt = trade_by_id.get(m[0])
            qty_m = int(m[2])
            fee_share = float(getattr(bt, "fee", 0) or 0) * (qty_m / bt.quantity) if bt and bt.quantity else 0.0
            realized_cost += float(m[3]) * qty_m + fee_share
        qty, cost = 0, 0.0
        for b in buys:
            if b.trade_id not in group_ids:
                continue
            rem = int(b.qty) - matched_by_buy.get(b.trade_id, 0)
            if rem <= 0:
                continue
            bt = trade_by_id.get(b.trade_id)
            fee = float(getattr(bt, "fee", 0) or 0)
            qty += rem
            cost += rem * float(b.price) + (fee * rem / b.qty if b.qty else 0.0)

        q = quotes.get(sid) or {}
        avg = cost / qty if qty else 0.0
        price = float(q["price"]) if q.get("price") else (avg if qty else 0.0)
        mv = price * qty
        unrealized = (price - avg) * qty if qty else 0.0
        m = masters.get(sid)
        is_etf = bool(getattr(m, "is_etf", False)) if m else False
        rows.append({
            "stock_id": sid,
            "name": (getattr(m, "name", None) or sid) if m else sid,
            "exchange": getattr(m, "exchange", None) if m else None,
            "price": price if q.get("price") else None,
            "change": float(q.get("change", 0) or 0),
            "change_pct": float(q.get("change_pct", 0) or 0),
            "qty": qty,
            "avg_cost": avg,
            "cost": cost,
            "breakeven": breakeven_sell_price(cost, qty, is_etf=is_etf) if qty else None,
            "market_value": mv,
            "realized": realized,
            "realized_pct": (realized / realized_cost * 100) if realized_cost else None,
            "has_realized": bool(g_matches),
            "unrealized": unrealized,
            "unrealized_pct": (unrealized / cost * 100) if qty and cost else None,
            "n_trades": n_by_stock.get(sid, 0),
        })
    rows.sort(key=lambda r: (-r["market_value"], -r["n_trades"], r["stock_id"]))

    tot_mv = sum(r["market_value"] for r in rows)
    tot_cost = sum(r["cost"] for r in rows if r["qty"])
    tot_unreal = sum(r["unrealized"] for r in rows)
    tot_real = sum(r["realized"] for r in rows)
    tot_real_cost = 0.0
    for r in rows:
        if r["realized_pct"]:
            tot_real_cost += r["realized"] / (r["realized_pct"] / 100)
    return {
        "rows": rows,
        "market_value": tot_mv,
        "unrealized": tot_unreal,
        "unrealized_pct": (tot_unreal / tot_cost * 100) if tot_cost else None,
        "realized": tot_real,
        "realized_pct": (tot_real / tot_real_cost * 100) if tot_real_cost else None,
    }


# ─── 防呆 ─────────────────────────────────────────────────────────────────

def validate_new_trade(side: str, qty, price, prev_close: Optional[float], holding_qty: int,
                       is_today: bool = True) -> Tuple[List[str], List[str]]:
    """回傳 (擋下的錯誤, 只提醒的警告)。

    holding_qty：該買賣人這檔目前的總持股（全部交易，不是分頁內）。
    is_today：交易日是今天才檢查漲跌停（補登過去的交易時，今天的昨收不適用）。
    """
    errors, warnings = [], []
    try:
        qty = int(qty or 0)
    except (TypeError, ValueError):
        qty = 0
    try:
        price = float(price or 0)
    except (TypeError, ValueError):
        price = 0.0
    if qty <= 0:
        errors.append("請輸入股數（要大於 0）。")
    if price <= 0:
        errors.append("請輸入成交價（要大於 0）。")
    if errors:
        return errors, warnings
    if is_today and prev_close and prev_close > 0:
        lo, hi = prev_close * 0.9, prev_close * 1.1
        if price < lo - 1e-9 or price > hi + 1e-9:
            errors.append(
                f"成交價 {price:,.2f} 超出今日漲跌停範圍（昨收 {prev_close:,.2f}，約 {lo:,.2f}～{hi:,.2f}），"
                "請檢查是否打錯小數點。"
            )
    if side == "SELL" and qty > holding_qty:
        errors.append(f"賣出 {qty:,} 股超過目前持股 {holding_qty:,} 股。")
    if qty % 1000 != 0:
        warnings.append(f"{qty:,} 股不是整張（1000 股的倍數），確定是零股嗎？")
    return errors, warnings


def validate_match_plan(rows: List[Tuple[int, int, int]], sell_qty: int) -> Optional[str]:
    """賣出沖銷配對檢查。rows = [(買進ID, 本次沖銷股數, 該批可沖銷股數)]。回傳錯誤訊息或 None。"""
    total = 0
    for buy_id, q, remaining in rows:
        q = int(q or 0)
        if q < 0:
            return f"買進 ID {buy_id} 的沖銷股數不能是負數。"
        if q > int(remaining):
            return f"買進 ID {buy_id} 只剩 {int(remaining):,} 股可沖銷，不能沖 {q:,} 股。"
        total += q
    if total != int(sell_qty):
        diff = int(sell_qty) - total
        return (f"沖銷股數合計 {total:,} 股，與賣出 {int(sell_qty):,} 股不符"
                f"（{'還差' if diff > 0 else '多了'} {abs(diff):,} 股）。")
    return None


def validate_edit_trade(side: str, old_qty: int, qty, price, prev_close: Optional[float], holding_qty: int,
                        is_today: bool = True) -> Tuple[List[str], List[str]]:
    """修改既有交易的防呆：同新增，但持股要先扣回這筆原本的股數。

    holding_qty：修改前該買賣人這檔的總持股（已含這筆原本的股數）。
    """
    old_qty = int(old_qty or 0)
    if side == "SELL":
        return validate_new_trade(side, qty, price, prev_close, holding_qty + old_qty, is_today)
    errors, warnings = validate_new_trade(side, qty, price, prev_close, holding_qty, is_today)
    if not errors:
        after = holding_qty - old_qty + int(qty)
        if after < 0:
            errors.append(f"改成 {int(qty):,} 股後持股會變成 {after:,} 股（後面已有賣出用到這批），"
                          "請先改或刪後面的賣出。")
    return errors, warnings
