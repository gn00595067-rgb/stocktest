# -*- coding: utf-8 -*-
"""操作手冊：系統介紹、各頁功能、改密碼步驟、優化效益（規格：docs/specs/操作手冊.md）。

內容寫在 docs/操作手冊.md，改文字只要改那個檔。
"""
import os
import sys

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.auth_service import ensure_bootstrap_admin, login_guard, render_auth_sidebar
from services.mobile_ui import inject_mobile_css

st.set_page_config(page_title="操作手冊", page_icon="📘", layout="wide")
inject_mobile_css()
ensure_bootstrap_admin()
login_guard()
render_auth_sidebar()

_MANUAL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "操作手冊.md")
try:
    with open(_MANUAL, encoding="utf-8") as f:
        st.markdown(f.read())
except OSError:
    st.error("找不到操作手冊檔案（docs/操作手冊.md），請通知 Jonathan。")
