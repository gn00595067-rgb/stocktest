# -*- coding: utf-8 -*-
"""分頁拖拉排序元件（docs/specs/分頁拖拉排序.md）。

不加第三方套件：index.html 自己走 Streamlit 元件訊息協定，拖完回傳新的 id 順序。"""
import os

import streamlit.components.v1 as components

_component = components.declare_component(
    "tab_sorter", path=os.path.dirname(os.path.abspath(__file__)))


def tab_sorter(items, key=None):
    """items：[(id, 名稱), ...]。回傳拖拉後的 id 清單；還沒拖過回傳 None。"""
    data = [{"id": int(i), "label": str(n)} for i, n in items]
    return _component(items=data, key=key, default=None)
