# -*- coding: utf-8 -*-
"""券商對帳單匯入（docs/specs/對帳單匯入.md）：解析、只補沒 key 的、當沖、賣出統一沖銷。"""
from dataclasses import dataclass
from datetime import date
from typing import Optional

from services.broker_statement import (
    DUP_IMPORT, KEYED_EXACT, KEYED_MERGED, NEW, REVIEW,
    assign_tmp_ids, build_name_map, classify_rows, parse_statement, plan_daytrade, plan_sell_matches,
    daytrade_cap, table_from_lines, plan_review_fix, orphan_keyed_trades,
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


def test_header_after_note_line_and_aliases():
    """國泰：第 1 列是說明、欄名是「淨收付金額」「成交價」、股名帶 *、檔尾空白列。"""
    import csv, io
    text = (
        ",根據您篩選後的結果，總計有33筆資料，當前資料為1-33筆，看更多請至國泰證券app查詢。,,,,\n"
        "帳戶,股名,日期,成交股數,淨收付金額,買賣別,成交價,成本,手續費,交易稅,委託書號\n"
        '豪哥,聯電,2026/10/29,"1,000","-116,046",現買,116,"116,000",46,0,A8859\n'
        '豪哥,國巨*,2026/10/29,200,"-108,043",現買,540,"108,000",43,0,k02hI\n'
        ",,,,,,,,,,\n"
    )
    recs, first = table_from_lines(list(csv.reader(io.StringIO(text))))
    assert first == 3 and len(recs) == 2
    names = build_name_map([("2303", "聯電"), ("2327", "國巨")])
    rows, errors = parse_statement(recs, names, first)
    assert errors == []
    assert [(r["stock_id"], r["price"], r["quantity"], r["side"], r["row_no"]) for r in rows] == [
        ("2303", 116.0, 1000, "BUY", 3), ("2327", 540.0, 200, "BUY", 4)]


def test_header_on_first_line_unchanged():
    recs, first = table_from_lines([["股名", "日期", "成交股數", "淨收付", "成交單價", "委託書號"],
                                    ["聯電", "2026/10/29", "1,000", "-115,546", "115.5", "A1"]])
    assert first == 2 and recs[0]["成交單價"] == "115.5"


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


def test_daytrade_picks_day_buys_by_strategy():
    """Elsa 11/04：當天買比當沖賣多（買 83 張、賣 50 張），沖哪幾筆照選的方式，不是固定照順序。"""
    rows = assign_tmp_ids(_rows(("2303", D29, "BUY", 102, 1000, "A1"), ("2303", D29, "BUY", 104, 1000, "A2"),
                                ("2303", D29, "BUY", 100, 1000, "A3"), ("2303", D29, "SELL", 105, 1000, "A4")))
    a1, a2, a3, a4 = (r["tmp_id"] for r in rows)
    assert plan_daytrade(rows, [], [], "FIFO")[0] == [(a4, a1, 1000)]
    assert plan_daytrade(rows, [], [], "CONSERVATIVE")[0] == [(a4, a2, 1000)]
    assert plan_daytrade(rows, [], [], "OPTIMISTIC")[0] == [(a4, a3, 1000)]
    assert plan_daytrade(rows, [], [], "MEAN")[0] == [(a4, a1, 1000)]
    assert plan_daytrade(rows, [], [], "NONE")[0] == [(a4, a1, 1000)]


def test_daytrade_sell_can_match_old_inventory():
    """當沖賣出改走統一沖銷時（畫面選「以前的庫存也可以」），先進先出會先沖以前幾天的買進。"""
    rows = assign_tmp_ids(_rows(("2303", D29, "BUY", 104, 1000, "A1"), ("2303", D29, "SELL", 105, 1500, "A2")))
    trades = [T(1, "2303", D28, "BUY", 100, 1000)]
    sell, short = plan_sell_matches(rows, trades, [], "FIFO")
    assert sell == [(rows[1]["tmp_id"], 1, 1000), (rows[1]["tmp_id"], rows[0]["tmp_id"], 500)] and short == {}


def test_daytrade_with_existing_manual_buy():
    rows = assign_tmp_ids(_rows(("2303", D29, "SELL", 117, 1000, "A3")))
    trades = [T(3, "2303", D29, "BUY", 115, 1000), T(4, "2303", D28, "BUY", 100, 1000)]
    rules, flagged = plan_daytrade(rows, trades, [])
    assert rules == [(rows[0]["tmp_id"], 3, 1000)]
    assert flagged == {3, rows[0]["tmp_id"]}
    # 已手 key 的當天買賣（都是既有的）不動
    assert plan_daytrade([], [T(1, "2303", D29, "BUY", 1, 1), T(2, "2303", D29, "SELL", 1, 1)], []) == ([], set())


def test_sell_with_normal_tax_is_not_daytrade():
    """緯穎：先賣掉 420 股庫存（稅 0.3%）、再買 6×10 股 → 不是當沖，賣出照先進先出沖舊庫存。"""
    specs = [("6669", D29, "SELL", 1920, 420, "k00xT", 287, 2419)]
    specs += [("6669", D29, "BUY", p, 10, f"k01{i}", 6, 0) for i, p in enumerate([1875, 1880, 1885, 1890, 1875, 1880])]
    rows = assign_tmp_ids(_rows(*specs))
    trades = [T(1, "6669", D28, "BUY", 1800, 420)]
    rules, flagged = plan_daytrade(rows, trades, [])
    assert rules == [] and flagged == set()
    sell, _ = plan_sell_matches(rows, trades, rules, "FIFO")
    assert sell == [(rows[0]["tmp_id"], 1, 420)]


def test_daytrade_cap_by_tax():
    def sell(sid, price, qty, tax):
        return {"side": "SELL", "stock_id": sid, "price": price, "quantity": qty, "tax": tax}
    assert daytrade_cap(sell("6669", 1920, 420, 2419)) == 0          # 0.3% 一般賣出
    assert daytrade_cap(sell("6669", 1920, 420, 1209)) == 420        # 0.15% 當沖
    assert daytrade_cap(sell("2303", 117, 1000, 0)) is None          # 沒稅看不出來
    assert daytrade_cap(sell("0056", 36, 1000, 36)) is None          # ETF 不減半
    assert daytrade_cap(sell("2303", 117, 1000, 260)) is None        # 部分當沖：照舊


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


def test_settlement_by_user_date_sums_net_regardless_of_status():
    from services.broker_statement import settlement_by_user_date
    rows, _ = parse_statement([
        rec("聯電", "2026/10/30", "1000", "-109038", "109", "38", "0", "A1"),
        rec("華通", "2026/10/30", "1000", "49800", "50", "20", "150", "A2"),
        rec("聯電", "2026/10/29", "1000", "-108038", "108", "38", "0", "A3"),
    ], NAMES)
    for r in rows:
        r["user"] = "P"
    rows[0]["status"] = DUP_IMPORT   # 已匯入過的也要算進當天交割款
    s = settlement_by_user_date(rows)
    assert s[("P", D30)] == -109038 + 49800
    assert s[("P", D29)] == -108038


def test_review_fix_and_orphans():
    """盤中先 key、盤後匯入（Peggy 11/06 需求）：key 錯價格的可以照對帳單修正；對帳單沒有的手 key 列出來。"""
    rows = _rows(("2303", D29, "BUY", 115.5, 1000, "A1", 164, 0), ("2303", D29, "BUY", 116, 2000, "A2", 330, 0),
                 ("2330", D29, "BUY", 1000, 1000, "A3", 1425, 0))
    trades = [T(1, "2303", D29, "BUY", 151.5, 3000),    # 打錯價格（股數合計對）
              T(2, "2317", D29, "BUY", 200, 1000),      # 對帳單沒有：打錯股票
              T(3, "2303", D28, "BUY", 100, 1000)]      # 別天的：不算
    out = classify_rows(rows, trades, [])
    assert _status(out) == [REVIEW, REVIEW, NEW]
    assert out[0]["review_trade_ids"] == [1]
    fix = plan_review_fix(out[:2], trades[0])
    assert fix["quantity"] == 3000 and abs(fix["price"] - (115.5 * 1000 + 116 * 2000) / 3000) < 1e-3
    assert fix["fee"] == 494 and fix["qty_changed"] is False and fix["old"] == "151.5 × 3,000"
    assert [t.id for t in orphan_keyed_trades(out, trades, [])] == [2]
    # 有書號的（之前匯入的）不算孤兒
    assert orphan_keyed_trades(out, trades, [(D29, "Z9", 2)]) == []


# ---------------------------------------------------------------- 沒有委託書號的格式（2026-11-09）
from services.broker_statement import (   # noqa: E402
    content_key, cross_user_same_content, has_order_no, same_day_dup_counts,
)

NO_ORDER_LINES = [
    ["成交日期", "\t類別", "\t股票名稱", "\t股數", "\t成交價", "\t價金", "\t手續費", "\t交易稅",
     "\t融資金額/券擔保品", "\t資自備款/券保證金", "\t利息", "\t稅款", "\t券手續費/標借費", "\t淨收付金額"],
    ["2026/11/5", "現股買進", "南亞(1303)", "1000", "262.5", "262500", "93", "0", "", "", "0", "", "0", "-262593"],
    ["2026/11/5", "現股買進", "南亞(1303)", "1000", "256", "256000", "91", "0", "", "", "0", "", "0", "-256091"],
    ["2026/11/5", "現股買進", "南亞(1303)", "1000", "256", "256000", "91", "0", "", "", "0", "", "0", "-256091"],
    ["2026/11/5", "現股買進", "元大高股息(0056)", "17", "56.4", "958", "1", "0", "", "", "0", "", "0", "-959"],
    ["2026/11/4", "現股賣出", "聯電(2303)", "1000", "118.5", "118500", "47", "355", "", "", "0", "", "0", "118098"],
]
D4, D5 = date(2026, 11, 4), date(2026, 11, 5)
IDS = ["1303", "0056", "2303"]


def _parse_no_order(lines=NO_ORDER_LINES):
    recs, first = table_from_lines(lines)
    return recs, *parse_statement(recs, {}, first, master_ids=IDS)


def test_no_order_parse():
    recs, rows, errors = _parse_no_order()
    assert not has_order_no(recs) and errors == []
    assert len(rows) == 5   # 同日完全相同的南亞 256 兩筆都留著（分開成交）
    sell = rows[0]
    assert (sell["trade_date"], sell["stock_id"], sell["stock_name"], sell["side"], sell["tax"]) == (D4, "2303", "聯電", "SELL", 355)
    assert {r["stock_id"] for r in rows} == {"1303", "0056", "2303"}   # 0 開頭代號直接用括號裡的
    assert all(r["order_no"] == "" for r in rows)
    same = {k: v for k, v in same_day_dup_counts(rows).items() if v > 1}
    assert same == {(D5, "1303", "BUY", 256.0, 1000): 2}


def test_no_order_parse_errors():
    lines = NO_ORDER_LINES[:1] + [
        ["2026/11/5", "融資買進", "南亞(1303)", "1000", "256", "256000", "91", "0", "", "", "0", "", "0", "-102091"],
        ["2026/11/5", "現股買進", "台積電(2330)", "1000", "1000", "1000000", "356", "0", "", "", "0", "", "0", "-1000356"],
    ]
    _recs, rows, errors = _parse_no_order(lines)
    assert rows == []
    assert "融資買進" in errors[0][1] and "2330" in errors[1][1]


def test_no_order_reimport_all_found():
    """重匯：上次匯入的交易（沒有書號）靠內容一對一認回來，同日相同的兩筆也各認一筆。"""
    _recs, rows, _ = _parse_no_order()
    trades = [T(i + 1, r["stock_id"], r["trade_date"], r["side"], r["price"], r["quantity"], r["fee"], r["tax"])
              for i, r in enumerate(rows)]
    out = classify_rows(rows, trades, [])
    assert all(r["status"] == KEYED_EXACT for r in out)
    assert len({r["claim_trade_id"] for r in out}) == 5


def test_no_order_same_day_partial():
    """同日相同兩筆、系統裡只有一筆 → 一筆已有、一筆新增。"""
    _recs, rows, _ = _parse_no_order()
    out = classify_rows(rows, [T(1, "1303", D5, "BUY", 256, 1000, 91, 0)], [])
    nanya256 = [r["status"] for r in out if r["stock_id"] == "1303" and r["price"] == 256]
    assert sorted(nanya256) == sorted([KEYED_EXACT, NEW])


def test_no_order_cross_user():
    _recs, rows, _ = _parse_no_order()
    other = [T(9, "1303", D5, "BUY", 256, 1000, user="雅雲"), T(10, "2303", D4, "SELL", 118.5, 1000, user="雅雲")]
    hits = cross_user_same_content(rows, other)
    assert sorted((r["stock_id"], u) for r, u in hits) == [("1303", "雅雲"), ("2303", "雅雲")]   # 南亞 256 兩筆只配到一筆


def test_orphans_whole_range():
    """沒書號的檔＝最早～最晚日期整段：中間沒出現在檔案的日子，手 key 的也列出來。"""
    rows = [dict(trade_date=D4), dict(trade_date=date(2026, 11, 6))]
    mid = T(1, "2303", D5, "BUY", 100, 1000)
    out_of_range = T(2, "2303", date(2026, 11, 7), "BUY", 100, 1000)
    assert orphan_keyed_trades(rows, [mid, out_of_range], []) == []
    assert orphan_keyed_trades(rows, [mid, out_of_range], [], whole_range=True) == [mid]


def test_content_key_trade_and_row():
    r = dict(trade_date=D5, stock_id="1303", side="BUY", price=256.0, quantity=1000)
    assert content_key(r) == content_key(T(1, "1303", D5, "buy", 256, 1000))


def test_sell_overrides_per_sell():
    """Elsa 11/10：統一保守（成本高先沖），其中一筆改樂觀；同股兩筆賣出各用各的方式、不會沖到同一筆。"""
    trades, _ = _hwa_tong()
    rows = assign_tmp_ids(_rows(("2313", D30, "SELL", 230, 1000, "S1"), ("2313", D30, "SELL", 231, 1000, "S2")))
    s1, s2 = rows[0]["tmp_id"], rows[1]["tmp_id"]
    assert plan_sell_matches(rows, trades, [], "CONSERVATIVE") == ([(s1, 1, 1000), (s2, 2, 1000)], {})
    assert plan_sell_matches(rows, trades, [], "CONSERVATIVE", {s1: "OPTIMISTIC"}) == ([(s1, 3, 1000), (s2, 1, 1000)], {})
    # 統一不指定、只有一筆指定 → 只寫那一筆
    assert plan_sell_matches(rows, trades, [], "NONE", {s2: "FIFO"}) == ([(s2, 1, 1000)], {})
    # 個別不指定 → 那筆不寫
    assert plan_sell_matches(rows, trades, [], "FIFO", {s1: "NONE"}) == ([(s2, 1, 1000)], {})


def test_manual_picks_reserve_buys_and_remainder_uses_strategy():
    """自己挑的先佔掉買進（當成已排好的規則傳進去），沒挑滿的照統一方式補，其他賣出不會再沖到被挑的。"""
    from services.broker_statement import available_buys, pending_sells
    trades, _ = _hwa_tong()
    rows = assign_tmp_ids(_rows(("2313", D30, "SELL", 230, 1500, "S1"), ("2313", D30, "SELL", 231, 1000, "S2")))
    s1, s2 = rows[0]["tmp_id"], rows[1]["tmp_id"]
    assert [b.trade_id for b in available_buys(rows[0], rows, trades, [])] == [1, 2, 3]
    picks = [(s1, 3, 1000)]                                   # S1 指定沖 10/29 215 那筆 1 張，差 500 股照統一
    assert [p["remain"] for p in pending_sells(rows, picks)] == [500, 1000]
    rules, short = plan_sell_matches(rows, trades, picks, "CONSERVATIVE")
    assert rules == [(s1, 1, 500), (s2, 1, 500), (s2, 2, 500)] and short == {}
    # 只能挑賣出日（含）以前的買進
    late = assign_tmp_ids(_rows(("2313", D28, "SELL", 230, 1000, "S1")))
    assert [b.trade_id for b in available_buys(late[0], late, trades, [])] == [1, 2]


def test_allocate_picks_whole_stock():
    """Elsa 11/10：聯電賣 5 筆共 25 張，整檔指定沖 11/05 100×10 張、11/04 100×10 張、11/04 101×5 張。"""
    from services.broker_statement import allocate_picks
    sells = [(-5, 5000, "2026-11-10"), (-1, 5000, "2026-11-10"), (-2, 5000, "2026-11-10"),
             (-3, 5000, "2026-11-10"), (-4, 5000, "2026-11-10")]
    picks = [(30, 10000, "2026-11-05"), (10, 10000, "2026-11-04"), (11, 5000, "2026-11-04")]
    rules, left = allocate_picks(sells, picks)
    assert left == 0
    assert rules == [(-5, 10, 5000), (-4, 10, 5000), (-3, 11, 5000), (-2, 30, 5000), (-1, 30, 5000)]
    # 挑的買進比賣出晚 → 分不出去
    assert allocate_picks([(-1, 1000, "2026-11-04")], [(30, 1000, "2026-11-05")]) == ([], 1000)


def test_pick_buys_used_only_by_unruled_old_sells():
    """以前沒寫規則的賣出（頁面用先進先出現算）佔的買進仍可挑；挑走後舊賣出改沖別的，不夠才算擠到。"""
    from services.broker_statement import available_buys, old_sells_short
    D4, D10 = date(2026, 11, 4), date(2026, 11, 10)
    trades = [T(1, "2303", D4, "BUY", 100, 1000), T(2, "2303", D4, "BUY", 105, 1000),
              T(3, "2303", D4, "SELL", 105.5, 1000)]                     # 先進先出會沖到 100 那筆
    rows = assign_tmp_ids(_rows(("2303", D10, "SELL", 102, 1000, "S1")))
    s1 = rows[0]["tmp_id"]
    assert [(b.trade_id, b.qty) for b in available_buys(rows[0], rows, trades, [])] == [(1, 1000), (2, 1000)]
    # 寫死的規則才真的佔掉
    assert [b.trade_id for b in available_buys(rows[0], rows, trades, [(3, 1, 1000)])] == [2]
    # 挑 100 那筆 → 舊賣出改沖 105，不會沖不到；統一沖銷也不會再沖到 100
    assert old_sells_short("2303", rows, trades, [(s1, 1, 1000)]) == 0
    assert plan_sell_matches(rows, trades, [(s1, 1, 1000)], "FIFO") == ([], {})
    # 庫存只夠一筆：挑走後舊賣出沖不到
    assert old_sells_short("2303", rows, trades[:1] + trades[2:], [(s1, 1, 1000)]) == 1000


def test_overflow_split_to_other_user():
    """Elsa 11/11：短線帳賣 5340 但沒庫存（是 Peggy姐 10 月買的）→ 不夠的拆給 Peggy姐。"""
    from services.broker_statement import inventory_shortage, split_overflow, stock_available
    D10, D11 = date(2026, 10, 15), date(2026, 11, 11)
    short_trades = [T(1, "5340", D11, "BUY", 100, 500, user="P11月短線")]
    rows = assign_tmp_ids(_rows(("5340", D11, "SELL", 110, 2000, "X1", 100, 330)))
    for r in rows:
        r["user"] = "P11月短線"
    s1 = rows[0]["tmp_id"]
    assert inventory_shortage(rows, short_trades, []) == {s1: 1500}
    peggy = [T(2, "5340", D10, "BUY", 90, 3000, user="Peggy姐")]
    assert stock_available("5340", D11, [], peggy, []) == 3000
    kept, new = split_overflow(rows[0], 1500, "Peggy姐", 99)
    assert (kept["quantity"], kept["fee"], kept["tax"], kept["user"]) == (500, 25, 82, "P11月短線")
    assert (new["quantity"], new["fee"], new["tax"], new["user"], new["seq"]) == (1500, 75, 248, "Peggy姐", 99)
    assert new["order_no"] == "X1" and "tmp_id" not in new
    rows[0]["net"] = 219570
    k2, n2 = split_overflow(rows[0], 1500, "Peggy姐", 99)
    assert k2["net"] + n2["net"] == 219570
    # 拆完兩邊都夠沖
    assign_tmp_ids([new])
    assert inventory_shortage([kept], short_trades, []) == {}
    assert plan_sell_matches([new], peggy, [], "CONSERVATIVE") == ([(new["tmp_id"], 2, 1500)], {})


def test_noloss_strategy():
    """Elsa 11/11：「沖成本高的，但不要賠」— 含手續費、稅不賠的買進裡挑最貴的；都會賠時挑賠最少的。"""
    D1, D2 = date(2026, 11, 1), date(2026, 11, 11)
    trades = [T(1, "2303", D1, "BUY", 104, 1000), T(2, "2303", D1, "BUY", 99.5, 1000),   # 99.5 扣費用後會賠
              T(3, "2303", D1, "BUY", 98, 1000), T(4, "2303", D1, "BUY", 95, 1000)]
    rows = assign_tmp_ids(_rows(("2303", D2, "SELL", 100, 2000, "S1")))
    s1 = rows[0]["tmp_id"]
    assert plan_sell_matches(rows, trades, [], "NOLOSS") == ([(s1, 3, 1000), (s1, 4, 1000)], {})
    assert plan_sell_matches(rows, trades, [], "CONSERVATIVE")[0] == [(s1, 1, 1000), (s1, 2, 1000)]
    # 不賠的不夠：剩下的從賠最少（成本低）的開始
    rows3 = assign_tmp_ids(_rows(("2303", D2, "SELL", 100, 3000, "S1")))
    t = rows3[0]["tmp_id"]
    assert plan_sell_matches(rows3, trades, [], "NOLOSS")[0] == [(t, 3, 1000), (t, 4, 1000), (t, 2, 1000)]


def test_noloss_daytrade_day_buys():
    rows = assign_tmp_ids(_rows(("2303", D29, "BUY", 101, 1000, "A1"), ("2303", D29, "BUY", 99, 1000, "A2"),
                                ("2303", D29, "BUY", 98, 1000, "A3"), ("2303", D29, "SELL", 100, 1000, "A4")))
    a1, a2, a3, a4 = (r["tmp_id"] for r in rows)
    assert plan_daytrade(rows, [], [], "NOLOSS")[0] == [(a4, a2, 1000)]
