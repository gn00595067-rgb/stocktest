# -*- coding: utf-8 -*-
"""全站偏好設定：買賣人預設值等。"""
from typing import List, Optional

# 各頁「買賣人」篩選預設對象。要看其他人時使用者再自行改選。
DEFAULT_TRADER = "Peggy姐"


def resolve_default_trader(options: List[str]) -> Optional[str]:
    """在可選買賣人名單中找出預設對象：
    優先完全等於 DEFAULT_TRADER；否則取任一含「peggy」者（大小寫不拘）；都沒有回 None。
    """
    if not options:
        return None
    if DEFAULT_TRADER in options:
        return DEFAULT_TRADER
    for o in options:
        if "peggy" in str(o).lower():
            return o
    return None
