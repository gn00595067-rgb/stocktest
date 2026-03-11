# -*- coding: utf-8 -*-
"""Excel 沖銷庫存資料匯入：匯入含已出售（自訂沖銷配對、當沖）與庫存股票的 Excel，每個分頁代表一家公司。"""
import re
import sys
import os
import io
from datetime import datetime
from collections import defaultdict

import streamlit as st
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.stock_list_loader import ensure_google_sheet_loaded
ensure_google_sheet_loaded()
from db.database import get_session
from db.models import Trade, StockMaster, CustomMatchRule
from sqlalchemy.exc import OperationalError, IntegrityError

st.set_page_config(page_title="Excel 沖銷庫存資料匯入", layout="wide")
st.title("Excel 沖銷庫存資料匯入")
st.caption("匯入含 **已出售**（自訂沖銷配對、當沖紀錄）與 **庫存股票** 的 Excel：**每個分頁代表一家公司**。匯入後會建立交易並寫入自定沖銷規則。")

# 顯示上次匯入成功訊息（留在畫面上不消失，直到上傳新檔案）
if st.session_state.get("excel_import_success_msg"):
    st.success(st.session_state["excel_import_success_msg"])

# ---------- 輔助：民國年、數字、欄位對照 ----------
def _parse_roc_date(s):
    """解析民國年日期：113/8/28 -> date, 115/2/26 -> date。"""
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return None
    s = str(s).strip()
    if not s or s.startswith("#"):
        return None
    parts = re.split(r"[/\-]", s)
    if len(parts) >= 3:
        try:
            y = int(re.search(r"\d+", parts[0]).group())
            m = int(re.search(r"\d+", parts[1]).group())
            d = int(re.search(r"\d+", parts[2]).group())
            if y < 200:
                y += 1911
            return datetime(y, m, d).date()
        except (ValueError, TypeError, AttributeError):
            pass
    return None


def _parse_num(s):
    """解析數字，忽略 #VALUE!、#REF!、空值、千分位。"""
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return None
    s = str(s).strip()
    if not s or s.startswith("#"):
        return None
    s = s.replace(",", "")
    m = re.search(r"-?[\d.]+", s)
    if m:
        try:
            return float(m.group())
        except ValueError:
            pass
    return None


def _stock_id_from_sheet_name(name):
    """從分頁名稱取出股票代號：3189景碩 -> 3189。"""
    if not name:
        return None
    m = re.match(r"^(\d{4})", str(name).strip())
    return m.group(1) if m else None


def _stock_id_from_company_cell(cell):
    """從「公司」欄位取出股票代號：3189 景碩 -> 3189。"""
    if cell is None or (isinstance(cell, float) and pd.isna(cell)):
        return None
    m = re.match(r"^(\d{4})", str(cell).strip())
    return m.group(1) if m else None


# ---------- 讀取 Excel 並偵測「已出售」「庫存股票」區塊 ----------
def _read_sheet_rows(path, sheet_name):
    """讀取單一分頁所有列，回傳 list of list。"""
    df = pd.read_excel(path, sheet_name=sheet_name, header=None, engine="openpyxl")
    return df.values.tolist()


def _find_header_row(rows, required_cols, from_row=0):
    """找到同時包含 required_cols 的列當作表頭，回傳 (row_index, {col_name: col_index})。"""
    for ri in range(from_row, len(rows)):
        row = rows[ri]
        if not row:
            continue
        cells = [str(c).strip() if c is not None and not (isinstance(c, float) and pd.isna(c)) else "" for c in row]
        found = {}
        for col_name in required_cols:
            for ci, c in enumerate(cells):
                if col_name in c or c == col_name:
                    found[col_name] = ci
                    break
        if len(found) >= len(required_cols):
            return ri, found
    return None, {}


def _parse_sold_section(rows, stock_id, user, from_row=0):
    """
    解析「已出售」區塊：表頭需含 買賣日、股數、股價、出售日、賣價。
    每列一筆「買─賣」配對，建立 Trade(BUY)、Trade(SELL)、CustomMatchRule。
    回傳 (trades, rules, errors)。
    """
    required = ["買賣日", "股數", "股價", "出售日", "賣價"]
    header_row_idx, col_map = _find_header_row(rows, required, from_row)
    if header_row_idx is None or not col_map:
        return [], [], [f"找不到已出售表頭（需含：{', '.join(required)}）"]
    header_cells = rows[header_row_idx]
    for label in ["手續費", "證交稅"]:
        for ci, c in enumerate(header_cells):
            if c is not None and label in str(c):
                if label == "手續費":
                    if "手續費" not in col_map:
                        col_map["手續費"] = ci
                    else:
                        col_map["手續費賣"] = ci
                elif label not in col_map:
                    col_map[label] = ci
                break
    trades = []
    rules = []
    errors = []
    i_buy_date = col_map["買賣日"]
    i_qty = col_map["股數"]
    i_price = col_map["股價"]
    i_sell_date = col_map["出售日"]
    i_sell_price = col_map["賣價"]
    i_fee_buy = col_map.get("手續費")
    i_fee_sell = col_map.get("手續費賣")
    i_tax = col_map.get("證交稅")
    for ri in range(header_row_idx + 1, len(rows)):
        row = rows[ri]
        if not row:
            continue
        ncol = len(row)
        if ncol <= max(i_buy_date, i_sell_date, i_qty, i_price, i_sell_price):
            continue
        buy_date_val = row[i_buy_date] if i_buy_date < ncol else None
        sell_date_val = row[i_sell_date] if i_sell_date < ncol else None
        if buy_date_val is None and sell_date_val is None:
            continue
        buy_date = _parse_roc_date(buy_date_val)
        sell_date = _parse_roc_date(sell_date_val)
        if not buy_date or not sell_date:
            continue
        qty = _parse_num(row[i_qty] if i_qty < ncol else None)
        price_buy = _parse_num(row[i_price] if i_price < ncol else None)
        price_sell = _parse_num(row[i_sell_price] if i_sell_price < ncol else None)
        if qty is None or qty <= 0 or price_buy is None or price_buy <= 0 or price_sell is None or price_sell <= 0:
            continue
        qty = int(qty)
        if "小計" in str(row[1] if ncol > 1 else ""):
            break
        fee_buy = _parse_num(row[i_fee_buy]) if i_fee_buy is not None and i_fee_buy < ncol else None
        fee_sell = _parse_num(row[i_fee_sell]) if i_fee_sell is not None and i_fee_sell < ncol else (_parse_num(row[i_fee_buy + 4]) if i_fee_buy is not None and i_fee_buy + 4 < ncol else None)
        tax_sell = _parse_num(row[i_tax]) if i_tax is not None and i_tax < ncol else None
        is_daytrade = buy_date == sell_date
        trades.append({
            "user": user,
            "stock_id": stock_id,
            "trade_date": buy_date,
            "side": "BUY",
            "price": round(price_buy, 2),
            "quantity": qty,
            "is_daytrade": is_daytrade,
            "fee": round(fee_buy, 2) if fee_buy is not None else None,
            "tax": None,
            "note": "Excel沖銷庫存-已出售",
        })
        trades.append({
            "user": user,
            "stock_id": stock_id,
            "trade_date": sell_date,
            "side": "SELL",
            "price": round(price_sell, 2),
            "quantity": qty,
            "is_daytrade": is_daytrade,
            "fee": round(fee_sell, 2) if fee_sell is not None else None,
            "tax": round(tax_sell, 2) if tax_sell is not None else None,
            "note": "Excel沖銷庫存-已出售",
        })
        rules.append({"qty": qty})
    return trades, rules, errors


def _parse_inventory_section(rows, stock_id, user, from_row=0):
    """解析「庫存股票」區塊：表頭需含 買賣日、股數、股價，僅建立 Buy 交易。"""
    required = ["買賣日", "股數", "股價"]
    header_row_idx, col_map = _find_header_row(rows, required, from_row)
    if header_row_idx is None or not col_map:
        return [], []
    trades = []
    i_buy_date = col_map["買賣日"]
    i_qty = col_map["股數"]
    i_price = col_map["股價"]
    i_fee = col_map.get("手續費")
    for ri in range(header_row_idx + 1, len(rows)):
        row = rows[ri]
        if not row or len(row) <= max(i_buy_date, i_qty, i_price):
            continue
        buy_date = _parse_roc_date(row[i_buy_date] if i_buy_date < len(row) else None)
        if not buy_date:
            continue
        qty = _parse_num(row[i_qty] if i_qty < len(row) else None)
        price = _parse_num(row[i_price] if i_price < len(row) else None)
        if qty is None or qty <= 0 or price is None or price <= 0:
            continue
        qty = int(qty)
        fee = _parse_num(row[i_fee]) if i_fee is not None and i_fee < len(row) else None
        trades.append({
            "user": user,
            "stock_id": stock_id,
            "trade_date": buy_date,
            "side": "BUY",
            "price": round(price, 2),
            "quantity": qty,
            "is_daytrade": False,
            "fee": round(fee, 2) if fee is not None else None,
            "tax": None,
            "note": "Excel沖銷庫存-庫存",
        })
    return trades, []


def _locate_sections(rows):
    """找到「已出售」與「庫存股票」的起始列（從該列往下找表頭）。"""
    result = {"已出售": None, "庫存股票": None}
    for ri, row in enumerate(rows):
        if not row:
            continue
        for cell in row:
            if cell is None:
                continue
            s = str(cell).strip()
            if "已出售" in s or "已出貨" in s:
                result["已出售"] = ri + 1
            if "庫存股票" in s or (s == "庫存" and result["庫存股票"] is None):
                result["庫存股票"] = ri + 1
    return result


def parse_partner_excel(path, sheet_name, user="匯入"):
    """解析單一分頁，回傳 (trades, rules, errors)。"""
    rows = _read_sheet_rows(path, sheet_name)
    stock_id = _stock_id_from_sheet_name(sheet_name)
    if not stock_id:
        for row in rows:
            for cell in row:
                sid = _stock_id_from_company_cell(cell)
                if sid:
                    stock_id = sid
                    break
            if stock_id:
                break
    if not stock_id:
        return [], [], [f"分頁「{sheet_name}」無法取得股票代號（請確認分頁名或「公司」欄為 4 碼數字開頭）"]
    sections = _locate_sections(rows)
    all_trades = []
    all_rules = []
    all_errors = []
    if sections["已出售"] is not None:
        t, r, e = _parse_sold_section(rows, stock_id, user, sections["已出售"])
        all_trades.extend(t)
        all_rules.extend(r)
        all_errors.extend(e)
    if sections["庫存股票"] is not None:
        t, e = _parse_inventory_section(rows, stock_id, user, sections["庫存股票"])
        all_trades.extend(t)
        all_errors.extend(e)
    return all_trades, all_rules, all_errors


# ---------- UI ----------
user_default = st.text_input("買賣人／帳戶名稱", value="匯入", key="partner_import_user")
uploaded = st.file_uploader("上傳 Excel（.xlsx）", type=["xlsx"], key="partner_excel")
if not uploaded:
    st.info("請上傳 .xlsx 檔案。每個分頁代表一家公司，需含「已出售」與／或「庫存股票」區塊。")
    st.markdown("**預期結構**：分頁名稱或表內「公司」欄為股票代號（如 3189景碩）；已出售區塊需有 買賣日、股數、股價、出售日、賣價；庫存區塊需有 買賣日、股數、股價。")
    st.stop()

# 上傳新檔案時清除上次的成功訊息
_upload_key = (uploaded.name, uploaded.size)
if _upload_key != st.session_state.get("excel_import_last_file"):
    st.session_state.pop("excel_import_success_msg", None)
st.session_state["excel_import_last_file"] = _upload_key

try:
    xl = pd.ExcelFile(uploaded, engine="openpyxl")
    sheet_names = xl.sheet_names
except Exception as e:
    st.error(f"無法讀取 Excel：{e}")
    st.stop()

if not sheet_names:
    st.warning("此檔案沒有任何分頁")
    st.stop()

import tempfile
with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
    tmp.write(uploaded.getvalue())
    path = tmp.name

try:
    selected_sheets = st.multiselect(
        "選擇要匯入的分頁（預設全選）",
        sheet_names,
        default=sheet_names,
        key="partner_sheets",
    )
    if not selected_sheets:
        st.warning("請至少選擇一個分頁")
        st.stop()

    all_trades = []
    all_rules = []
    all_errors = []
    by_sheet = []
    for sn in selected_sheets:
        t, r, e = parse_partner_excel(path, sn, user_default)
        all_trades.extend(t)
        all_rules.extend(r)
        all_errors.extend(e)
        by_sheet.append((sn, len(t), len(r), e))

    if all_errors:
        st.warning("解析過程有下列問題：")
        for err in all_errors[:20]:
            st.caption(f"• {err}")
        if len(all_errors) > 20:
            st.caption(f"… 共 {len(all_errors)} 則")

    st.subheader("預覽")
    st.caption("各分頁解析筆數（交易數、配對數）：")
    for sn, n_t, n_r, _ in by_sheet:
        st.caption(f"• **{sn}**：{n_t} 筆交易、{n_r} 筆沖銷配對")
    if not all_trades:
        st.warning("沒有可匯入的交易。請確認 Excel 內「已出售」「庫存股票」區塊與欄位名稱。")
        st.stop()

    preview_rows = []
    for tr in all_trades[:50]:
        preview_rows.append({
            "買賣人": tr["user"],
            "股票": tr["stock_id"],
            "日期": str(tr["trade_date"]),
            "買/賣": tr["side"],
            "價格": tr["price"],
            "股數": tr["quantity"],
            "當沖": tr.get("is_daytrade", False),
            "手續費": tr.get("fee"),
            "稅": tr.get("tax"),
        })
    st.dataframe(pd.DataFrame(preview_rows), use_container_width=True, hide_index=True)
    if len(all_trades) > 50:
        st.caption(f"僅顯示前 50 筆，共 {len(all_trades)} 筆交易、{len(all_rules)} 筆沖銷配對。")

    sess = get_session()
    try:
        existing_ids = {m.stock_id for m in sess.query(StockMaster.stock_id).all()}
    finally:
        sess.close()
    missing_stocks = list(set(t["stock_id"] for t in all_trades) - existing_ids)
    if missing_stocks:
        st.warning(f"以下股票代號不在主檔中：{', '.join(sorted(missing_stocks))}。建議先至「主檔/設定」同步股票清單。")

    # 唯讀環境可下載解析結果，於本機用「交易匯入」上傳 CSV 或設定 DATABASE_URL 後再匯入
    dl_trades = pd.DataFrame(all_trades)
    dl_trades["trade_date"] = dl_trades["trade_date"].astype(str)
    buf_dl = io.BytesIO()
    dl_trades.to_csv(buf_dl, index=False, encoding="utf-8-sig")
    buf_dl.seek(0)
    st.download_button(
        "下載解析結果 CSV（唯讀環境可於本機再次開啟本頁上傳同一份 Excel 匯入）",
        data=buf_dl.getvalue(),
        file_name="excel_沖銷庫存_解析結果.csv",
        mime="text/csv",
        key="dl_partner_parsed",
    )

    if st.button("確認匯入", type="primary", key="partner_do_import"):
        sess = get_session()
        try:
            created_buy = []
            created_sell = []
            for tr in all_trades:
                t = Trade(
                    user=tr["user"],
                    stock_id=tr["stock_id"],
                    trade_date=tr["trade_date"],
                    side=tr["side"],
                    price=tr["price"],
                    quantity=tr["quantity"],
                    is_daytrade=tr.get("is_daytrade", False),
                    fee=tr.get("fee"),
                    tax=tr.get("tax"),
                    note=tr.get("note"),
                )
                sess.add(t)
                sess.flush()
                if tr["side"] == "BUY":
                    created_buy.append(t.id)
                else:
                    created_sell.append(t.id)
            for i, r in enumerate(all_rules):
                if i < len(created_buy) and i < len(created_sell):
                    sess.add(CustomMatchRule(sell_trade_id=created_sell[i], buy_trade_id=created_buy[i], matched_qty=r["qty"]))
            sess.commit()
            st.session_state["excel_import_success_msg"] = f"已匯入 {len(all_trades)} 筆交易、{len(all_rules)} 筆自定沖銷規則。可至「交易輸入」「自定沖銷設定」「Portfolio」檢視。"
            st.session_state["excel_import_last_file"] = (uploaded.name, uploaded.size)
            st.rerun()
        except OperationalError as e:
            sess.rollback()
            st.error("無法寫入資料庫（目前環境可能唯讀）。請在本機執行或設定 DATABASE_URL。")
            st.caption("**若在雲端執行**：請在本機開啟此 App，再次進入「Excel 沖銷庫存資料匯入」上傳同一份 Excel 即可寫入；或於 **Secrets** 設定 `DATABASE_URL` 連線至可寫入的雲端資料庫（如 PostgreSQL）後即可在雲端直接匯入。")
            st.caption(f"技術細節：{e}")
        except IntegrityError as e:
            sess.rollback()
            st.error("寫入時發生主鍵或外鍵錯誤，請確認資料。")
            st.caption(str(e))
        except Exception as e:
            sess.rollback()
            st.error(f"匯入失敗：{e}")
        finally:
            sess.close()
finally:
    if path and os.path.isfile(path):
        try:
            os.remove(path)
        except Exception:
            pass
