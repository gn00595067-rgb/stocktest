# -*- coding: utf-8 -*-
"""主檔/設定"""
import streamlit as st
import pandas as pd
import io
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STOCK_LIST_CACHE_PATH = os.path.join(_PROJECT_ROOT, "data", "stock_list_cache.csv")
# Google Sheet 連結用（實際 ID 與載入邏輯在 services.stock_list_loader）
try:
    if hasattr(st, "secrets") and st.secrets.get("FINMIND_TOKEN"):
        os.environ.setdefault("FINMIND_TOKEN", str(st.secrets["FINMIND_TOKEN"]).strip())
except Exception:
    pass
from db.database import get_session
from db.models import StockMaster
from db.seed_data import run_seed
from db.mock_data import generate_mock_trades
from services.price_service import fetch_stock_list_finmind
from services.stock_list_loader import (
    STOCK_LIST_GOOGLE_SHEET_ID,
    load_from_google_sheet,
    write_to_stock_master,
    ensure_google_sheet_loaded,
)
from sqlalchemy.exc import OperationalError, IntegrityError

ensure_google_sheet_loaded()


def _save_stock_list_cache(items: list) -> bool:
    """將股票清單寫入 data/stock_list_cache.csv，本機可寫入；Cloud 可能唯讀則回傳 False。"""
    if not items:
        return False
    try:
        os.makedirs(os.path.dirname(STOCK_LIST_CACHE_PATH), exist_ok=True)
        df = pd.DataFrame(items)
        df.to_csv(STOCK_LIST_CACHE_PATH, index=False, encoding="utf-8-sig")
        return True
    except Exception:
        return False


def _load_stock_list_cache() -> list:
    """從 data/stock_list_cache.csv 讀取，回傳 [{"stock_id", "name", ...}, ...]，失敗回傳 []。"""
    if not os.path.isfile(STOCK_LIST_CACHE_PATH):
        return []
    try:
        df = pd.read_csv(STOCK_LIST_CACHE_PATH, encoding="utf-8-sig")
        if df.empty or "stock_id" not in df.columns:
            return []
        out = []
        for _, row in df.iterrows():
            sid = str(row["stock_id"]).strip()
            if not sid:
                continue
            out.append({
                "stock_id": sid,
                "name": (str(row.get("name", "")) or sid)[:100],
                "industry_name": (str(row.get("industry_name", "")) or "")[:100],
                "market": str(row.get("market", "TW")),
                "exchange": str(row.get("exchange", "TWSE")),
                "is_etf": bool(row.get("is_etf", False)),
            })
        return out
    except Exception:
        return []


def _parse_is_etf(v) -> bool:
    """CSV/Sheet 的 is_etf 可能是 TRUE/FALSE 字串或 bool。"""
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().upper() in ("TRUE", "1", "YES", "Y")
    return bool(v)


def _parse_row_to_item(row) -> dict:
    """將 DataFrame 一列轉成 stock_master 用的 dict。"""
    sid = str(row["stock_id"]).strip()
    if not sid:
        return None
    return {
        "stock_id": sid,
        "name": (str(row.get("name", "") or sid))[:100],
        "industry_name": (str(row.get("industry_name", "")) or "")[:100],
        "market": str(row.get("market", "TW")),
        "exchange": str(row.get("exchange", "TWSE")),
        "is_etf": _parse_is_etf(row.get("is_etf", False)),
    }


st.set_page_config(page_title="主檔/設定", layout="wide")
st.title("主檔/設定")

st.subheader("stock_master CSV 匯入")
st.caption("CSV 欄位：stock_id, name, industry_name, market, exchange, is_etf（選填）")
uploaded = st.file_uploader("上傳 CSV", type=["csv"])
if uploaded:
    try:
        df = pd.read_csv(uploaded)
        required = ["stock_id"]
        if not all(c in df.columns for c in required):
            st.error("CSV 至少需有 stock_id 欄位")
        else:
            sess = get_session()
            try:
                for _, row in df.iterrows():
                    sid = str(row["stock_id"]).strip()
                    if not sid:
                        continue
                    existing = sess.query(StockMaster).filter(StockMaster.stock_id == sid).first()
                    name = row.get("name", existing.name if existing else None)
                    industry_name = row.get("industry_name", existing.industry_name if existing else None)
                    market = row.get("market", existing.market if existing else None)
                    exchange = row.get("exchange", existing.exchange if existing else None)
                    is_etf = bool(row.get("is_etf", False)) if "is_etf" in row else (existing.is_etf if existing else False)
                    if existing:
                        existing.name = name
                        existing.industry_name = industry_name
                        existing.market = market
                        existing.exchange = exchange
                        existing.is_etf = is_etf
                    else:
                        sess.add(StockMaster(stock_id=sid, name=name, industry_name=industry_name, market=market, exchange=exchange, is_etf=is_etf))
                sess.commit()
                st.success("匯入完成")
            except OperationalError as e:
                sess.rollback()
                st.error("無法寫入資料庫（目前環境為唯讀，例如 Streamlit Cloud）。請在本機執行以匯入 CSV。")
                st.caption(f"技術細節：{e}")
            finally:
                sess.close()
    except Exception as e:
        st.error(str(e))

st.subheader("手續費/稅率設定")
fee_rate = st.number_input("手續費率（若 trades.fee 為空則用此估算）", value=0.001425, format="%.6f")
tax_rate = st.number_input("證交稅率（賣出）", value=0.003, format="%.4f")
st.caption("trades 有填 fee/tax 則以實際為準，否則用上述估算。估算邏輯可在寫入交易時套用。")

if "fee_rate" not in st.session_state:
    st.session_state["fee_rate"] = fee_rate
if "tax_rate" not in st.session_state:
    st.session_state["tax_rate"] = tax_rate
st.session_state["fee_rate"] = fee_rate
st.session_state["tax_rate"] = tax_rate

st.subheader("種子資料")
if st.button("載入種子資料（2330/2317/3706 等）"):
    try:
        run_seed()
        st.success("種子資料已寫入")
    except OperationalError as e:
        st.error("無法寫入資料庫（目前環境為唯讀，例如 Streamlit Cloud）。請在本機執行以載入種子資料。")
        st.caption(f"技術細節：{e}")

# ---------- 開發用：一鍵產生模擬交易（正式版可整段移除） ----------
with st.expander("🔧 開發用：一鍵產生模擬交易（正式版可移除此區塊）", expanded=False):
    st.caption("一次產生多筆模擬交易，方便預覽「交易輸入」「Portfolio」「日成交彙總」「日成交明細」「個股明細」「投資績效」等介面。")
    num_trades = st.number_input("模擬筆數", min_value=50, max_value=2000, value=300, step=50, key="mock_num")
    col_a, col_b = st.columns(2)
    with col_a:
        mock_start = st.date_input("區間起日", value=None, key="mock_start")
    with col_b:
        mock_end = st.date_input("區間迄日", value=None, key="mock_end")
    st.caption("留空則預設為最近 90 天。買賣人為張三、李四、王五等；股票從 stock_master 或種子代號隨機。**模擬比例為買多於賣（約 3:2），方便在「個股明細」看到庫存。**")
    if st.button("產生模擬交易", key="mock_btn"):
        from datetime import date, timedelta
        end = mock_end or date.today()
        start = mock_start or (end - timedelta(days=90))
        try:
            n = generate_mock_trades(num_trades=int(num_trades), start_date=start, end_date=end)
            st.success(f"已寫入 {n} 筆模擬交易，可到「交易輸入」「Portfolio」「日成交彙總」「日成交明細」「個股明細」「投資績效」檢視效果。")
        except OperationalError as e:
            st.error(
                "無法寫入資料庫。若您是在 **Streamlit Cloud** 上執行，雲端檔案系統為唯讀，無法建立或更新本機 SQLite。"
                "請在本機執行此 App 以產生模擬交易，或改用已內建的種子/匯入資料預覽介面。"
            )
            st.caption(f"技術細節：{e}")

# ---------- Google Sheet 股票清單（每次進入此頁自動載入） ----------
st.subheader("Google Sheet 股票清單（預設）")
st.caption(f"程式已設定使用您提供的 [Google 試算表](https://docs.google.com/spreadsheets/d/{STOCK_LIST_GOOGLE_SHEET_ID})。**程式一啟動即會自動載入**；每次進入此頁也會再次載入至 stock_master。請將試算表設為「知道連結的任何人可檢視」。若仍出現 400 錯誤，可改試：試算表 **檔案 → 共用 → 發佈到網路**，選擇「整份文件」與 CSV，取得連結後告知開發者以改用該連結。")

gs_list, gs_err = load_from_google_sheet()
if gs_err:
    st.warning("無法從 Google Sheet 取得清單（請確認試算表已設為「知道連結的任何人可檢視」或檢查網路）。可改用手動上傳 CSV 或下方 FinMind 同步。")
    st.caption(f"錯誤：{gs_err}")
else:
    n_raw = len(gs_list)
    by_id = {item["stock_id"]: item for item in gs_list}
    gs_list = list(by_id.values())
    if not gs_list:
        st.warning("試算表無有效資料（需有 stock_id 欄位）。")
    else:
        st.success(f"已從 Google Sheet 取得 **{len(gs_list)}** 筆" + (f"（已略過 {n_raw - len(gs_list)} 筆重複）" if n_raw > len(gs_list) else "") + "，正在自動載入至 stock_master…")
        ok, write_err = write_to_stock_master(gs_list)
        if ok:
            st.success(f"已自動載入 **{len(gs_list)}** 筆股票至 stock_master。")
        elif write_err and "唯讀" in write_err:
            st.warning("無法寫入資料庫（目前環境可能唯讀，例如 **Streamlit Cloud**）。請在本機執行此 App 一次，進入本頁即可自動載入；或下載下方 CSV 後在本機用「上傳 CSV」匯入。")
            df_gs = pd.DataFrame(gs_list)
            buf_gs = io.BytesIO()
            df_gs.to_csv(buf_gs, index=False, encoding="utf-8-sig")
            buf_gs.seek(0)
            st.download_button("下載此次取得的股票清單 CSV", data=buf_gs.getvalue(), file_name="stock_list_from_sheet.csv", mime="text/csv", key="dl_gs_csv")
        elif write_err and "IntegrityError" in write_err:
            st.error("寫入時發生主鍵重複（IntegrityError）。請確認清單中無重複的 stock_id；若已去重仍發生，請回報。")
        else:
            st.error(f"寫入失敗：{write_err}")

# ---------- 本機快取與 FinMind 同步 ----------
st.subheader("從 FinMind 同步 / 本機快取")
st.caption("從 FinMind TaiwanStockInfo API 取得上市櫃清單，寫入 stock_master，並更新本機快取檔（data/stock_list_cache.csv）。需設定 FINMIND_TOKEN（可選）。**FinMind 每小時約 600 次請求上限**。")

# 從快取載入（有快取檔時顯示，免每次重開都同步）
cached = _load_stock_list_cache()
if cached:
    st.caption(f"已偵測到快取檔（共 {len(cached)} 筆）。可先「從快取載入」使用舊資料，或直接「同步」取得最新清單。")
    if st.button("從快取載入股票列表"):
        sess = get_session()
        try:
            for item in cached:
                existing = sess.query(StockMaster).filter(StockMaster.stock_id == item["stock_id"]).first()
                if existing:
                    existing.name = item["name"]
                    existing.industry_name = item["industry_name"]
                    existing.market = item["market"]
                    existing.exchange = item["exchange"]
                    existing.is_etf = item["is_etf"]
                else:
                    sess.add(StockMaster(
                        stock_id=item["stock_id"],
                        name=item["name"],
                        industry_name=item["industry_name"],
                        market=item["market"],
                        exchange=item["exchange"],
                        is_etf=item["is_etf"],
                    ))
            sess.commit()
            st.success(f"已從快取載入 {len(cached)} 筆股票至 stock_master")
        except OperationalError as e:
            sess.rollback()
            st.error("無法寫入資料庫（目前環境為唯讀）。請在本機執行以載入快取。")
        finally:
            sess.close()
else:
    st.caption("尚無快取檔。請先執行一次「同步股票列表（FinMind）」；本機執行時會自動寫入 data/stock_list_cache.csv，之後可從快取載入。")
if cached:
    df_dl = pd.DataFrame(cached)
    buf_dl = io.BytesIO()
    df_dl.to_csv(buf_dl, index=False, encoding="utf-8-sig")
    buf_dl.seek(0)
    st.download_button("下載快取 CSV", data=buf_dl.getvalue(), file_name="stock_list_cache.csv", mime="text/csv", key="dl_cache")

if st.button("同步股票列表（FinMind）"):
    lst = fetch_stock_list_finmind()
    if not lst:
        st.warning("無法取得清單（請檢查網路或 FINMIND_TOKEN）")
    else:
        # 依 stock_id 去重（API 可能回傳重複），並限制欄位長度避免 DB 錯誤
        seen = set()
        unique = []
        for item in lst:
            sid = str(item.get("stock_id", "")).strip()
            if not sid or sid in seen:
                continue
            seen.add(sid)
            name = (item.get("name") or sid)[:100]
            industry_name = (item.get("industry_name") or "")[:100]
            unique.append({
                "stock_id": sid,
                "name": name,
                "industry_name": industry_name,
                "market": item.get("market", "TW"),
                "exchange": item.get("exchange", "TWSE"),
                "is_etf": item.get("is_etf", False),
            })
        sess = get_session()
        try:
            for item in unique:
                existing = sess.query(StockMaster).filter(StockMaster.stock_id == item["stock_id"]).first()
                if existing:
                    existing.name = item["name"]
                    existing.industry_name = item["industry_name"]
                    existing.market = item["market"]
                    existing.exchange = item["exchange"]
                    existing.is_etf = item["is_etf"]
                else:
                    sess.add(StockMaster(
                        stock_id=item["stock_id"],
                        name=item["name"],
                        industry_name=item["industry_name"],
                        market=item["market"],
                        exchange=item["exchange"],
                        is_etf=item["is_etf"],
                    ))
            sess.commit()
            st.success(f"已同步 {len(unique)} 筆股票至 stock_master")
            if _save_stock_list_cache(unique):
                st.caption("已更新本機快取檔（data/stock_list_cache.csv），下次可從快取載入。")
            else:
                st.caption("快取檔未寫入（目前環境可能唯讀），可改以下方「下載快取 CSV」備存後，用「上傳 CSV」匯入。")
        except Exception as e:
            sess.rollback()
            st.error(f"同步失敗：{e}")
        finally:
            sess.close()

st.subheader("即時股價 API")
try:
    secret_token = st.secrets.get("FINMIND_TOKEN", "") if hasattr(st, "secrets") else ""
except Exception:
    secret_token = ""
token_set = bool(os.environ.get("FINMIND_TOKEN") or secret_token)
if token_set:
    st.success("**FINMIND_TOKEN：已設定 ✓** 報價應為 FinMind 真實資料，請到「交易輸入」按「更新即時現價」確認。")
else:
    st.warning("**FINMIND_TOKEN：未設定 ✗** 目前報價為模擬數據（例如 2330 固定 580）。")
st.markdown("**若要顯示正確即時／收盤價，必須設定 FINMIND_TOKEN。** 未設定時報價卡會顯示「模擬報價」（例如 2330 固定 580），僅供測試。")
st.caption("**若您是在 Streamlit Cloud 上執行**：Cloud 不會讀取 repo 裡的 .env，請務必到 App → **Settings** → **Secrets** 新增：`FINMIND_TOKEN = \"你的token\"`，存檔後等重新部署。")
st.caption("本機執行：在專案根目錄放 `.env`，內容為 `FINMIND_TOKEN=你的token`，並**重新啟動** Streamlit。")
st.caption("詳細步驟請見專案中的 **FinMind_Token取得步驟.md**，或依下列簡述操作：")
st.markdown("""
1. 打開 **https://finmindtrade.com** → 點「登入」或「註冊」  
2. **註冊**：填信箱、密碼 → 收信點驗證連結  
3. **登入**：https://finmindtrade.com/analysis/#/account/login  
4. 登入後進入 **使用者資訊／帳戶／API** 頁面，複製 **API Token**  
5. 本機：在專案 `.env` 加上 `FINMIND_TOKEN=你的token`，重啟 Streamlit  
   Cloud：App → Settings → Secrets → 新增 `FINMIND_TOKEN = "你的token"`  
""")
st.code("FINMIND_TOKEN=your_token\nFUGLE_API_KEY=your_key  # 預留", language="bash")
