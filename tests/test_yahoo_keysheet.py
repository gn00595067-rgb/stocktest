# -*- coding: utf-8 -*-
"""收盤 Yahoo key 單：數字要讓 Yahoo（先進先出、無稅費）算出跟系統一樣的已實現與庫存。"""
import os
import sys
from datetime import date
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from reports.yahoo_keysheet import build_keysheet, key_steps

P = "CUSTOM_PLUS_FIFO"
D0, D1, D2 = date(2026, 11, 4), date(2026, 11, 16), date(2026, 11, 17)


def _t(id, d, side, price, qty, sid="2313"):
    return NS(id=id, user="P", stock_id=sid, trade_date=d, side=side, price=price, quantity=qty)


def _huatong():
    """Elsa 11/16 華通：舊庫存 4000@196.75；今天買 3 張、賣 3000@203＋1000@203.5。
    系統配對：3000@203 沖今天 3 張（當沖），1000@203.5 沖舊庫存。"""
    trades = [
        _t(1, D0, "BUY", 196.75, 4000),
        _t(2, D1, "BUY", 195, 1000),
        _t(3, D1, "BUY", 199, 1000),
        _t(4, D1, "BUY", 199.5, 1000),
        _t(5, D1, "SELL", 203, 3000),
        _t(6, D1, "SELL", 203.5, 1000),
    ]
    rules = [(5, 2, 1000), (5, 3, 1000), (5, 4, 1000), (6, 1, 1000)]
    return trades, rules


def test_huatong_numbers():
    trades, rules = _huatong()
    (r,) = build_keysheet(trades, D1, P, rules)
    assert (r["prev_qty"], r["prev_avg"]) == (4000, 196.75)
    assert (r["buy_qty"], r["sell_qty"]) == (3000, 4000)
    assert (r["old_sold_qty"], r["old_sold_avg"]) == (1000, 196.75)
    assert r["dt_sold_qty"] == 3000 and r["dt_sold_avg"] == pytest.approx(197.8333, abs=1e-4)
    assert (r["end_qty"], r["end_avg"]) == (3000, 196.75)
    assert r["realized_gross"] == pytest.approx(812500 - 790250)
    # 驗算：前一天成本 − 沖掉 ＋ 今天買 ＝ 收盤後成本
    assert r["prev_qty"] * r["prev_avg"] - r["sold_qty"] * r["sold_avg"] + r["buy_qty"] * r["buy_avg"] == pytest.approx(
        r["end_qty"] * r["end_avg"])
    assert abs(r["yahoo_realized"] - r["realized_gross"]) <= r["sold_qty"] * 0.005


def _yahoo_fifo(rows):
    """模擬 Yahoo：rows=[(date, seq, side, qty, price)]，賣出扣日期最早（同日依 seq）的買進。"""
    lots, realized = [], 0.0
    for d, seq, side, q, p in sorted(rows, key=lambda x: (x[0], x[1])):
        if side == "BUY":
            lots.append([q, p])
            continue
        while q:
            take = min(q, lots[0][0])
            realized += (p - lots[0][1]) * take
            lots[0][0] -= take
            q -= take
            if not lots[0][0]:
                lots.pop(0)
    qty = sum(l[0] for l in lots)
    return realized, qty, (sum(l[0] * l[1] for l in lots) / qty if qty else 0)


def test_two_days_chain_matches_system_in_yahoo():
    """照 key 單連做兩天：Yahoo 累計已實現、庫存均價都跟系統一致（差在 2 位小數取整內）。"""
    trades, rules = _huatong()
    trades += [_t(7, D2, "BUY", 190, 2000), _t(8, D2, "SELL", 200, 2000)]
    rules += [(8, 1, 2000)]   # 隔天賣的沖舊庫存，今天買的留著

    yahoo = [(D0, 0, "BUY", 4000, 196.75)]  # 前一天那一包
    total_sys = 0.0
    for day in (D1, D2):
        (r,) = build_keysheet(trades, day, P, rules)
        total_sys += r["realized_gross"]
        # 「原本那一包」＝最後一筆還沒被賣過的買入：改成 A（日期不動）
        pkg = max(i for i, x in enumerate(yahoo) if x[2] == "BUY")
        pd_, ps = yahoo[pkg][0], yahoo[pkg][1]
        yahoo[pkg] = (pd_, ps, "BUY", r["sold_qty"], round(r["sold_avg"], 2))
        yahoo.append((day, 1, "BUY", r["end_qty"], round(r["end_avg"], 2)))
        yahoo.append((day, 2, "SELL", r["sell_qty"], round(r["sell_avg"], 2)))

    y_real, y_qty, y_avg = _yahoo_fifo(yahoo)
    (last,) = build_keysheet(trades, D2, P, rules)
    assert y_qty == last["end_qty"] == 3000
    assert y_avg == pytest.approx(last["end_avg"], abs=0.005)
    assert y_real == pytest.approx(total_sys, abs=50)


def test_later_trades_ignored_and_only_today_stocks():
    trades, rules = _huatong()
    trades += [_t(9, D0, "BUY", 500, 1000, sid="3105"), _t(10, D2, "SELL", 600, 1000)]
    rows = build_keysheet(trades, D1, P, rules)
    assert [r["stock_id"] for r in rows] == ["2313"]   # 3105 今天沒交易
    assert rows[0]["sell_qty"] == 4000                  # 11/17 的賣出不算


def test_steps_text():
    trades, rules = _huatong()
    (r,) = build_keysheet(trades, D1, P, rules)
    s = key_steps(r, D1)
    assert "刪掉" in s[0]
    assert "4,000 股＠196.75" in s[1] and "4,000 股＠197.56" in s[1]
    assert "3,000 股＠196.75" in s[2]
    assert "4,000 股＠203.12" in s[3] or "4,000 股＠203.13" in s[3]
