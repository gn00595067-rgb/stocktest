# -*- coding: utf-8 -*-
"""損益總覽：已與「投資績效」合併為一頁，自動導向合併頁。"""
import streamlit as st

st.set_page_config(page_title="損益總覽", layout="wide")
# 與投資績效合併為「損益總覽與投資績效」，導向該頁
st.switch_page("pages/4_投資績效.py")
