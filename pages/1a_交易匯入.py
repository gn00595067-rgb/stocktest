# -*- coding: utf-8 -*-
"""交易匯入：上傳券商格式之交易紀錄 CSV/Excel，解析後寫入 trades"""
import streamlit as st
import pandas as pd
import re
import io
from datetime import datetime
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.stock_list_loader import ensure_google_sheet_loaded
ensure_google_sheet_loaded()
try:
    if hasattr(st, "secrets") and st.secrets.get("FINMIND_TOKEN"):
        os.environ.setdefault("FINMIND_TOKEN", str(st.secrets["FINMIND_TOKEN"]).strip())
except Exception:
    pass
from db.database import get_session
from db.models import Trade, StockMaster
from sqlalchemy.exc import OperationalError

st.set_page_config(page_title="交易匯入", layout="wide")
st.title("交易匯入")

# 欄位候選名稱（依券商常見用語）
COL_ACCOUNT = ["帳戶", "戶名", "買賣人", "帳號"]
COL_STOCK_NAME = ["股名", "股票名稱", "名稱", "股票", "公司"]
COL_DATE = ["日期", "交易日期", "成交日", "買賣日"]
COL_QUANTITY = ["成交股數", "股數", "數量"]
COL_NET_AMOUNT = ["淨收付金額", "淨收付", "買賣", "方向"]  # 內含「買進」「賣出」或數字
COL_SIDE = ["買賣", "方向", "多空"]  # 若為獨立欄位且僅含買進/賣出則優先使用
COL_PRICE = ["成交價", "價格", "單價", "股價"]
COL_AMOUNT = ["成交金額", "金額"]
COL_FEE = ["手續費", "佣金", "手續費率"]
COL_TAX = ["交易稅", "證交稅", "稅"]
COL_NOTE = ["備註", "備註欄", "說明"]


def _find_column(df, candidates):
    """依候選名稱找到對應欄位（完全一致或包含）"""
    cols = [str(c).strip() for c in df.columns]
    for cand in candidates:
        for col in cols:
            if cand == col or cand in col:
                return col
    return None


def _parse_number(s):
    """從字串解析數字，支援千分位逗號"""
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return 0
    s = str(s).strip().replace(",", "")
    m = re.search(r"-?[\d.]+", s)
    return float(m.group()) if m else 0


def _parse_date(s):
    """解析日期：YYYY/MM/DD、YYYY-MM-DD、民國等"""
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return None
    s = str(s).strip()[:20]
    # YYYY/MM/DD
    if "/" in s:
        parts = s.split("/")
        if len(parts) >= 3:
            try:
                y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
                if y < 200:  # 民國年
                    y += 1911
                return datetime(y, m, d).date()
            except (ValueError, TypeError):
                pass
    # YYYY-MM-DD
    if "-" in s:
        parts = s.replace("/", "-").split("-")
        if len(parts) >= 3:
            try:
                y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
                if y < 200:
                    y += 1911
                return datetime(y, m, d).date()
            except (ValueError, TypeError):
                pass
    try:
        if len(s) >= 10:
            for fmt in ("%Y/%m/%d", "%Y-%m-%d"):
                try:
                    return datetime.strptime(s[:10], fmt).date()
                except ValueError:
                    continue
    except Exception:
        pass
    return None


def _infer_side(cell):
    """從「淨收付金額」等欄位推斷買賣：含「買」→ BUY，含「賣」→ SELL"""
    if cell is None or (isinstance(cell, float) and pd.isna(cell)):
        return "BUY"  # 預設
    s = str(cell).strip()
    if "賣" in s:
        return "SELL"
    if "買" in s:
        return "BUY"
    # 若為數字：負數常為買進、正數為賣出（券商淨收付）
    n = _parse_number(s)
    return "SELL" if n >= 0 else "BUY"


def build_name_to_stock_id(session):
    """建立 股名 → stock_id 對照（stock_master 的 name 對應 stock_id）"""
    masters = session.query(StockMaster).all()
    name2id = {}
    for m in masters:
        if m.name:
            name2id[m.name.strip()] = m.stock_id
            # 去掉常見後綴方便對應
            for suffix in ["股", "電子", "科技", "金控", "證券"]:
                short = m.name.strip().replace(suffix, "").strip()
                if short and short not in name2id:
                    name2id[short] = m.stock_id
    return name2id


def parse_upload_to_rows(df, name2id):
    """
    將上傳的 DataFrame 解析為可寫入 Trade 的 dict 列表。
    回傳 (rows, errors)：rows 為成功解析的 list of dict，errors 為 list of (row_index, message)。
    """
    rows = []
    errors = []
    col_account = _find_column(df, COL_ACCOUNT)
    col_stock = _find_column(df, COL_STOCK_NAME)
    col_date = _find_column(df, COL_DATE)
    col_qty = _find_column(df, COL_QUANTITY)
    col_net = _find_column(df, COL_NET_AMOUNT)
    col_side = _find_column(df, COL_SIDE)  # 獨立買賣欄位
    col_price = _find_column(df, COL_PRICE)
    col_fee = _find_column(df, COL_FEE)
    col_tax = _find_column(df, COL_TAX)
    col_note = _find_column(df, COL_NOTE)

    if not col_stock:
        return [], [(0, "找不到「股名」或「股票名稱」欄位")]
    if not col_date:
        return [], [(0, "找不到「日期」欄位")]
    if not col_qty:
        return [], [(0, "找不到「成交股數」欄位")]
    if not col_price:
        return [], [(0, "找不到「成交價」欄位")]

    for idx, row in df.iterrows():
        stock_name = str(row.get(col_stock, "")).strip() if col_stock else ""
        if not stock_name or stock_name == "nan":
            errors.append((idx + 2, "股名為空"))  # +2 約為 Excel 列號
            continue
        stock_id = name2id.get(stock_name)
        if not stock_id:
            # 嘗試只取前幾字或部分匹配
            for k, sid in name2id.items():
                if k in stock_name or stock_name in k:
                    stock_id = sid
                    break
        if not stock_id:
            errors.append((idx + 2, f"找不到股票代號：{stock_name}（請先至主檔/設定同步股票列表或匯入 stock_master）"))
            continue

        trade_date = _parse_date(row.get(col_date))
        if not trade_date:
            errors.append((idx + 2, f"日期無法解析：{row.get(col_date)}"))
            continue

        quantity = int(_parse_number(row.get(col_qty, 0)))
        if quantity <= 0:
            errors.append((idx + 2, "成交股數應大於 0"))
            continue

        price = _parse_number(row.get(col_price, 0))
        if price <= 0:
            errors.append((idx + 2, "成交價應大於 0"))
            continue

        side = _infer_side(row.get(col_net)) if col_net else "BUY"
        # 若有獨立「買賣」欄且內容為買進/賣出，優先使用
        if col_side:
            raw_side = str(row.get(col_side, "")).strip()
            if "賣" in raw_side:
                side = "SELL"
            elif "買" in raw_side:
                side = "BUY"
        user = str(row.get(col_account, "")).strip() if col_account else "匯入"
        if not user or user == "nan":
            user = "匯入"
        fee = row.get(col_fee)
        fee_val = _parse_number(fee) if fee is not None else None
        tax = row.get(col_tax)
        tax_val = _parse_number(tax) if tax is not None else None
        note = str(row.get(col_note, "")).strip() if col_note else None
        if note == "nan" or note == "":
            note = None

        rows.append({
            "user": user[:50],
            "stock_id": stock_id,
            "trade_date": trade_date,
            "side": side,
            "price": round(price, 2),
            "quantity": quantity,
            "is_daytrade": False,
            "fee": fee_val if fee_val else None,
            "tax": tax_val if tax_val else None,
            "note": note,
        })
    return rows, errors


# ---------- UI ----------
st.caption("支援券商交易紀錄匯出之 CSV 或 Excel（.xlsx）。欄位需包含：帳戶、股名、日期、成交股數、淨收付金額（或買賣）、成交價；可選手續費、交易稅、備註。買/賣會依「淨收付金額」內的「買進」「賣出」或正負數自動判斷。")

uploaded = st.file_uploader("上傳 CSV 或 Excel", type=["csv", "xlsx"], key="trade_import_file")
if not uploaded:
    st.info("請上傳檔案。若為券商匯出之 Excel，請先另存為 .xlsx 或 .csv（UTF-8）。")
    st.markdown("**預期欄位範例**：帳戶、股名、日期、成交股數、淨收付金額、成交價、手續費、交易稅、備註。")
    st.stop()

# 讀檔
try:
    if uploaded.name.lower().endswith(".xlsx"):
        df_raw = pd.read_excel(uploaded, engine="openpyxl")
    else:
        df_raw = pd.read_csv(uploaded, encoding="utf-8-sig")
except Exception as e:
    try:
        df_raw = pd.read_csv(uploaded, encoding="big5")
    except Exception:
        st.error(f"無法讀取檔案：{e}")
        st.stop()
    # if was xlsx we already failed
    if uploaded.name.lower().endswith(".xlsx"):
        st.error(f"無法讀取 Excel：{e}")
        st.stop()

if df_raw.empty:
    st.warning("檔案為空")
    st.stop()

# 取得股名→代號對照
sess = get_session()
name2id = build_name_to_stock_id(sess)
sess.close()

parsed, parse_errors = parse_upload_to_rows(df_raw, name2id)

st.subheader("預覽與欄位對應")
st.caption("下表為系統解析出的欄位對應；下方為即將匯入的筆數與錯誤列。")
st.dataframe(df_raw.head(20), use_container_width=True, hide_index=True)

if parse_errors:
    st.warning(f"共 {len(parse_errors)} 筆無法匯入（列號／原因）：")
    err_df = pd.DataFrame([{"列": e[0], "原因": e[1]} for e in parse_errors[:50]])
    st.dataframe(err_df, use_container_width=True, hide_index=True)
    if len(parse_errors) > 50:
        st.caption(f"… 尚有 {len(parse_errors) - 50} 筆錯誤")

if not parsed:
    st.error("沒有可匯入的筆數，請檢查欄位與股名是否已在 stock_master 中。")
    st.stop()

st.success(f"可匯入 **{len(parsed)}** 筆交易。")
PREVIEW_ROWS = 100
preview_df = pd.DataFrame([
    {
        "買賣人": r["user"],
        "股票": r["stock_id"],
        "日期": str(r["trade_date"]),
        "買/賣": r["side"],
        "當沖": r.get("is_daytrade", False),
        "價格": r["price"],
        "股數": r["quantity"],
        "手續費": r.get("fee"),
        "稅": r.get("tax"),
    }
    for r in parsed[:PREVIEW_ROWS]
])
st.dataframe(preview_df, use_container_width=True, hide_index=True, height=min(400, 50 + min(len(preview_df), 15) * 38))
if len(parsed) > PREVIEW_ROWS:
    st.caption(f"僅顯示前 {PREVIEW_ROWS} 筆，共 {len(parsed)} 筆。")
else:
    st.caption(f"共 {len(parsed)} 筆。")

# 唯讀環境可下載解析結果 CSV，於本機匯入或備存
dl_df = pd.DataFrame(parsed)
dl_df["trade_date"] = dl_df["trade_date"].astype(str)
buf = io.BytesIO()
dl_df.to_csv(buf, index=False, encoding="utf-8-sig")
buf.seek(0)
st.download_button("下載解析結果 CSV（唯讀環境可於本機匯入）", data=buf.getvalue(), file_name="trades_parsed.csv", mime="text/csv", key="dl_parsed_trades")

if st.button("確認匯入", type="primary", key="do_import"):
    sess = get_session()
    try:
        for r in parsed:
            sess.add(Trade(
                user=r["user"],
                stock_id=r["stock_id"],
                trade_date=r["trade_date"],
                side=r["side"],
                price=r["price"],
                quantity=r["quantity"],
                is_daytrade=r.get("is_daytrade", False),
                fee=r.get("fee"),
                tax=r.get("tax"),
                note=r.get("note"),
            ))
        sess.commit()
        st.success(f"已成功匯入 {len(parsed)} 筆交易。可至「交易輸入」「日成交彙總」「個股明細」「投資績效」檢視。")
    except OperationalError as e:
        sess.rollback()
        st.error("無法寫入資料庫（目前環境為唯讀，例如 Streamlit Cloud）。請在本機執行以匯入交易。")
        st.caption("**若在雲端執行**：可先使用上方「下載解析結果 CSV」儲存後，於本機開啟此 App 再上傳該 CSV 匯入；或於 **Secrets** 設定 `DATABASE_URL` 連線至可寫入的雲端資料庫（如 PostgreSQL）。")
        st.caption(f"技術細節：{e}")
    except Exception as e:
        sess.rollback()
        st.error(f"匯入失敗：{e}")
    finally:
        sess.close()
