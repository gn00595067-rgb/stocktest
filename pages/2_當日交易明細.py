# -*- coding: utf-8 -*-
"""當日交易明細：以「日期」為主體顯示全部原始買賣與交割加總。"""
import io
import streamlit as st
import pandas as pd
import sys
import os
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.stock_list_loader import ensure_google_sheet_loaded
ensure_google_sheet_loaded()
from sqlalchemy.exc import OperationalError
from db.database import get_session
from db.models import Trade, StockMaster, CustomMatchRule
from services.pnl_engine import Lot, compute_matches, net_pnl_for_match

st.set_page_config(page_title="當日交易明細", layout="wide")
st.title("當日交易明細")
st.caption("以「每日」為主體列出當日全部原始買賣，並加總交割應收/應付，協助核對交割金額。")
st.caption("計算口徑：當日列表之賣出損益已扣買進手續費、賣出手續費、證交稅；交割加總則依當日收付金額（含手續費/證交稅）估算。")

try:
    sess = get_session()
    trades = sess.query(Trade).all()
    masters = {m.stock_id: m for m in sess.query(StockMaster).all()}
    custom_rules = [(r.sell_trade_id, r.buy_trade_id, r.matched_qty) for r in sess.query(CustomMatchRule).all()]
    sess.close()
except OperationalError:
    st.warning("資料庫無法使用（雲端部署請在 Secrets 設定 USE_GOOGLE_SHEET、GOOGLE_SHEET_ID、GOOGLE_SHEET_CREDENTIALS_B64）。")
    st.stop()
except Exception:
    st.warning("無法載入交易資料。")
    st.stop()

if not trades:
    st.info("尚無交易資料。")
    st.stop()

stock_ids = sorted(set(str(t.stock_id).strip() for t in trades if getattr(t, "stock_id", None)))
users = sorted(set((getattr(t, "user", None) or "").strip() for t in trades if getattr(t, "user", None)))
dates = sorted({t.trade_date for t in trades if getattr(t, "trade_date", None)})

def _stock_label(sid: str) -> str:
    sid = str(sid).strip()
    m = masters.get(sid)
    name = (getattr(m, "name", None) or "").strip() if m else ""
    return f"{sid} {name}".strip() if name else sid

stock_opts = ["全部"] + [_stock_label(sid) for sid in stock_ids]
user_opts = ["全部"] + users

def _label_to_sid(lbl: str):
    if lbl == "全部":
        return None
    return str(lbl).split(" ")[0].strip()

def _style_signed(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    try:
        x = float(v)
        if x > 0:
            return "color: #c00; font-weight: 600;"
        if x < 0:
            return "color: #0d7a0d; font-weight: 600;"
    except Exception:
        return ""
    return ""


def _compute_pnl_by_sell_ids(all_trades, sell_ids: set[int]) -> dict:
    trade_by_id = {t.id: t for t in all_trades}
    pnl_by_sell_id = defaultdict(float)
    keyed = defaultdict(list)
    for t in all_trades:
        k = (str(getattr(t, "stock_id", "")).strip(), (getattr(t, "user", "") or "").strip())
        keyed[k].append(t)
    for (_sid, _u), ts in keyed.items():
        buys = [
            Lot(t.id, int(t.quantity or 0), float(t.price or 0), str(t.trade_date))
            for t in ts if (getattr(t, "side", "") or "").strip().upper() in ("BUY", "配股")
        ]
        sells = [
            Lot(t.id, int(t.quantity or 0), float(t.price or 0), str(t.trade_date))
            for t in ts if (getattr(t, "side", "") or "").strip().upper() == "SELL"
        ]
        if not buys or not sells:
            continue
        matches = compute_matches(buys, sells, "CUSTOM_PLUS_FIFO", custom_rules=custom_rules)
        for m in matches:
            _buy_id, sell_id, _qty, _bp, _sp, _ = m
            if sell_id in sell_ids:
                pnl_by_sell_id[sell_id] += net_pnl_for_match(m, trade_by_id)
    return pnl_by_sell_id


def _build_df(scope_trades):
    sell_ids = {t.id for t in scope_trades if (getattr(t, "side", "") or "").strip().upper() == "SELL"}
    pnl_by_sell_id = _compute_pnl_by_sell_ids(trades, sell_ids)
    rows = []
    for t in sorted(scope_trades, key=lambda x: (x.trade_date, str(x.stock_id), (x.user or ""), x.id)):
        side = (getattr(t, "side", "") or "").upper()
        qty = int(getattr(t, "quantity", 0) or 0)
        price = float(getattr(t, "price", 0) or 0)
        fee = float(getattr(t, "fee", 0) or 0)
        tax = float(getattr(t, "tax", 0) or 0)
        gross = qty * price
        if side == "BUY":
            settle_in = 0.0
            settle_out = gross + fee
        else:
            settle_in = gross - fee - tax
            settle_out = 0.0
        rows.append({
            "日期": str(getattr(t, "trade_date", "")),
            "買賣人": getattr(t, "user", "") or "",
            "股票": _stock_label(getattr(t, "stock_id", "")),
            "買/賣": side,
            "股數": qty,
            "價格": price,
            "成交金額": gross,
            "手續費": fee,
            "證交稅": tax,
            "交割應收": settle_in,
            "交割應付": settle_out,
            "單筆損益": round(float(pnl_by_sell_id.get(t.id, 0)), 0) if side == "SELL" else None,
            "當沖": bool(getattr(t, "is_daytrade", False)),
            "備註": (getattr(t, "note", "") or "")[:40],
        })
    df = pd.DataFrame(rows)
    df["_rank"] = (df["交割應收"] + df["交割應付"]).apply(lambda v: 0 if v else 1)
    df = df.sort_values(by=["日期", "_rank", "股票", "買賣人", "買/賣"]).drop(columns=["_rank"])
    cum = 0.0
    cum_vals = []
    for _, r in df.iterrows():
        v = r.get("單筆損益")
        if pd.notna(v):
            cum += float(v)
            cum_vals.append(round(cum, 0))
        else:
            cum_vals.append(None)
    df["累計損益"] = cum_vals
    return df


def _render_result(df: pd.DataFrame, pnl_label: str, dl_name: str, dl_key: str):
    fmt = {
        "股數": "{:,.0f}",
        "價格": "{:,.2f}",
        "成交金額": "{:,.0f}",
        "手續費": "{:,.0f}",
        "證交稅": "{:,.0f}",
        "交割應收": "{:,.0f}",
        "交割應付": "{:,.0f}",
        "單筆損益": "{:,.0f}",
        "累計損益": "{:,.0f}",
    }
    sty = df.style.format(fmt, na_rep="—")
    for c in ["單筆損益", "累計損益"]:
        if c in df.columns:
            sty = sty.map(_style_signed, subset=[c])
    st.dataframe(sty, use_container_width=True, hide_index=True)

    total_in = float(df["交割應收"].sum())
    total_out = float(df["交割應付"].sum())
    net = total_in - total_out
    realized = float(df["單筆損益"].fillna(0).sum()) if "單筆損益" in df.columns else 0.0

    st.markdown("---")
    st.subheader("交割加總")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("交割應收（賣出淨入帳）", f"{total_in:,.0f}")
    c2.metric("交割應付（買進含手續費）", f"{total_out:,.0f}")
    c3.metric("淨交割（應收－應付）", f"{net:,.0f}")
    realized_cls = "#c62828" if realized >= 0 else "#2e7d32"
    c4.markdown(
        f"""
        <div style="font-size:0.95rem;color:#6b7280;margin-bottom:0.25rem;">{pnl_label}</div>
        <div style="font-size:1.9rem;font-weight:700;color:{realized_cls};">{realized:+,.0f}</div>
        """,
        unsafe_allow_html=True,
    )
    st.caption("提醒：此處以交易金額 ± 手續費/證交稅估算交割收付；若券商另有其他費用/利息，請以帳單為準。")

    csv_buf = io.BytesIO()
    df.to_csv(csv_buf, index=False, encoding="utf-8-sig")
    st.download_button(
        f"下載{dl_name} CSV",
        data=csv_buf.getvalue(),
        file_name=f"{dl_key}.csv",
        mime="text/csv",
        key=f"dl_{dl_key}",
    )


tab_day, tab_range = st.tabs(["當日交易明細", "區間交易明細"])

with tab_day:
    f1, f2, f3 = st.columns([1.2, 1.2, 1.2])
    with f1:
        day = st.date_input("日期", value=dates[-1], key="daily_page_date")
    with f2:
        stock_label_day = st.selectbox("股票", options=stock_opts, key="daily_page_stock")
    with f3:
        user_day = st.selectbox("買賣人", options=user_opts, key="daily_page_user")
    sid_filter_day = _label_to_sid(stock_label_day)
    daily = [t for t in trades if getattr(t, "trade_date", None) == day]
    if user_day != "全部":
        daily = [t for t in daily if (getattr(t, "user", None) or "").strip() == user_day]
    if sid_filter_day:
        daily = [t for t in daily if str(getattr(t, "stock_id", "")).strip() == sid_filter_day]
    st.caption(f"當日筆數：**{len(daily)}**")
    if not daily:
        st.info("此日期下沒有符合篩選條件的交易。")
    else:
        _render_result(_build_df(daily), "當日已實現損益", f"當日交易明細_{day}", f"daily_trades_{day}")

with tab_range:
    r1, r2, r3, r4 = st.columns([1.1, 1.1, 1.2, 1.2])
    with r1:
        start_day = st.date_input("開始日期", value=dates[-1], key="range_page_start")
    with r2:
        end_day = st.date_input("結束日期", value=dates[-1], key="range_page_end")
    with r3:
        stock_label_range = st.selectbox("股票", options=stock_opts, key="range_page_stock")
    with r4:
        user_range = st.selectbox("買賣人", options=user_opts, key="range_page_user")
    if start_day > end_day:
        start_day, end_day = end_day, start_day
    sid_filter_range = _label_to_sid(stock_label_range)
    ranged = [t for t in trades if start_day <= getattr(t, "trade_date", start_day) <= end_day]
    if user_range != "全部":
        ranged = [t for t in ranged if (getattr(t, "user", None) or "").strip() == user_range]
    if sid_filter_range:
        ranged = [t for t in ranged if str(getattr(t, "stock_id", "")).strip() == sid_filter_range]
    st.caption(f"區間筆數：**{len(ranged)}**（{start_day} ～ {end_day}）")
    if not ranged:
        st.info("此區間下沒有符合篩選條件的交易。")
    else:
        _render_result(_build_df(ranged), "區間已實現損益", f"區間交易明細_{start_day}_{end_day}", f"range_trades_{start_day}_{end_day}")

