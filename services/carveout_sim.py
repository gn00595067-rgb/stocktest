# -*- coding: utf-8 -*-
"""庫存模擬（代操扣除）：多人連動庫存合計，扣掉模擬對象的張數後剩多少、均價多少。純計算，不寫回資料。

規格：docs/specs/庫存模擬.md
"""
from typing import Dict, Iterable, List, Optional, Tuple

from services.position_cost import compute_position_and_cost_by_stock


def positions_by_person(trades, people: Iterable[str], rules, policy: str = "CUSTOM_PLUS_FIFO"
                        ) -> Dict[str, Dict[str, Tuple[int, float]]]:
    """{股票: {人: (股數, 剩餘成本含買進手續費)}}，只含有庫存的。每人各自沖銷（同仿Yahoo、庫存損益頁）。"""
    out: Dict[str, Dict[str, Tuple[int, float]]] = {}
    for p in people:
        mine = [t for t in trades if (getattr(t, "user", None) or "").strip() == p]
        for sid, v in compute_position_and_cost_by_stock(mine, custom_rules=rules, policy=policy).items():
            if int(v["qty"]) > 0:
                out.setdefault(str(sid).strip(), {})[p] = (int(v["qty"]), float(v["cost"]))
    return out


def simulate(total_qty: int, total_cost: float, sim_qty: int, sim_price: Optional[float],
             fee_rate: float, deduct_with_fee: bool) -> dict:
    """一檔的模擬結果（股數單位：股）。

    sim_cost_avg：模擬對象均價（含手續費）＝價格×(1＋費率)，不計最低 1 元。
    扣除後總成本＝連動總成本 − 股數×（含費或不含費的價格）。
    over＝模擬股數超過連動庫存（此時不算扣除後均價）。
    """
    sim_qty = max(0, int(sim_qty or 0))
    price = float(sim_price) if sim_price else 0.0
    sim_cost_avg = price * (1 + fee_rate) if price else None
    unit = (sim_cost_avg if deduct_with_fee else price) or 0.0
    over = sim_qty > total_qty
    left_qty = total_qty - sim_qty
    left_cost = total_cost - sim_qty * unit
    if over or left_qty <= 0:
        left_avg = None
    elif sim_qty and not price:
        left_avg = None   # 有張數沒價格：還不能算
    else:
        left_avg = left_cost / left_qty
    return {
        "total_avg": (total_cost / total_qty) if total_qty else None,
        "left_qty": left_qty,
        "left_avg": left_avg,
        "sim_cost_avg": sim_cost_avg,
        "over": over,
        "missing_price": bool(sim_qty and not price),
    }
