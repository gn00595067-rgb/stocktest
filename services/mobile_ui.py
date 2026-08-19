# -*- coding: utf-8 -*-
"""全站響應式／觸控優化 CSS。

多頁 Streamlit app 每個頁面各自執行，所以每頁都要呼叫一次 inject_mobile_css()
才能全站生效。CSS 必須每次 rerun 都注入（Streamlit 每次互動會重建 DOM，若跳過注入
樣式會在第一次互動後消失），故不做旗標快取。

設計目標（針對 iPad 與觸控）：
- 觸控目標 ≥ 44px（Apple HIG）：按鈕、number_input 的 +/- 步進鈕、輸入框。
- 平板寬度（≤ 1200px）放大內文與表格字體，改善「欄多字小」的擁擠感。
- 寬表 / 自訂欄位表可橫向捲動，不硬擠。
- 直向（≤ 820px）時側邊欄改為疊加浮層，不再吃掉內容寬度；並讓多欄排版在極窄時自動換行堆疊。
- 桌面（> 1200px）維持原樣，不影響既有體驗。
"""
import streamlit as st

_CSS = """
<style>
/* ============ 觸控裝置通用（不分寬度）============ */
@media (pointer: coarse) {
    /* 按鈕加大點擊區 */
    .stButton > button,
    .stDownloadButton > button {
        min-height: 44px;
        font-size: 0.95rem;
    }
    /* number_input 的 +/- 步進鈕加大 */
    [data-testid="stNumberInputStepUp"],
    [data-testid="stNumberInputStepDown"] {
        min-width: 40px;
        min-height: 40px;
    }
    /* 輸入框本體加高，手指好點 */
    .stNumberInput input,
    .stTextInput input,
    .stDateInput input,
    div[data-baseweb="select"] > div {
        min-height: 42px;
    }
    /* 分頁 / radio / checkbox 點擊區加大 */
    .stRadio label, .stCheckbox label {
        min-height: 36px;
        display: flex;
        align-items: center;
    }
}

/* ============ 平板與以下（≤ 1200px）============ */
@media (max-width: 1200px) {
    /* 主內容左右留白縮小，把空間讓給資料 */
    .block-container {
        padding-left: 1rem !important;
        padding-right: 1rem !important;
        padding-top: 1.2rem !important;
    }
    /* 內文與表格字體放大，改善小字 */
    .stMarkdown, .stMarkdown p, .stCaption, [data-testid="stMetricValue"] {
        font-size: 1.02rem;
    }
    /* 交易輸入沖銷表：字體從 0.72rem 拉大，並允許橫向捲動 */
    .te-match-td { font-size: 0.9rem !important; }
    /* 資料表格橫向可捲動（多欄時用手指滑，不擠壓） */
    [data-testid="stDataFrame"], [data-testid="stTable"] {
        overflow-x: auto;
    }
    [data-testid="stDataFrame"] { -webkit-overflow-scrolling: touch; }
}

/* ============ 直向 / 窄平板（≤ 820px）============ */
@media (max-width: 820px) {
    /* 側邊欄改為浮層疊加，不再壓縮主內容寬度（直向最大的體驗改善） */
    section[data-testid="stSidebar"] {
        position: fixed !important;
        z-index: 999 !important;
        height: 100% !important;
        box-shadow: 2px 0 12px rgba(0,0,0,0.15);
    }
    /* 註：此處刻意不動多欄排版，保持沖銷配對等資料表對齊。
       只有到手機寬度（≤ 560px）才改成單欄堆疊。 */
}

/* ============ 手機（≤ 560px）============ */
@media (max-width: 560px) {
    /* 極窄螢幕多欄硬擠不可讀，改成單欄由上而下堆疊 */
    [data-testid="stHorizontalBlock"] {
        flex-wrap: wrap !important;
    }
    [data-testid="stHorizontalBlock"] > [data-testid="stColumn"],
    [data-testid="stHorizontalBlock"] > [data-testid="column"] {
        min-width: 100% !important;
        flex: 1 1 100% !important;
    }
    .block-container { padding-left: 0.6rem !important; padding-right: 0.6rem !important; }
}
</style>
"""


def inject_mobile_css() -> None:
    """注入全站響應式／觸控 CSS。每頁在 set_page_config 之後呼叫一次。"""
    st.markdown(_CSS, unsafe_allow_html=True)
