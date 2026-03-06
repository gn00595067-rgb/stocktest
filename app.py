# -*- coding: utf-8 -*-
"""台股股價分析系統 - Streamlit 主入口"""
import os
from dotenv import load_dotenv

# 從專案根目錄載入 .env（避免因啟動目錄不同而讀不到）
_project_root = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_project_root, ".env"))

import streamlit as st

st.set_page_config(
    page_title="台股股價分析系統",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("📈 台股股價分析系統")
st.markdown("請從左側選單選擇功能：**交易輸入**、**Portfolio 持倉與損益**、**日成交彙總**、**戰略儀表板**、**主檔/設定**。")
