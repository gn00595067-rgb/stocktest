# -*- coding: utf-8 -*-
"""交易匯入：券商 CSV/Excel 交易紀錄 ＋ Excel 沖銷庫存資料（上下排列）"""
import re
import io
import os
import tempfile
import time
from datetime import datetime
from collections import defaultdict

import streamlit as st
import pandas as pd
from sqlalchemy.exc import OperationalError, IntegrityError

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.stock_list_loader import ensure_google_sheet_loaded
ensure_google_sheet_loaded()
try:
    if hasattr(st, "secrets") and st.secrets.get("FINMIND_TOKEN"):
        os.environ.setdefault("FINMIND_TOKEN", str(st.secrets["FINMIND_TOKEN"]).strip())
except Exception:
    pass
from db.database import get_session
from db.models import Trade, StockMaster, CustomMatchRule

st.set_page_config(page_title="交易匯入", layout="wide")
st.title("交易匯入")
st.caption("本頁提供兩種匯入方式：**一、券商 CSV/Excel 交易紀錄**；**二、Excel 沖銷庫存資料**（已出售＋庫存，每分頁一家公司）。")

# ---------- 清空所有資料（交易＋自定沖銷規則，主檔股票列表保留） ----------
with st.expander("⚠️ 清空所有資料", expanded=False):
    st.caption("將刪除**所有交易**與**所有自定沖銷規則**，主檔股票列表會保留。此操作無法復原。")
    if st.button("清空所有交易與沖銷規則", type="secondary", key="clear_all_btn"):
        sess = get_session()
        try:
            n_rules = sess.query(CustomMatchRule).delete()
            n_trades = sess.query(Trade).delete()
            sess.commit()
            st.session_state["clear_all_done"] = f"已刪除 {n_trades} 筆交易、{n_rules} 筆自定沖銷規則。"
            st.rerun()
        except Exception as e:
            sess.rollback()
            st.error(f"清空失敗：{e}")
        finally:
            sess.close()
    if st.session_state.get("clear_all_done"):
        st.success(st.session_state["clear_all_done"])
        st.session_state.pop("clear_all_done", None)

# ========== 一、券商 CSV / Excel 交易紀錄 ==========
st.subheader("一、券商 CSV / Excel 交易紀錄")
COL_ACCOUNT = ["帳戶", "戶名", "買賣人", "帳號"]
COL_STOCK_NAME = ["股名", "股票名稱", "名稱", "股票", "公司"]
COL_DATE = ["日期", "交易日期", "成交日", "買賣日"]
COL_QUANTITY = ["成交股數", "股數", "數量"]
COL_NET_AMOUNT = ["淨收付金額", "淨收付", "買賣", "方向"]
COL_SIDE = ["買賣", "方向", "多空"]
COL_PRICE = ["成交價", "價格", "單價", "股價"]
COL_FEE = ["手續費", "佣金", "手續費率"]
COL_TAX = ["交易稅", "證交稅", "稅"]
COL_NOTE = ["備註", "備註欄", "說明"]


def _find_column(df, candidates):
    cols = [str(c).strip() for c in df.columns]
    for cand in candidates:
        for col in cols:
            if cand == col or cand in col:
                return col
    return None


def _parse_number(s):
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return 0
    s = str(s).strip().replace(",", "")
    m = re.search(r"-?[\d.]+", s)
    return float(m.group()) if m else 0


def _parse_date(s):
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return None
    s = str(s).strip()[:20]
    if "/" in s:
        parts = s.split("/")
        if len(parts) >= 3:
            try:
                y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
                if y < 200:
                    y += 1911
                return datetime(y, m, d).date()
            except (ValueError, TypeError):
                pass
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
    if cell is None or (isinstance(cell, float) and pd.isna(cell)):
        return "BUY"
    s = str(cell).strip()
    if "賣" in s:
        return "SELL"
    if "買" in s:
        return "BUY"
    n = _parse_number(s)
    return "SELL" if n >= 0 else "BUY"


def build_name_to_stock_id(session):
    masters = session.query(StockMaster).all()
    name2id = {}
    for m in masters:
        if m.name:
            name2id[m.name.strip()] = m.stock_id
            for suffix in ["股", "電子", "科技", "金控", "證券"]:
                short = m.name.strip().replace(suffix, "").strip()
                if short and short not in name2id:
                    name2id[short] = m.stock_id
    return name2id


def parse_upload_to_rows(df, name2id):
    rows = []
    errors = []
    col_account = _find_column(df, COL_ACCOUNT)
    col_stock = _find_column(df, COL_STOCK_NAME)
    col_date = _find_column(df, COL_DATE)
    col_qty = _find_column(df, COL_QUANTITY)
    col_net = _find_column(df, COL_NET_AMOUNT)
    col_side = _find_column(df, COL_SIDE)
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
            errors.append((idx + 2, "股名為空"))
            continue
        stock_id = name2id.get(stock_name)
        if not stock_id:
            for k, sid in name2id.items():
                if k in stock_name or stock_name in k:
                    stock_id = sid
                    break
        if not stock_id:
            errors.append((idx + 2, f"找不到股票代號：{stock_name}"))
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
        if col_side:
            raw_side = str(row.get(col_side, "")).strip()
            if "賣" in raw_side:
                side = "SELL"
            elif "買" in raw_side:
                side = "BUY"
        user = str(row.get(col_account, "")).strip() if col_account else "匯入"
        if not user or user == "nan":
            user = "匯入"
        fee_val = _parse_number(row.get(col_fee)) if col_fee and row.get(col_fee) is not None else None
        tax_val = _parse_number(row.get(col_tax)) if col_tax and row.get(col_tax) is not None else None
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


uploaded = st.file_uploader("上傳 CSV 或 Excel", type=["csv", "xlsx"], key="trade_import_file")
if not uploaded:
    st.info("請上傳檔案。若為券商匯出之 Excel，請先另存為 .xlsx 或 .csv（UTF-8）。")
    st.markdown("**預期欄位範例**：帳戶、股名、日期、成交股數、淨收付金額、成交價、手續費、交易稅、備註。")
else:
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
            df_raw = None
        if df_raw is None and uploaded.name.lower().endswith(".xlsx"):
            st.error(f"無法讀取 Excel：{e}")
            df_raw = None
    if df_raw is not None and not df_raw.empty:
        sess = get_session()
        name2id = build_name_to_stock_id(sess)
        sess.close()
        parsed, parse_errors = parse_upload_to_rows(df_raw, name2id)
        st.caption("下表為系統解析出的欄位對應；下方為即將匯入的筆數與錯誤列。")
        st.dataframe(df_raw.head(20), use_container_width=True, hide_index=True)
        if parse_errors:
            st.warning(f"共 {len(parse_errors)} 筆無法匯入（列號／原因）：")
            err_df = pd.DataFrame([{"列": e[0], "原因": e[1]} for e in parse_errors[:50]])
            st.dataframe(err_df, use_container_width=True, hide_index=True)
            if len(parse_errors) > 50:
                st.caption(f"… 尚有 {len(parse_errors) - 50} 筆錯誤")
        if parsed:
            st.success(f"可匯入 **{len(parsed)}** 筆交易。")
            PREVIEW_ROWS = 100
            preview_df = pd.DataFrame([
                {"買賣人": r["user"], "股票": r["stock_id"], "日期": str(r["trade_date"]), "買/賣": r["side"], "當沖": r.get("is_daytrade", False), "價格": r["price"], "股數": r["quantity"], "手續費": r.get("fee"), "稅": r.get("tax")}
                for r in parsed[:PREVIEW_ROWS]
            ])
            _fmt_preview = {"價格": "{:,.2f}", "股數": "{:,.0f}"}
            for c in ("手續費", "稅"):
                if c in preview_df.columns and preview_df[c].notna().any():
                    _fmt_preview[c] = "{:,.2f}"
            st.dataframe(preview_df.style.format(_fmt_preview, na_rep="—"), use_container_width=True, hide_index=True, height=min(400, 50 + min(len(preview_df), 15) * 38))
            if len(parsed) > PREVIEW_ROWS:
                st.caption(f"僅顯示前 {PREVIEW_ROWS} 筆，共 {len(parsed)} 筆。")
            else:
                st.caption(f"共 {len(parsed)} 筆。")
            dl_df = pd.DataFrame(parsed)
            dl_df["trade_date"] = dl_df["trade_date"].astype(str)
            buf = io.BytesIO()
            dl_df.to_csv(buf, index=False, encoding="utf-8-sig")
            buf.seek(0)
            st.download_button("下載解析結果 CSV（唯讀環境可於本機匯入）", data=buf.getvalue(), file_name="trades_parsed.csv", mime="text/csv", key="dl_parsed_trades")
            auto_daytrade_match = st.checkbox(
                "匯入時自動當沖配對（建立自定沖銷規則）",
                value=True,
                key="import_auto_daytrade_match",
                help="會針對同日、同股票、同買賣人的 BUY/SELL 進行保守配對（先配完最明確的股數），並建立自定沖銷規則。若只有單邊或股數不足則不會硬配。",
            )
            if st.button("確認匯入", type="primary", key="do_import"):
                sess = get_session()
                try:
                    created = []
                    for r in parsed:
                        t = Trade(
                            user=r["user"], stock_id=r["stock_id"], trade_date=r["trade_date"], side=r["side"],
                            price=r["price"], quantity=r["quantity"], is_daytrade=r.get("is_daytrade", False),
                            fee=r.get("fee"), tax=r.get("tax"), note=r.get("note"),
                        )
                        sess.add(t)
                        sess.flush()
                        created.append(t)

                    # 自動當沖配對：同日/同股/同買賣人 的 BUY/SELL 以股數做保守配對，寫入 CustomMatchRule
                    if auto_daytrade_match and created:
                        by_key = defaultdict(list)
                        for t in created:
                            k = (str(getattr(t, "user", "") or ""), str(getattr(t, "stock_id", "") or ""), getattr(t, "trade_date", None))
                            by_key[k].append(t)

                        def _is_buy(t):
                            return (getattr(t, "side", "") or "").upper() == "BUY"

                        for (u, sid, d), ts in by_key.items():
                            buys = [t for t in ts if _is_buy(t)]
                            sells = [t for t in ts if not _is_buy(t)]
                            if not buys or not sells:
                                continue
                            # 依建立順序（id）做 FIFO；同日無更細時間就用此作為保守順序
                            buys = sorted(buys, key=lambda x: x.id)
                            sells = sorted(sells, key=lambda x: x.id)
                            buy_rem = {t.id: int(getattr(t, "quantity", 0) or 0) for t in buys}
                            sell_rem = {t.id: int(getattr(t, "quantity", 0) or 0) for t in sells}
                            for s in sells:
                                sr = sell_rem.get(s.id, 0)
                                if sr <= 0:
                                    continue
                                for b in buys:
                                    br = buy_rem.get(b.id, 0)
                                    if sr <= 0:
                                        break
                                    if br <= 0:
                                        continue
                                    qty = min(sr, br)
                                    if qty <= 0:
                                        continue
                                    sess.add(CustomMatchRule(sell_trade_id=int(s.id), buy_trade_id=int(b.id), matched_qty=int(qty)))
                                    # 標記為當沖（可選）：兩邊都有配對就算當沖
                                    s.is_daytrade = True
                                    b.is_daytrade = True
                                    sr -= qty
                                    buy_rem[b.id] = br - qty
                                sell_rem[s.id] = sr
                    sess.commit()
                    st.success(f"已成功匯入 {len(parsed)} 筆交易。可至「交易輸入」「個股明細」「投資績效」檢視。")
                except OperationalError as e:
                    sess.rollback()
                    st.error("無法寫入資料庫（目前環境為唯讀）。請在本機執行以匯入交易。")
                    st.caption("**若在雲端執行**：可先使用上方「下載解析結果 CSV」儲存後，於本機開啟此 App 再上傳該 CSV 匯入；或於 **Secrets** 設定 `DATABASE_URL`。")
                except Exception as e:
                    sess.rollback()
                    st.error(f"匯入失敗：{e}")
                finally:
                    sess.close()
        else:
            st.error("沒有可匯入的筆數，請檢查欄位與股名是否已在 stock_master 中。")
    elif df_raw is not None and df_raw.empty:
        st.warning("檔案為空")

st.markdown("---")

# ========== 二、Excel 沖銷庫存資料匯入 ==========
st.subheader("二、Excel 沖銷庫存資料匯入")
st.caption("匯入含 **已出售**（自訂沖銷配對、當沖紀錄）與 **庫存股票** 的 Excel：**每個分頁代表一家公司**。匯入後會建立交易並寫入自定沖銷規則。")


def _parse_roc_date(s):
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
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return None
    s = str(s).strip()
    if not s or s.startswith("#"):
        return None
    s = s.replace(",", "")
    m = re.search(r"-?[\d.]+", s)
    return float(m.group()) if m else None


def _stock_id_from_sheet_name(name):
    if not name:
        return None
    m = re.match(r"^(\d{4})", str(name).strip())
    return m.group(1) if m else None


def _stock_id_from_company_cell(cell):
    if cell is None or (isinstance(cell, float) and pd.isna(cell)):
        return None
    m = re.match(r"^(\d{4})", str(cell).strip())
    return m.group(1) if m else None


def _read_sheet_rows(path, sheet_name):
    df = pd.read_excel(path, sheet_name=sheet_name, header=None, engine="openpyxl")
    return df.values.tolist()


def _find_header_row(rows, required_cols, from_row=0):
    for ri in range(from_row, len(rows)):
        row = rows[ri]
        if not row:
            continue
        cells = [str(c).strip() if c is not None and not (isinstance(c, float) and pd.isna(c)) else "" for c in row]
        found = {}
        for col_name in required_cols:
            for ci, c in enumerate(cells):
                if c == col_name:
                    found[col_name] = ci
                    break
                if col_name in c:
                    # 避免「股價」對到「賣價」（股價 是 賣價 的子字串）
                    if col_name == "股價" and "賣" in c:
                        continue
                    found[col_name] = ci
                    break
        if len(found) >= len(required_cols):
            return ri, found
    return None, {}


def _parse_sold_section(rows, stock_id, user, from_row=0):
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
        trades.append({"user": user, "stock_id": stock_id, "trade_date": buy_date, "side": "BUY", "price": round(price_buy, 2), "quantity": qty, "is_daytrade": is_daytrade, "fee": round(fee_buy, 2) if fee_buy is not None else None, "tax": None, "note": "Excel沖銷庫存-已出售"})
        trades.append({"user": user, "stock_id": stock_id, "trade_date": sell_date, "side": "SELL", "price": round(price_sell, 2), "quantity": qty, "is_daytrade": is_daytrade, "fee": round(fee_sell, 2) if fee_sell is not None else None, "tax": round(tax_sell, 2) if tax_sell is not None else None, "note": "Excel沖銷庫存-已出售"})
        rules.append({"qty": qty})
    return trades, rules, errors


def _parse_inventory_section(rows, stock_id, user, from_row=0):
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
        # 跳過小計列，避免把合計當成一筆買進
        if any("小計" in str(c) for c in row if c is not None):
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
        trades.append({"user": user, "stock_id": stock_id, "trade_date": buy_date, "side": "BUY", "price": round(price, 2), "quantity": qty, "is_daytrade": False, "fee": round(fee, 2) if fee is not None else None, "tax": None, "note": "Excel沖銷庫存-庫存"})
    return trades, []


def _locate_sections(rows):
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
        return [], [], [f"分頁「{sheet_name}」無法取得股票代號"]
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


user_default = st.text_input("買賣人／帳戶名稱", value="匯入", key="partner_import_user")
uploaded2 = st.file_uploader("上傳 Excel（.xlsx）", type=["xlsx"], key="partner_excel")
if not uploaded2:
    st.info("請上傳 .xlsx 檔案。每個分頁代表一家公司，需含「已出售」與／或「庫存股票」區塊。")
    st.markdown("**預期結構**：分頁名稱或表內「公司」欄為股票代號（如 3189景碩）；已出售區塊需有 買賣日、股數、股價、出售日、賣價；庫存區塊需有 買賣日、股數、股價。")
else:
    _upload_key = (uploaded2.name, uploaded2.size)
    if _upload_key != st.session_state.get("excel_import_last_file"):
        st.session_state.pop("excel_import_success_msg", None)
        st.session_state.pop("excel_import_success_time", None)
    st.session_state["excel_import_last_file"] = _upload_key
    path = None
    try:
        xl = pd.ExcelFile(uploaded2, engine="openpyxl")
        sheet_names = xl.sheet_names
    except Exception as e:
        st.error(f"無法讀取 Excel：{e}")
        sheet_names = []
    if not sheet_names:
        st.warning("此檔案沒有任何分頁")
    else:
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            tmp.write(uploaded2.getvalue())
            path = tmp.name
        try:
            selected_sheets = st.multiselect("選擇要匯入的分頁（預設全選）", sheet_names, default=sheet_names, key="partner_sheets")
            if not selected_sheets:
                st.warning("請至少選擇一個分頁")
            else:
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
                st.caption("各分頁解析筆數（交易數、配對數）：")
                for sn, n_t, n_r, _ in by_sheet:
                    st.caption(f"• **{sn}**：{n_t} 筆交易、{n_r} 筆沖銷配對")
                if not all_trades:
                    st.warning("沒有可匯入的交易。請確認 Excel 內「已出售」「庫存股票」區塊與欄位名稱。")
                else:
                    # 偵測買進股價異常高（可能曾把「賣價」欄當成「股價」讀入）
                    high_price_buys = [(t["stock_id"], t["trade_date"], t["price"], t["quantity"]) for t in all_trades if t.get("side") == "BUY" and float(t.get("price") or 0) > 600]
                    if high_price_buys:
                        st.warning("⚠️ 偵測到 **買進股價 > 600** 的筆數（若該檔歷史股價未超過 600，可能是表頭「股價」對到「賣價」欄導致）。請確認預覽表內價格是否合理後再匯入。")
                        for sid, d, p, q in high_price_buys[:10]:
                            st.caption(f"• 股票 {sid} 日期 {d} 買進 股價={p:,.2f} 股數={q:,}")
                    preview_rows = []
                    for tr in all_trades[:50]:
                        preview_rows.append({"買賣人": tr["user"], "股票": tr["stock_id"], "日期": str(tr["trade_date"]), "買/賣": tr["side"], "價格": tr["price"], "股數": tr["quantity"], "當沖": tr.get("is_daytrade", False), "手續費": tr.get("fee"), "稅": tr.get("tax")})
                    _pr_df = pd.DataFrame(preview_rows)
                    _fmt_pr = {"價格": "{:,.2f}", "股數": "{:,.0f}"}
                    if "手續費" in _pr_df.columns and _pr_df["手續費"].notna().any():
                        _fmt_pr["手續費"] = "{:,.2f}"
                    if "稅" in _pr_df.columns and _pr_df["稅"].notna().any():
                        _fmt_pr["稅"] = "{:,.2f}"
                    st.dataframe(_pr_df.style.format(_fmt_pr, na_rep="—"), use_container_width=True, hide_index=True)
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
                    dl_trades = pd.DataFrame(all_trades)
                    dl_trades["trade_date"] = dl_trades["trade_date"].astype(str)
                    buf_dl = io.BytesIO()
                    dl_trades.to_csv(buf_dl, index=False, encoding="utf-8-sig")
                    buf_dl.seek(0)
                    st.download_button("下載解析結果 CSV（唯讀環境可於本機再次上傳匯入）", data=buf_dl.getvalue(), file_name="excel_沖銷庫存_解析結果.csv", mime="text/csv", key="dl_partner_parsed")
                    if st.button("確認匯入", type="primary", key="partner_do_import"):
                        sess = get_session()
                        try:
                            created_buy = []
                            created_sell = []
                            for tr in all_trades:
                                t = Trade(user=tr["user"], stock_id=tr["stock_id"], trade_date=tr["trade_date"], side=tr["side"], price=tr["price"], quantity=tr["quantity"], is_daytrade=tr.get("is_daytrade", False), fee=tr.get("fee"), tax=tr.get("tax"), note=tr.get("note"))
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
                            st.session_state["excel_import_success_msg"] = f"已匯入 {len(all_trades)} 筆交易、{len(all_rules)} 筆自定沖銷規則。可至「交易輸入」「自定沖銷設定」「庫存損益」檢視。"
                            st.session_state["excel_import_success_time"] = time.time()
                            st.session_state["excel_import_last_file"] = (uploaded2.name, uploaded2.size)
                            st.rerun()
                        except OperationalError as e:
                            sess.rollback()
                            st.error("無法寫入資料庫（目前環境可能唯讀）。請在本機執行或設定 DATABASE_URL。")
                            st.caption("**若在雲端執行**：請在本機開啟此 App，於本頁「二、Excel 沖銷庫存資料匯入」上傳同一份 Excel 即可寫入；或於 **Secrets** 設定 `DATABASE_URL`。")
                        except IntegrityError as e:
                            sess.rollback()
                            st.error("寫入時發生主鍵或外鍵錯誤，請確認資料。")
                            st.caption(str(e))
                        except Exception as e:
                            sess.rollback()
                            st.error(f"匯入失敗：{e}")
                        finally:
                            sess.close()
                    # 匯入成功訊息：僅顯示在確認匯入下方，10 秒後自動清除
                    if st.session_state.get("excel_import_success_msg"):
                        t0 = st.session_state.get("excel_import_success_time", 0)
                        if time.time() - t0 < 10:
                            st.success(st.session_state["excel_import_success_msg"])
                        else:
                            st.session_state.pop("excel_import_success_msg", None)
                            st.session_state.pop("excel_import_success_time", None)
        finally:
            if path and os.path.isfile(path):
                try:
                    os.remove(path)
                except Exception:
                    pass
