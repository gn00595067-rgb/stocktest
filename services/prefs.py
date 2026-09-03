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


# 沖銷方式（損益配對口徑）。固定用「自定沖銷＋其餘先進先出」：
# 所有賣出都納入計算、且尊重自定沖銷規則。原本可切「僅自定沖銷」會漏算沒規則的賣出。
DEFAULT_POLICY = "CUSTOM_PLUS_FIFO"
POLICY_LABELS = {
    "CUSTOM_ONLY": "僅自定沖銷",
    "CUSTOM_PLUS_FIFO": "先進先出（未定沖銷部分）",
    "CUSTOM_PLUS_CONSERVATIVE": "保守（未定沖銷部分）",
    "CUSTOM_PLUS_OPTIMISTIC": "樂觀（未定沖銷部分）",
    "CUSTOM_PLUS_MEAN": "均值配對（未定沖銷部分）",
}
_SHARED_POLICY_KEY = "shared_pnl_policy"


def shared_policy_selectbox(label: str = "沖銷方式") -> str:
    """沖銷口徑固定為『自定沖銷＋其餘先進先出』，不再提供下拉切換
    （原本可切「僅自定沖銷」會漏算沒有自定規則的賣出，造成數字偏低）。
    保留此函式簽名讓呼叫端無需改動；改為顯示固定口徑說明並回傳固定 policy。
    """
    import streamlit as st
    st.caption(f"{label}：自定沖銷 ＋ 其餘先進先出（固定；所有賣出都計入）")
    return DEFAULT_POLICY
