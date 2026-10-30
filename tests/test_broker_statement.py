# -*- coding: utf-8 -*-
"""券商對帳單匯入（docs/specs/對帳單匯入.md）：解析、只補沒 key 的、當沖、賣出統一沖銷。"""
from dataclasses import dataclass
from datetime import date
from typing import Optional

from services.broker_statement import (
    DUP_IMPORT, KEYED_EXACT, KEYED_MERGED, NEW, REVIEW,
    assign_tmp_ids, build_name_map, classify_rows, parse_statement, plan_daytrade, plan_sell_matches,
)

NAMES = build_name_map([("2303", "聯電"), ("2313", "華通"), ("3037", "欣興"), ("4958", "臻鼎-KY"),
                        ("0056", "元大高股息"), ("00981A", "主動統一台股增長")])
D28, D29, D30 = date(2026, 10, 28), date(2026, 10, 29), date(2026, 10, 30)


def rec(name, d, qty, net, price, fee, tax, order_no, cur="台幣"):
    return {"股名": name, "日期": d, "成交股數": qty, "淨收付": net, "成交單價": price, "成交價金": "",
            "手續費": fee, "交易稅": tax, "稅款": "0", "委託書號": order_no, "幣別": cur, "備註": ""}


@dataclass
class T:
    id: int
    stock_id: str
    trade_date: date
    side: str
    price: float
    quantity: int
    fee: Optional[float] = None
    tax: Optional[float] = None
    user: str = "Peggy姐"


def test_parse_sample_format():
    recs = [   # 跟證券 App 匯出的一樣：新到舊、千分位、零股、淨收付負＝買
        rec("聯電", "2026/10/29", "1,000", "-115,546", "115.5", "46", "0", "A4233"),
        rec("欣興", "2026/10/29", "50", "-53,021", "1060", "21", "0", "k01C4"),
        rec("臻鼎－KY", "2026/10/29", "200", "-105,241", "526", "41", "0", "k028y"),   # 全形破折號也要對得到
        rec("華通", "2026/10/28", "1,000", "-226,090", "226", "90", "0", "A2826"),
        rec("元大高股息", "2026/10/27", "17", "-959", "56.4", "1", "0", "p016u"),
        rec("主動統一台股增長", "2026/10/27", "36", "-992", "27.53", "1", "0", "p02WJ"),
        rec("聯電", "2026/10/30", "1,000", "118,280", "118.5", "47", "173", "B0001"),       # 正＝賣
    ]
    rows, errors = parse_statement(recs, NAMES)
    assert errors == []
    assert [r["stock_id"] for r in rows] == ["0056", "00981A", "2313", "2303", "3037", "4958", "2303"]  # 日期舊→新
    r = rows[3]
    assert (r["side"], r["quantity"], r["price"], r["fee"], r["tax"], r["order_no"]) == ("BUY", 1000, 115.5, 46, 0, "A4233")
    assert rows[0]["quantity"] == 17 and rows[0]["stock_id"] == "0056"
    assert rows[-1]["side"] == "SELL" and rows[-1]["tax"] == 173


def test_parse_errors():
    recs = [rec("不存在", "2026/10/29", "1", "-1", "1", "1", "0", "X1"),
            rec("聯電", "2026/10/29", "1,000", "-115,546", "115.5", "46", "0", "A1", cur="美元"),
            rec("聯電", "2026/10/29", "1,000", "-115,546", "115.5", "46", "0", "A2"),
            rec("聯電", "2026/10/29", "1,000", "-115,546", "115.5", "46", "0", "A2")]   # 拼檔重複
    rows, errors = parse_statement(recs, NAMES)
    assert len(rows) == 1
    assert [e[0] for e in errors] == [2, 3, 5]
    assert parse_statement([{"股名": "聯電"}], NAMES)[1][0][1].startswith("不是證券 App")


def _rows(*specs):
    """(stock_id, date, side, price, qty, order_no, fee, tax)"""
    out = []
    for i, (sid, d, side, price, qty, o, *ft) in enumerate(specs):
        fee, tax = (ft + [0, 0])[:2] if ft else (0, 0)
        out.append({"row_no": i + 2, "seq": i, "stock_id": sid, "stock_name": sid, "trade_date": d, "side": side,
                    "price": price, "quantity": qty, "fee": fee, "tax": tax, "order_no": o, "note": None})
    return out


def _status(rows):
    return [r["status"] for r in rows]


def test_all_new_then_dup_import():
    rows = _rows(("2303", D29, "BUY", 115.5, 1000, "A1"), ("2303", D29, "BUY", 115.5, 1000, "A2"))
    assert _status(classify_rows(rows, [], [])) == [NEW, NEW]
    trades = [T(1, "2303", D29, "BUY", 115.5, 1000), T(2, "2303", D29, "BUY", 115.5, 1000)]
    refs = [(D29, "A1", 1), (D29, "A2", 2)]
    assert _status(classify_rows(rows, trades, refs)) == [DUP_IMPORT, DUP_IMPORT]
    # 交易被刪掉 → 書號不算，可以重匯
    assert _status(classify_rows(rows, trades[:1], refs)) == [DUP_IMPORT, NEW]


def test_keyed_exact_by_count_and_fee_update():
    rows = _rows(*[("2303", D29, "BUY", 115.5, 1000, f"A{i}", 46) for i in range(3)])
    trades = [T(10, "2303", D29, "BUY", 115.5, 1000, fee=49.0)]   # 手 key 用折數算的手續費
    out = classify_rows(rows, trades, [])
    assert _status(out) == [KEYED_EXACT, NEW, NEW]
    assert out[0]["claim_trade_id"] == 10
    assert out[0]["fee_update"] == {"trade_id": 10, "old_fee": 49.0, "old_tax": 0.0, "fee": 46, "tax": 0}


def test_keyed_merged():
    rows = _rows(("4958", D29, "BUY", 530, 1000, "A1"), ("4958", D29, "BUY", 526, 200, "k1", 41),
                 ("4958", D29, "BUY", 525, 300, "k2", 10))
    out = classify_rows(rows, [T(7, "4958", D29, "BUY", 529.0, 1500, fee=250)], [])
    assert _status(out) == [KEYED_MERGED] * 3
    assert {r["claim_trade_id"] for r in out} == {7}
    assert out[0]["fee_update"]["fee"] == 51


def test_mismatch_goes_to_review():
    rows = _rows(("2313", D29, "BUY", 215, 1000, "A1"), ("2303", D29, "BUY", 115.5, 1000, "A2"))
    out = classify_rows(rows, [T(5, "2313", D29, "BUY", 210, 1000)], [])   # 手 key 打錯價格
    assert _status(out) == [REVIEW, NEW]
    assert out[0]["review_with"] == "210 × 1,000"


def test_other_trader_not_affecting():
    # classify_rows 只拿得到這個買賣人的交易；別人的交易不會傳進來 → 全部新增
    rows = _rows(("2303", D29, "BUY", 115.5, 1000, "A1"))
    assert _status(classify_rows(rows, [], [])) == [NEW]


def test_already_ref_trade_not_claimed_again():
    # 上一份對帳單匯入的交易（已有書號）不會被另一個書號再認領
    rows = _rows(("2303", D29, "BUY", 115.5, 1000, "A9"))
    trades = [T(1, "2303", D29, "BUY", 115.5, 1000)]
    assert _status(classify_rows(rows, trades, [(D29, "A1", 1)])) == [NEW]


def test_daytrade_partial():
    rows = assign_tmp_ids(_rows(("2303", D29, "BUY", 115, 1000, "A1"), ("2303", D29, "BUY", 116, 1000, "A2"),
                                ("2303", D29, "SELL", 117, 1000, "A3")))
    rules, flagged = plan_daytrade(rows, [], [])
    a1, a2, a3 = (r["tmp_id"] for r in rows)
    assert rules == [(a3, a1, 1000)]
    assert flagged == {a1, a3}


def test_daytrade_with_existing_manual_buy():
    rows = assign_tmp_ids(_rows(("2303", D29, "SELL", 117, 1000, "A3")))
    trades = [T(3, "2303", D29, "BUY", 115, 1000), T(4, "2303", D28, "BUY", 100, 1000)]
    rules, flagged = plan_daytrade(rows, trades, [])
    assert rules == [(rows[0]["tmp_id"], 3, 1000)]
    assert flagged == {3, rows[0]["tmp_id"]}
    # 已手 key 的當天買賣（都是既有的）不動
    assert plan_daytrade([], [T(1, "2303", D29, "BUY", 1, 1), T(2, "2303", D29, "SELL", 1, 1)], []) == ([], set())


def _hwa_tong():
    trades = [T(1, "2313", D28, "BUY", 226, 1000), T(2, "2313", D28, "BUY", 225, 1000),
              T(3, "2313", D29, "BUY", 215, 1000)]
    rows = assign_tmp_ids(_rows(("2313", D30, "SELL", 230, 1000, "S1")))
    return trades, rows


def test_sell_strategies():
    trades, rows = _hwa_tong()
    sid = rows[0]["tmp_id"]
    assert plan_sell_matches(rows, trades, [], "FIFO") == ([(sid, 1, 1000)], {})
    assert plan_sell_matches(rows, trades, [], "CONSERVATIVE") == ([(sid, 1, 1000)], {})
    assert plan_sell_matches(rows, trades, [], "OPTIMISTIC") == ([(sid, 3, 1000)], {})
    assert plan_sell_matches(rows, trades, [], "NONE") == ([], {})


def test_sell_skips_lots_used_by_existing_sells_and_reports_shortage():
    trades, rows = _hwa_tong()
    trades.append(T(4, "2313", D29, "SELL", 220, 2000))          # 既有賣出（無規則）先進先出吃掉 1、2
    rows[0]["quantity"] = 1500
    rules, short = plan_sell_matches(rows, trades, [], "FIFO")
    assert rules == [(rows[0]["tmp_id"], 3, 1000)]
    assert short == {rows[0]["tmp_id"]: 500}


def test_sell_after_daytrade_rules():
    # 當天買 1 張、賣 2 張：1 張當沖，另 1 張從舊庫存沖
    trades = [T(1, "2303", D28, "BUY", 100, 1000)]
    rows = assign_tmp_ids(_rows(("2303", D29, "BUY", 115, 1000, "A1"), ("2303", D29, "SELL", 117, 2000, "A2")))
    dt_rules, _ = plan_daytrade(rows, trades, [])
    b, s = rows[0]["tmp_id"], rows[1]["tmp_id"]
    assert dt_rules == [(s, b, 1000)]
    rules, short = plan_sell_matches(rows, trades, dt_rules, "FIFO")
    assert rules == [(s, 1, 1000)] and short == {}


def test_cross_user_dup():
    from services.broker_statement import mark_cross_user_dups
    rows = _rows(("2303", D29, "BUY", 115.5, 1000, "A1"), ("2303", D29, "BUY", 115.5, 1000, "A2"),
                 ("2313", D29, "BUY", 215, 1000, "A3"))
    live = [(D29, "A1", "雅雲", "2303", "BUY"),      # 上次分給雅雲 → 這次不論選誰都算已匯入過
            (D29, "A3", "別人", "2330", "BUY")]      # 別的證券帳戶剛好同書號、股票不同 → 不算
    rows = mark_cross_user_dups(rows, live)
    assert [r.get("status") for r in rows] == [DUP_IMPORT, None, None]
    assert rows[0]["dup_user"] == "雅雲"
    out = classify_rows(rows, [], [])                # 已標過的保留
    assert _status(out) == [DUP_IMPORT, NEW, NEW]
