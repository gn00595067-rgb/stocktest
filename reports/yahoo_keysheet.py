# -*- coding: utf-8 -*-
"""收盤 Yahoo key 單：把系統的沖銷結果換算成 Yahoo 奇摩投資組合要 key 的數字。

Yahoo 只有日期／股數／成交價、沒有稅費、賣出一律先進先出，所以全部用「不含稅費」價格。
規格：docs/specs/收盤Yahoo_key單.md
"""
from collections import defaultdict
from datetime import date
from typing import Dict, List, Optional, Tuple

from services.pnl_engine import Lot, compute_matches


def _is_buy(t) -> bool:
    return (getattr(t, "side", None) or "").strip().upper() in ("BUY", "配股")


def _avg(cost: float, qty: int) -> float:
    return cost / qty if qty else 0.0


def build_keysheet(
    trades,
    day: date,
    policy: str,
    custom_rules: Optional[List[Tuple[int, int, int]]] = None,
) -> List[Dict]:
    """回傳當天有交易的每檔股票一列（dict），數字皆不含稅費。

    trades 應已篩成同一個 Yahoo 投資組合的買賣人；只用 trade_date <= day 的交易。
    """
    upto = [t for t in trades if t.trade_date <= day]
    today_sids = {t.stock_id for t in upto if t.trade_date == day}
    rows = []
    for sid in sorted(today_sids):
        ts = [t for t in upto if t.stock_id == sid]
        buys = [Lot(t.id, t.quantity, float(t.price), str(t.trade_date)) for t in ts if _is_buy(t)]
        sells = [Lot(t.id, t.quantity, float(t.price), str(t.trade_date)) for t in ts if not _is_buy(t)]
        buys.sort(key=lambda b: (b.date, b.trade_id))
        sells.sort(key=lambda s: (s.date, s.trade_id))
        matches = compute_matches(buys, sells, policy, custom_rules=custom_rules or [])

        day_s = str(day)
        buy_by_id = {b.trade_id: b for b in buys}
        sell_date = {s.trade_id: s.date for s in sells}
        used_before = defaultdict(int)   # 賣出日 < day 的配對，各買進被用掉的股數
        used_today = defaultdict(int)    # 賣出日 = day 的配對
        realized = 0.0
        for buy_id, sell_id, qty, bp, sp, pnl in matches:
            if sell_date.get(sell_id) == day_s:
                used_today[buy_id] += qty
                realized += pnl
            else:
                used_before[buy_id] += qty

        prev_q = prev_c = 0
        old_q = old_c = 0     # 沖掉以前庫存
        dt_q = dt_c = 0       # 沖掉今天買進
        end_q = end_c = 0
        tb_q = tb_c = 0       # 今天買進
        for b in buys:
            used_t = used_today.get(b.trade_id, 0)
            left_after = b.qty - used_before.get(b.trade_id, 0) - used_t
            end_q += left_after
            end_c += left_after * b.price
            if b.date < day_s:
                left_prev = b.qty - used_before.get(b.trade_id, 0)
                prev_q += left_prev
                prev_c += left_prev * b.price
                old_q += used_t
                old_c += used_t * b.price
            else:
                tb_q += b.qty
                tb_c += b.qty * b.price
                dt_q += used_t
                dt_c += used_t * b.price

        ts_today = [s for s in sells if s.date == day_s]
        sell_q = sum(s.qty for s in ts_today)
        sell_c = sum(s.qty * s.price for s in ts_today)
        sold_q = old_q + dt_q
        sold_c = old_c + dt_c

        # Yahoo 會算出的已實現：價格先四捨五入到 2 位，賣出扣 A 那一包
        yahoo_realized = None
        if sold_q:
            yahoo_realized = round(_avg(sell_c, sell_q), 2) * sold_q - round(_avg(sold_c, sold_q), 2) * sold_q

        rows.append({
            "stock_id": sid,
            "prev_qty": prev_q, "prev_avg": _avg(prev_c, prev_q),
            "buy_qty": tb_q, "buy_avg": _avg(tb_c, tb_q),
            "sell_qty": sell_q, "sell_avg": _avg(sell_c, sell_q),
            "old_sold_qty": old_q, "old_sold_avg": _avg(old_c, old_q),
            "dt_sold_qty": dt_q, "dt_sold_avg": _avg(dt_c, dt_q),
            "sold_qty": sold_q, "sold_avg": _avg(sold_c, sold_q),
            "end_qty": end_q, "end_avg": _avg(end_c, end_q),
            "realized_gross": realized,
            "yahoo_realized": yahoo_realized,
            # 賣出股數 > 配到的股數＝庫存不夠（沒有買進可配），Yahoo 也會對不起來
            "unmatched_sell_qty": sell_q - sold_q,
        })
    return rows


def key_steps(r: Dict, day: date) -> List[str]:
    """依 build_keysheet 的一列，產生給人照抄的 Yahoo 步驟（白話）。"""
    d = day.strftime("%Y/%m/%d")
    steps = []
    if r["sell_qty"]:
        if r["buy_qty"]:
            steps.append("刪掉今天盤中 key 的「買入」列（下面已經併好）")
        if r["prev_qty"]:
            steps.append(
                f"把原本那一包（{r['prev_qty']:,} 股＠{r['prev_avg']:.2f}）改成 "
                f"**{r['sold_qty']:,} 股＠{r['sold_avg']:.2f}**，日期不要動"
            )
        else:
            steps.append(
                f"新增買入 **{r['sold_qty']:,} 股＠{r['sold_avg']:.2f}**，日期設成前一天（要比下一筆早，Yahoo 才會先扣它）"
            )
        if r["end_qty"]:
            steps.append(f"新增買入 {d}　**{r['end_qty']:,} 股＠{r['end_avg']:.2f}**（收盤後庫存）")
        steps.append(
            f"賣出：{d}　**{r['sell_qty']:,} 股＠{r['sell_avg']:.2f}**"
            "（盤中逐筆 key 的賣出股數價格都對就保留，不用合併）"
        )
    elif r["buy_qty"]:
        if r["prev_qty"]:
            steps.append("刪掉今天盤中 key 的「買入」列")
            steps.append(
                f"把原本那一包（{r['prev_qty']:,} 股＠{r['prev_avg']:.2f}）改成 "
                f"**{r['end_qty']:,} 股＠{r['end_avg']:.2f}**"
            )
        else:
            steps.append(f"新增買入 {d}　**{r['end_qty']:,} 股＠{r['end_avg']:.2f}**（盤中已逐筆 key 就不用動）")
    return steps
