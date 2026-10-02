# -*- coding: utf-8 -*-
"""台股股價分析系統 - Streamlit 主入口"""
import os
from datetime import date
from dotenv import load_dotenv

# 從專案根目錄載入 .env（避免因啟動目錄不同而讀不到）
_project_root = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_project_root, ".env"))

import streamlit as st
from sqlalchemy.exc import OperationalError
from services.auth_service import ensure_bootstrap_admin, login_guard, render_auth_sidebar, is_admin

# Streamlit Cloud 的 Secrets 不會自動變成 os.environ，報價與試算表後端讀 os.environ，所以要同步
try:
    if hasattr(st, "secrets"):
        if st.secrets.get("FINMIND_TOKEN"):
            os.environ["FINMIND_TOKEN"] = str(st.secrets["FINMIND_TOKEN"]).strip()
        if st.secrets.get("USE_GOOGLE_SHEET"):
            os.environ["USE_GOOGLE_SHEET"] = str(st.secrets["USE_GOOGLE_SHEET"]).strip()
        if st.secrets.get("GOOGLE_SHEET_ID"):
            os.environ["GOOGLE_SHEET_ID"] = str(st.secrets["GOOGLE_SHEET_ID"]).strip()
        if st.secrets.get("GOOGLE_SHEET_CREDENTIALS"):
            cred = st.secrets.get("GOOGLE_SHEET_CREDENTIALS")
            if isinstance(cred, str):
                os.environ["GOOGLE_SHEET_CREDENTIALS"] = cred.strip()
            else:
                import json
                os.environ["GOOGLE_SHEET_CREDENTIALS"] = json.dumps(cred)
        if st.secrets.get("GOOGLE_SHEET_CREDENTIALS_B64"):
            os.environ["GOOGLE_SHEET_CREDENTIALS_B64"] = str(st.secrets["GOOGLE_SHEET_CREDENTIALS_B64"]).strip()
except Exception:
    pass

st.set_page_config(
    page_title="台股股價分析系統",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)
from services.mobile_ui import inject_mobile_css
inject_mobile_css()

ensure_bootstrap_admin()
login_guard()
render_auth_sidebar()

# 左側欄一出現就自動從 Google Sheet 載入股票清單（每 session 只執行一次；任一頁面載入都會觸發）
from services.stock_list_loader import ensure_google_sheet_loaded
ensure_google_sheet_loaded()

# 若已啟用 Google 試算表聯動，顯示狀態；若本機用檔案 DB 但已設定試算表，提供手動同步
try:
    from db.database import USE_GOOGLE_SHEET, DATABASE_URL, get_engine
    if USE_GOOGLE_SHEET:
        st.sidebar.caption("📋 **資料聯動**：交易與沖銷規則已與 Google 試算表同步，重啟後會從試算表載入。")
    elif DATABASE_URL:
        st.sidebar.caption("📦 **資料庫模式**：資料存在雲端資料庫；Google 試算表每天自動匯出一份當備份。")
        if is_admin():
            from services.sheet_sync import is_google_sheet_enabled, sync_db_to_sheet
            if is_google_sheet_enabled() and st.sidebar.button("匯出備份到 Google 試算表"):
                with st.spinner("匯出中…"):
                    ok, err = sync_db_to_sheet(get_engine(), force=True)
                if ok:
                    st.sidebar.success("已把資料庫內容匯出到 Google 試算表（含備份分頁）。")
                else:
                    st.sidebar.error(f"匯出失敗：{err or '未知錯誤'}")
    else:
        try:
            from services.sheet_sync import is_google_sheet_enabled, sync_db_to_sheet
            if is_google_sheet_enabled():
                st.sidebar.caption("📋 **本機資料庫**：目前為檔案模式，不會自動寫入試算表。")
                if st.sidebar.button("手動同步到 Google 試算表"):
                    ok, err = sync_db_to_sheet(get_engine(), force=True)
                    if ok:
                        st.sidebar.success("已將目前資料庫內容寫入 Google 試算表。")
                    else:
                        st.sidebar.error(f"同步失敗：{err or '未知錯誤'}")
        except Exception:
            pass
except Exception:
    pass

# 開發用按鈕（產生模擬數據、側欄清空全部）只在本機開發資料庫出現：
# 正式資料（資料庫或試算表模式）上一按就會塞 2000 筆假交易或清空全部，且同事共用管理者帳號。
# 正式資料要清空請到「交易匯入」頁（需手打確認字樣，刪除前自動備份）。
from services.data_guard import is_production_data
_dev_tools = is_admin() and not is_production_data()

# 左側欄：產生模擬數據按鈕（2024/1/1～今天，2000 筆）
if _dev_tools:
    st.sidebar.markdown("---")
    st.sidebar.caption("🔧 開發用")
if _dev_tools and st.sidebar.button("產生模擬數據"):
    try:
        from db.mock_data import generate_mock_trades
        n = generate_mock_trades(
            num_trades=2000,
            start_date=date(2024, 1, 1),
            end_date=date.today(),
        )
        st.sidebar.success(f"已產生 {n} 筆模擬交易（2024/1/1～今天）")
    except OperationalError:
        st.sidebar.error("無法寫入資料庫（唯讀環境，請在本機執行）")
    except Exception as e:
        st.sidebar.error(f"產生失敗：{e}")

# 左側欄：清空所有庫存資料（刪除全部交易與沖銷規則）— 只限本機開發資料庫
if _dev_tools:
    st.sidebar.markdown("---")
    st.sidebar.caption("⚠️ 資料管理")
if _dev_tools and st.session_state.get("show_clear_confirm"):
    st.sidebar.warning("即將刪除**所有交易**與**自定沖銷規則**，此操作無法復原。")
    if st.sidebar.button("確認清空", type="primary", key="confirm_clear_btn"):
        try:
            from db.database import get_session
            from db.models import Trade, CustomMatchRule, TradeMatch
            sess = get_session()
            try:
                n_rules = sess.query(CustomMatchRule).delete()
                n_matches = sess.query(TradeMatch).delete()
                n_trades = sess.query(Trade).delete()
                sess.commit()
                st.sidebar.success(f"已清空：{n_trades} 筆交易、{n_rules} 筆自定沖銷、{n_matches} 筆沖銷紀錄。")
            except OperationalError:
                sess.rollback()
                st.sidebar.error("無法寫入資料庫（唯讀環境，請在本機執行）")
            finally:
                sess.close()
            del st.session_state["show_clear_confirm"]
            st.rerun()
        except Exception as e:
            st.sidebar.error(f"清空失敗：{e}")
    if st.sidebar.button("取消", key="cancel_clear_btn"):
        del st.session_state["show_clear_confirm"]
        st.rerun()
elif _dev_tools:
    if st.sidebar.button("清空所有庫存資料"):
        st.session_state["show_clear_confirm"] = True
        st.rerun()

# 首頁僅保留側邊欄功能，直接進入投資績效頁
st.switch_page("pages/0_投資績效.py")
