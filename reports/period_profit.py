# -*- coding: utf-8 -*-
"""期間獲利表（Peggy Excel 版的系統化）：每月／每年獲利、累計、帳戶結餘。

- 系統從 115/9（2026-09）起才有完整交易紀錄 → 這之後的月份用系統已實現損益（依賣出月份）。
- 之前的期間（112/7/19～112/12/31、113 全年、114 各月、115/1～8 月）與帳戶結餘由使用者手動輸入。
規格：docs/specs/Peggy回饋_2026-10-14.md §8
"""
from datetime import date
from typing import Dict, List, Optional

SYSTEM_START = date(2026, 9, 1)     # 115/9 起用系統數字
FIRST_DAY = date(2023, 7, 19)       # 112/7/19 開戶起算
MONTHLY_FROM_YEAR = 2025            # 114 年起逐月列
BALANCE_KEY = "balance"
# Excel 右側紅框：「虧(a)」手動填、「(含虧)c=a+b」＝a＋當年小計(b)。意思待確認，先照字面做（Jonathan 2026-10-14）
LOSS_A_KEY = "loss_a"
LOSS_A_LABEL = "虧(a)"
LOSS_C_LABEL = "(含虧)c=a+b"
# 逐月之前的整段期間（只有一格）
LUMP_PERIODS = [("2023H2", "112/7/19～112/12/31"), ("2024", "113/1-12月")]


def roc(y: int) -> int:
    return y - 1911


def month_key(y: int, m: int) -> str:
    return f"{y:04d}-{m:02d}"


def is_system_month(y: int, m: int, system_start: date = SYSTEM_START) -> bool:
    return (y, m) >= (system_start.year, system_start.month)


def manual_period_keys(today: date, system_start: date = SYSTEM_START) -> List[tuple]:
    """需要手動輸入的期間 [(key, 顯示名稱)]：整段期間、系統上線前已過的月份、帳戶結餘。"""
    out = list(LUMP_PERIODS)
    for y in range(MONTHLY_FROM_YEAR, today.year + 1):
        for m in range(1, 13):
            if (y, m) > (today.year, today.month) or is_system_month(y, m, system_start):
                break
            out.append((month_key(y, m), f"{roc(y)}/{m}月"))
    out.append((LOSS_A_KEY, LOSS_A_LABEL))
    out.append((BALANCE_KEY, "帳戶結餘"))
    return out


def build_period_blocks(
    manual: Dict[str, Optional[float]],
    system_monthly: Dict[str, float],
    today: date,
    system_start: date = SYSTEM_START,
) -> List[List[dict]]:
    """回傳畫面上的區塊（每塊一張小表，像 Excel 左右兩欄）。每列 dict：
    期間、獲利（None＝未填／未到）、來源（手動／系統／小計／累計／未到）、kind（item／subtotal／total／balance）。

    第一塊＝整段期間＋第一個逐月年度；之後每年一塊；帳戶結餘接在最後一塊。
    """
    def _v(x):
        return float(x) if x is not None else None

    running = 0.0          # 112/7/19 起累計
    b_this_year = 0.0      # 當年小計（b）
    blocks: List[List[dict]] = []
    first = []
    for key, label in LUMP_PERIODS:
        v = _v(manual.get(key))
        running += v or 0.0
        first.append({"期間": label, "獲利": v, "來源": "手動", "kind": "item"})

    for y in range(MONTHLY_FROM_YEAR, today.year + 1):
        rows = first if y == MONTHLY_FROM_YEAR else []
        year_sum = 0.0
        last_m = 12
        for m in range(1, 13):
            label = f"{roc(y)}/{m}月"
            if (y, m) > (today.year, today.month):
                rows.append({"期間": label, "獲利": None, "來源": "未到", "kind": "item"})
                continue
            if is_system_month(y, m, system_start):
                v, src = float(system_monthly.get(month_key(y, m), 0.0)), "系統"
            else:
                v, src = _v(manual.get(month_key(y, m))), "手動"
            year_sum += v or 0.0
            rows.append({"期間": label, "獲利": v, "來源": src, "kind": "item"})
        if y == today.year:
            last_m = today.month
        running += year_sum
        # 當年小計標「(b)」：對應下方「(含虧)c=a+b」的 b
        sub_label = f"{roc(y)}/1-12月" + (" (b)" if y == today.year else "")
        rows.append({"期間": sub_label, "獲利": year_sum, "來源": "小計", "kind": "subtotal"})
        rows.append({
            "期間": f"{roc(FIRST_DAY.year)}/{FIRST_DAY.month}/{FIRST_DAY.day}～{roc(y)}/{last_m}月",
            "獲利": running, "來源": "累計", "kind": "total",
        })
        blocks.append(rows)
        b_this_year = year_sum
    if not blocks:          # today 早於逐月年度（理論上不會發生）
        blocks.append(first)
    a = _v(manual.get(LOSS_A_KEY))
    blocks[-1].append({"期間": "帳戶結餘", "獲利": _v(manual.get(BALANCE_KEY)), "來源": "手動", "kind": "balance"})
    blocks[-1].append({"期間": LOSS_A_LABEL, "獲利": a, "來源": "手動", "kind": "item"})
    blocks[-1].append({"期間": LOSS_C_LABEL, "獲利": (a or 0.0) + b_this_year, "來源": "小計", "kind": "subtotal"})
    return blocks
