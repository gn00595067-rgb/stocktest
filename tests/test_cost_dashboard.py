# -*- coding: utf-8 -*-
"""持股成本儀表板：分頁成本相加、扣掉美美（只扣也在「全部」有持股的股票）。數字是編的，不是真實帳戶。"""
from reports.cost_dashboard import build_cost_dashboard


def _s(mv, unreal, rows=None):
    return {"market_value": mv, "unrealized_net": unreal, "rows": rows or []}


def _r(sid, qty, mv=0.0, unreal=0.0, name=""):
    return {"stock_id": sid, "name": name or sid, "qty": qty, "market_value": mv, "unrealized_net": unreal}


MEIMEI = [_r("2330", 10, 1000, -200, "台積電"), _r("2303", 10, 500, -100, "聯電"),
          _r("2542", 10, 300, -50, "興富發"),   # 「全部」裡沒有持股 → 不扣
          _r("9999", 0)]                         # 0 股不列
ALL_ROWS = [_r("2330", 1), _r("2303", 1), _r("3037", 1), _r("2542", 0)]
SUMMARIES = {
    "P11/3 短線": _s(800, -200), "11/17": _s(400, 100), "Peggy姐": _s(3000, -1000),
    "豪哥": _s(600, -400), "雅雲": _s(700, -300),
    "美美": _s(0, 0, MEIMEI), "全部": _s(5500, -1800, ALL_ROWS),
}


def test_accounts_totals_and_deduct():
    d = build_cost_dashboard(SUMMARIES)
    assert d["missing"] == []
    peggy = d["accounts"][0]
    assert [t["label"] for t in peggy["tabs"]] == ["11/3短", "11/17", "P姐"]
    assert [t["cost"] for t in peggy["tabs"]] == [1000, 300, 4000]          # 成本＝市值－未實現
    assert (peggy["market_value"], peggy["cost"]) == (4200, 5300)
    assert round(peggy["pct"], 2) == round(-1100 / 5300 * 100, 2)
    assert (d["total"]["market_value"], d["total"]["unrealized"], d["total"]["cost"]) == (5500, -1800, 7300)
    assert [(x["stock_id"], x["deducted"]) for x in d["deduct"]] == [("2330", True), ("2303", True), ("2542", False)]
    assert (d["deduct_total"]["market_value"], d["deduct_total"]["cost"]) == (1500, 1800)
    n = d["net"]
    assert (n["market_value"], n["unrealized"], n["cost"]) == (4000, -1500, 5500)
    assert round(n["ratio"], 4) == round(4000 / 5500 * 100, 4)
    assert round(n["pct"], 4) == round(-1500 / 5500 * 100, 4)


def test_missing_group_counts_zero():
    s = dict(SUMMARIES)
    s["11/17"] = None
    d = build_cost_dashboard(s)
    assert d["missing"] == ["11/17"]
    assert d["accounts"][0]["tabs"][1]["cost"] == 0
