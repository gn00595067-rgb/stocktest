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


# 沖銷方式（損益配對口徑）。各頁共用同一個 session key，改一頁其他頁跟著同步。
DEFAULT_POLICY = "CUSTOM_ONLY"
POLICY_LABELS = {
    "CUSTOM_ONLY": "僅自定沖銷",
    "CUSTOM_PLUS_FIFO": "先進先出（未定沖銷部分）",
    "CUSTOM_PLUS_CONSERVATIVE": "保守（未定沖銷部分）",
    "CUSTOM_PLUS_OPTIMISTIC": "樂觀（未定沖銷部分）",
    "CUSTOM_PLUS_MEAN": "均值配對（未定沖銷部分）",
}
_SHARED_POLICY_KEY = "shared_pnl_policy"


def shared_policy_selectbox(label: str = "沖銷方式") -> str:
    """共用的「沖銷方式」下拉：所有頁面用同一個 session key，選擇會跨頁同步，
    確保『已實現損益』『當日交易明細』等頁口徑一致。回傳選定的 policy 鍵。
    """
    import streamlit as st
    keys = list(POLICY_LABELS.keys())
    cur = st.session_state.get(_SHARED_POLICY_KEY, DEFAULT_POLICY)
    if cur not in keys:
        cur = DEFAULT_POLICY
    return st.selectbox(
        label, keys, index=keys.index(cur),
        format_func=lambda k: POLICY_LABELS.get(k, k), key=_SHARED_POLICY_KEY,
    )
