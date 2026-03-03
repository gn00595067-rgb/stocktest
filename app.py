# -*- coding: utf-8 -*-
"""台股股價分析系統 - Streamlit 主入口"""
import os
from dotenv import load_dotenv
load_dotenv()

import streamlit as st

st.set_page_config(
    page_title="台股股價分析系統",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("📈 台股股價分析系統")
st.markdown("請從左側選單選擇功能：**交易輸入**、**Portfolio 持倉與損益**、**日成交彙總**、**戰略儀表板**、**主檔/設定**。")
