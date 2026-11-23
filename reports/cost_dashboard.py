# -*- coding: utf-8 -*-
"""持股成本儀表板：各分頁成本相加，再扣掉美美（也在「全部」裡的股票），得到淨投入成本。

成本＝市值－未實現（扣賣出費稅），數字都來自仿Yahoo 的 summarize_group，跟仿Yahoo 畫面一致。
規格：docs/specs/持股成本儀表板.md
"""
from typing import Dict, List, Optional

OWNER = "老闆"   # 分頁設定取這個帳號的（Peggy姐 用的那組）
# 帳戶 → [(仿Yahoo 分頁名稱, 顯示名稱)]
ACCOUNTS = [
    ("Peggy姐", [("P11/3 短線", "11/3短"), ("11/17", "11/17"), ("Peggy姐", "P姐")]),
    ("豪哥", [("豪哥", "豪哥")]),
    ("雅雲", [("雅雲", "雅雲")]),
]
DEDUCT_GROUP = "美美"     # 扣這個分頁的持股……
DEDUCT_SCOPE = "全部"     # ……但只扣也在這個分頁有持股的股票


def needed_groups() -> List[str]:
    return [g for _a, tabs in ACCOUNTS for g, _l in tabs] + [DEDUCT_GROUP, DEDUCT_SCOPE]


def _part(mv: float, unreal: float) -> dict:
    cost = mv - unreal
    return {"market_value": mv, "unrealized": unreal, "cost": cost,
            "pct": (unreal / cost * 100) if cost else None}


def _sum(parts: List[dict]) -> dict:
    return _part(sum(p["market_value"] for p in parts), sum(p["unrealized"] for p in parts))


def build_cost_dashboard(summaries: Dict[str, Optional[dict]]) -> dict:
    """summaries：分頁名稱 → summarize_group 的結果（找不到的分頁給 None）。

    回傳 accounts（每個帳戶的分頁與小計）、total（三帳戶合計）、deduct（美美逐檔，含是否扣除）、
    deduct_total、net（扣除後：市值、未實現、成本、pct、ratio＝市值÷成本）、missing（找不到的分頁）。
    """
    missing = [g for g in needed_groups() if summaries.get(g) is None]
    accounts, all_tabs = [], []
    for name, tabs in ACCOUNTS:
        rows = []
        for g, label in tabs:
            s = summaries.get(g)
            p = _part(float(s["market_value"]), float(s["unrealized_net"])) if s else _part(0.0, 0.0)
            rows.append({"group": g, "label": label, **p})
        all_tabs += rows
        accounts.append({"account": name, "tabs": rows, **_sum(rows)})
    total = _sum(all_tabs)

    scope = summaries.get(DEDUCT_SCOPE)
    in_scope = {r["stock_id"] for r in (scope or {}).get("rows", []) if r.get("qty")}
    deduct = []
    for r in (summaries.get(DEDUCT_GROUP) or {}).get("rows", []):
        if not r.get("qty"):
            continue
        deduct.append({"stock_id": r["stock_id"], "name": r.get("name") or r["stock_id"], "qty": int(r["qty"]),
                       "deducted": r["stock_id"] in in_scope,
                       **_part(float(r["market_value"]), float(r["unrealized_net"]))})
    deduct_total = _sum([d for d in deduct if d["deducted"]])
    net = _part(total["market_value"] - deduct_total["market_value"], total["unrealized"] - deduct_total["unrealized"])
    net["ratio"] = (net["market_value"] / net["cost"] * 100) if net["cost"] else None
    return {"accounts": accounts, "total": total, "deduct": deduct, "deduct_total": deduct_total,
            "net": net, "missing": missing}
