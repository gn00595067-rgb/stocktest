# -*- coding: utf-8 -*-
"""交易輸入（仿奇摩）：持倉清單內直接 Key in 買賣，即時損益與沖銷配對"""
import os
import sys
import time
from collections import defaultdict
from datetime import date, timedelta

import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.stock_list_loader import ensure_google_sheet_loaded

ensure_google_sheet_loaded()

try:
    if hasattr(st, "secrets") and st.secrets.get("FINMIND_TOKEN"):
        os.environ.setdefault("FINMIND_TOKEN", str(st.secrets["FINMIND_TOKEN"]).strip())
except Exception:
    pass

from sqlalchemy.exc import OperationalError

from db.database import get_session
from db.models import Trade, StockMaster, CustomMatchRule
from services.price_service import (
    get_quote_cached,
    get_quotes_cached,
    fetch_stock_list_cached,
    clear_quote_cache,
    get_finmind_debug,
)
from services.auth_service import (
    ensure_bootstrap_admin,
    login_guard,
    render_auth_sidebar,
    is_admin,
    get_allowed_traders,
    can_access_trader,
    filter_trades_by_permission,
)
from services.trade_fees import fees_for_trade, get_fee_tax_rates
from services.prefs import resolve_default_trader
from services.trader_service import (
    list_trader_names,
    add_trader,
    delete_trader,
    ensure_traders_seeded,
)
from services.trade_entry_service import (
    build_holdings_summary,
    get_open_buy_lots,
    fifo_match_plan,
    combined_match_plan,
    filter_lots_by_time,
    filter_and_sort_lots,
    realized_pnl_for_sell_plan,
    compute_realized_in_range,
    safe_int_qty,
    estimate_match_row_net_pnl,
)
from services.position_cost import compute_position_and_cost_by_stock

st.set_page_config(page_title="交易輸入", layout="wide")
from services.mobile_ui import inject_mobile_css
inject_mobile_css()

_POLICY_OPTIONS = {
    "CUSTOM_PLUS_FIFO": "自定沖銷 + 未定部分先進先出",
    "CUSTOM_ONLY": "僅自定沖銷",
    "CUSTOM_PLUS_CONSERVATIVE": "自定 + 保守（高買價先出）",
    "CUSTOM_PLUS_OPTIMISTIC": "自定 + 樂觀（低買價先出）",
}


def _init_session_defaults():
    st.session_state.setdefault("fee_rate", 0.00035625)  # 公定 0.1425% × 2.5 折
    st.session_state.setdefault("tax_rate", 0.003)
    st.session_state.setdefault("te_date", date.today())
    st.session_state.setdefault("te_period_days", 3)
    st.session_state.setdefault("te_policy", "CUSTOM_PLUS_FIFO")
    st.session_state.setdefault("te_auto_fifo", True)
    st.session_state.setdefault("te_autorefresh", True)
    st.session_state.setdefault("te_ar_interval", 20)


def _fmt_pnl(v):
    if v is None:
        return "—"
    try:
        x = float(v)
        sign = "+" if x >= 0 else ""
        return f"{sign}{x:,.0f}"
    except Exception:
        return str(v)


def _pnl_delta_color(v):
    if v is None:
        return "off"
    return "normal" if float(v) >= 0 else "inverse"


def _added_stocks_key(trader: str) -> str:
    """每位買賣人各自的『手動加入股票』清單 key；避免切換買賣人時看到別人加的股。"""
    return f"te_added_stocks::{(trader or '').strip()}"


# 沖銷表欄寬（表頭與資料列必須一致）；末欄含股數輸入＋補滿/清0/只此快捷鈕，較寬
_MATCH_COL_WIDTHS = [0.5, 0.86, 0.56, 0.6, 0.78, 0.62, 1.6]


def _inject_trade_entry_css():
    st.markdown(
        """
        <style>
        .te-match-box {
            border: 1px solid #dbe3ef;
            border-radius: 10px;
            padding: 0.75rem 0.65rem 0.55rem;
            background: linear-gradient(180deg, #f8fafc 0%, #fff 100%);
            margin: 0.35rem 0 0.85rem;
        }
        .te-match-th {
            font-size: 0.72rem;
            color: #64748b;
            font-weight: 600;
            padding: 0 0 0.45rem 0;
            margin: 0;
            border-bottom: 1px solid #e2e8f0;
            line-height: 1.3;
            text-align: left;
        }
        .te-match-td {
            font-size: 0.88rem;
            line-height: 1.35;
            padding-top: 0.15rem;
            min-height: 2.1rem;
        }
        .te-match-box div[data-testid="column"] {
            padding-left: 0.2rem !important;
            padding-right: 0.2rem !important;
        }
        .te-match-box div[data-testid="stNumberInput"] {
            margin-top: -0.15rem;
        }
        .te-match-box div[data-testid="stNumberInput"] label {
            display: none;
        }
        .te-match-box div[data-testid="stNumberInput"] > div {
            padding-top: 0.1rem;
        }
        /* 持股表格：表頭與資料列同欄寬、數字靠右對齊 */
        .te-hold-th {
            font-size: 0.74rem;
            color: #64748b;
            font-weight: 600;
            padding: 0.1rem 0.15rem 0.4rem;
            border-bottom: 1px solid #e2e8f0;
            white-space: nowrap;
        }
        .te-hold-td {
            font-size: 0.9rem;
            min-height: 2.5rem;
            display: flex;
            align-items: center;
            padding: 0.1rem 0.15rem;
            white-space: nowrap;
            /* 不裁切：靠右對齊的數字若放不下，ellipsis 會從左邊吃掉最高位
               （例：4275 被切成 275），寧可讓它完整顯示也不能少一位數。 */
            overflow: visible;
        }
        /* 平板／窄螢幕：把持股表數字字級與欄距略縮，讓 4～5 位數股價塞得下，
           避免欄位被擠到需要裁切。 */
        @media (max-width: 1200px) {
            .te-hold-td { font-size: 0.82rem; padding: 0.1rem 0.08rem; }
            .te-hold-th { font-size: 0.68rem; padding-left: 0.08rem; padding-right: 0.08rem; }
        }
        @media (max-width: 560px) {
            .te-hold-td { font-size: 0.78rem; }
        }
        /* KPI 摘要（6 欄）：縮小數字字級、單行不換行、縮小欄距，避免大數字被擠成「23,631,...」 */
        div[data-testid="stMetric"] {
            padding-left: 0.1rem;
            padding-right: 0.1rem;
        }
        div[data-testid="stMetricValue"] {
            font-size: 1.15rem;
            font-weight: 700;
            white-space: nowrap;
            overflow: visible;
            line-height: 1.25;
        }
        div[data-testid="stMetricValue"] > div {
            white-space: nowrap;
            overflow: visible;
        }
        div[data-testid="stMetricLabel"] {
            font-size: 0.78rem;
        }
        div[data-testid="stMetricLabel"] p {
            font-size: 0.78rem;
            white-space: nowrap;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _html_pnl_amount(amount: float, decimals: int = 0) -> str:
    """損益金額著色（台股：賺紅、賠綠）。"""
    if amount > 0:
        color = "#c62828"
    elif amount < 0:
        color = "#2e7d32"
    else:
        color = "#64748b"
    if decimals == 0:
        text = f"{amount:+,.0f}"
    else:
        text = f"{amount:+,.{decimals}f}"
    return f'<span style="color:{color};font-weight:600">{text}</span>'


def _render_kpi(col, label: str, value, help_text: str = "", pnl: bool = True) -> None:
    """KPI 卡：損益數字上色（賠=紅、賺=藍、中性不上色）並保留 +/− 號；
    非損益（成本/市值 pnl=False）不上色、不加正負號。"""
    v = float(value or 0)
    if pnl and v > 0:
        color, text = "#1565c0", f"+{v:,.0f}"        # 賺 → 藍
    elif pnl and v < 0:
        color, text = "#c62828", f"-{abs(v):,.0f}"   # 賠 → 紅
    elif pnl:
        color, text = "#334155", "0"                 # 中性（0）→ 不上色
    else:
        color, text = "#334155", f"{v:,.0f}"         # 非損益 → 不上色、無正負號
    col.markdown(
        f'<div title="{help_text}">'
        f'<div style="font-size:0.82rem;color:#64748b;white-space:nowrap">{label}</div>'
        f'<div style="font-size:1.2rem;font-weight:700;color:{color};white-space:nowrap;line-height:1.3">{text}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )


def _html_price_diff(sell_price: float, buy_price: float) -> str:
    """單股價差（賣價 − 買價）；台股習慣：賺紅、賠綠。"""
    diff = round(float(sell_price) - float(buy_price), 2)
    if diff > 0:
        color = "#c62828"
    elif diff < 0:
        color = "#2e7d32"
    else:
        color = "#64748b"
    bp = float(buy_price)
    pct = (diff / bp * 100) if bp else 0.0
    return (
        f'<span style="color:{color};font-weight:600">{diff:+.2f}</span>'
        f'<span style="color:{color};font-size:0.82em"> ({pct:+.2f}%)</span>'
    )


# 持股表格欄寬與對齊（表頭與資料列必須一致）
_HOLD_COL_WIDTHS = [1.5, 0.7, 1.0, 0.9, 0.82, 1.0, 1.1, 1.15, 1.1, 0.7]
_HOLD_LABELS = ["股名", "代號", "現價", "漲跌", "股數", "成交均價", "持股成本均價", "總成本", "未實現", ""]
_HOLD_JUSTIFY = ["flex-start", "flex-start", "flex-end", "flex-end", "flex-end", "flex-end", "flex-end", "flex-end", "flex-end", "center"]
_HOLD_TEXT_ALIGN = ["left", "left", "right", "right", "right", "right", "right", "right", "right", "center"]


def _quote_color(change) -> str:
    """台股：漲紅、跌綠、平灰。"""
    x = float(change or 0)
    if x > 0:
        return "#c62828"
    if x < 0:
        return "#2e7d32"
    return "#64748b"


def _html_price_colored(price, change) -> str:
    """現價依漲跌著色（奇摩式）。"""
    color = _quote_color(change)
    return f'<span style="color:{color};font-weight:700">{float(price):.2f}</span>'


def _html_change_arrow(change, pct) -> str:
    """▲/▼ 漲跌值 (百分比)，紅漲綠跌，一目了然。"""
    x = float(change or 0)
    color = _quote_color(x)
    arrow = "▲" if x > 0 else ("▼" if x < 0 else "—")
    return (
        f'<span style="color:{color};font-weight:600">{arrow} {abs(x):.2f}</span>'
        f'<span style="color:{color};font-size:0.85em"> ({abs(float(pct or 0)):.2f}%)</span>'
    )


def _render_holdings_header():
    cols = st.columns(_HOLD_COL_WIDTHS)
    for c, lbl, ta in zip(cols, _HOLD_LABELS, _HOLD_TEXT_ALIGN):
        c.markdown(f'<div class="te-hold-th" style="text-align:{ta}">{lbl}</div>', unsafe_allow_html=True)


def _render_holding_row(row: dict, sid: str, open_now: bool):
    cols = st.columns(_HOLD_COL_WIDTHS)
    avg_price = f"{row.get('avg_price', 0):.2f}" if row["qty"] else "—"
    avg = f"{row['avg_cost']:.2f}" if row["qty"] else "—"
    total_cost = f'{row.get("total_cost", 0):,.0f}' if row["qty"] else "—"
    change = row.get("change", 0)
    values = [
        f'<b>{row["name"]}</b>',
        f'<code>{sid}</code>',
        _html_price_colored(row["price"], change),
        _html_change_arrow(change, row["change_pct"]),
        f'{row["qty"]:,}',
        avg_price,
        avg,
        total_cost,
        _html_pnl_amount(row["unrealized"]),
    ]
    for c, v, jc in zip(cols[:9], values, _HOLD_JUSTIFY[:9]):
        c.markdown(
            f'<div class="te-hold-td" style="justify-content:{jc}">{v}</div>',
            unsafe_allow_html=True,
        )
    with cols[9]:
        if st.button(
            "收合" if open_now else "輸入",
            key=f"te_toggle_{sid}",
            use_container_width=True,
        ):
            new_state = not open_now
            if new_state:
                # 手風琴：展開此檔時，自動收合其他所有檔，畫面不會越拉越長
                for k in list(st.session_state.keys()):
                    if str(k).startswith("te_open_"):
                        st.session_state[k] = False
            st.session_state[f"te_open_{sid}"] = new_state
            st.rerun()


def _coerce_date(v):
    """把 data_editor 回傳的日期（date / Timestamp / 字串）統一轉成 datetime.date。"""
    if v is None:
        return None
    try:
        if isinstance(v, float) and pd.isna(v):
            return None
    except Exception:
        pass
    if isinstance(v, date) and not hasattr(v, "hour"):
        return v
    try:
        return pd.to_datetime(v).date()
    except Exception:
        return None


def _save_tx_edits(sid: str, orig_by_id: dict, edited_df, trader: str, is_etf: bool) -> None:
    """把 data_editor 的修改（改值／新增列／刪除列）寫回資料庫。

    費用處理：
    - 既有列：一律採用表格內的手續費/證交稅（不自動重算），使用者手動修正不會被洗掉。
    - 新增列（此表加列）：比照初次輸入自動帶入費率計算，方便直接補一筆。
    """
    sess = get_session()
    try:
        seen_ids = set()
        for _, r in edited_df.iterrows():
            rid = r.get("id")
            has_id = rid is not None and not (isinstance(rid, float) and pd.isna(rid))
            side = "BUY" if str(r.get("買/賣")) == "買入" else "SELL"
            qty = safe_int_qty(r.get("交易股數"))
            try:
                price = float(r.get("交易股價") or 0)
            except (TypeError, ValueError):
                price = 0.0
            tdate = _coerce_date(r.get("交易日期"))
            is_dt = bool(r.get("當沖"))
            # 買賣人：可在表格改；空白（如新列）沿用目前選定的買賣人
            trader_row = (str(r.get("買賣人")).strip() if r.get("買賣人") is not None
                          and not (isinstance(r.get("買賣人"), float) and pd.isna(r.get("買賣人"))) else "")
            trader_row = trader_row or trader
            if qty <= 0 or price <= 0 or tdate is None:
                continue  # 略過尚未填完整的空白／新列
            if not has_id:
                # 新增列（此表加列）：比照初次輸入自動帶入費率
                fee, tax = fees_for_trade(side, price, qty, is_etf=is_etf, is_daytrade=is_dt)
                tax = tax if side == "SELL" else 0.0
            else:
                # 既有列：採用表格內手動值，不自動重算（避免手動修正被洗掉）
                try:
                    fee = float(r.get("手續費") or 0)
                except (TypeError, ValueError):
                    fee = 0.0
                try:
                    tax = float(r.get("證交稅") or 0) if side == "SELL" else 0.0
                except (TypeError, ValueError):
                    tax = 0.0

            if not has_id:
                # 新增列
                if not can_access_trader(trader_row):
                    continue
                sess.add(Trade(
                    user=trader_row, stock_id=sid, trade_date=tdate, side=side,
                    price=price, quantity=qty, is_daytrade=is_dt, fee=fee, tax=tax,
                ))
                continue

            rid = int(rid)
            seen_ids.add(rid)
            t = sess.query(Trade).filter(Trade.id == rid).first()
            if not t or not can_access_trader(t.user):
                continue
            # 若要改成別的買賣人，需有目標買賣人的權限；否則保留原買賣人
            if trader_row != (t.user or "") and not can_access_trader(trader_row):
                trader_row = t.user or trader
            # 股數／買賣方向／買賣人改變 → 舊沖銷配對已不成立，清掉讓損益重新以 policy 計算
            core_changed = (
                (int(t.quantity or 0) != qty)
                or (str(t.side).upper() != side)
                or ((t.user or "") != trader_row)
            )
            t.user = trader_row
            t.trade_date = tdate
            t.side = side
            t.price = price
            t.quantity = qty
            t.is_daytrade = is_dt
            t.fee = fee
            t.tax = tax
            if core_changed:
                sess.query(CustomMatchRule).filter(CustomMatchRule.sell_trade_id == rid).delete()
                sess.query(CustomMatchRule).filter(CustomMatchRule.buy_trade_id == rid).delete()

        # 被刪掉的列（原本有、編輯後不見）
        for oid, ot in orig_by_id.items():
            if oid not in seen_ids:
                if not can_access_trader(ot.user):
                    continue
                sess.query(CustomMatchRule).filter(CustomMatchRule.sell_trade_id == oid).delete()
                sess.query(CustomMatchRule).filter(CustomMatchRule.buy_trade_id == oid).delete()
                sess.query(Trade).filter(Trade.id == oid).delete()

        sess.commit()
        st.success("已儲存修改")
        st.rerun()
    except Exception as e:
        sess.rollback()
        st.error(str(e))
    finally:
        sess.close()


def _render_stock_tx_list(sid: str, stock_ts: list, cur_price: float, trader: str, is_etf: bool) -> None:
    """奇摩股市式可編輯逐筆交易表：買賣人、買/賣、股數、股價、手續費、當沖可直接改；按鈕整批儲存。"""
    orig_by_id = {int(t.id): t for t in stock_ts}
    # 可選買賣人清單（管理者看全部，一般帳號看有權限者），並含目前表內已出現的人
    trader_opts = (list_trader_names() if is_admin() else get_allowed_traders()) or []
    trader_opts = sorted(set(trader_opts) | {trader} | {(t.user or "").strip() for t in stock_ts if (t.user or "").strip()})

    # 注意：此表只放資料庫的穩定欄位，不放隨即時股價變動的欄（例如市值）。
    # 因為 st.data_editor 只要輸入表內容和上一次不同，就會把未儲存的編輯重置掉，
    # 會造成「改了日期/買賣人卻跳回」。市值請看上方持股列。
    df = pd.DataFrame([
        {
            "id": int(t.id),
            "買賣人": (t.user or "").strip() or trader,
            "交易日期": _coerce_date(str(t.trade_date)[:10]),
            "買/賣": "買入" if str(t.side).upper() == "BUY" else "賣出",
            "交易股數": int(t.quantity or 0),
            "交易股價": float(t.price or 0),
            "手續費": float(getattr(t, "fee", 0) or 0),
            "證交稅": float(getattr(t, "tax", 0) or 0),
            "當沖": bool(getattr(t, "is_daytrade", False)),
        }
        for t in stock_ts
    ])
    # 全部展開：依列數把 data_editor 高度撐到剛好容納所有列，取消內部捲動
    # Streamlit 每列約 35px、表頭約 35px、dynamic 會多一列可新增列 + 邊框緩衝
    _row_h = 35
    _full_h = _row_h * (len(df) + 2) + 3
    edited = st.data_editor(
        df,
        key=f"te_txedit_{sid}",
        hide_index=True,
        use_container_width=True,
        height=_full_h,
        num_rows="dynamic",
        column_config={
            "id": st.column_config.NumberColumn("ID", disabled=True, width="small"),
            "買賣人": st.column_config.SelectboxColumn(
                "買賣人", options=trader_opts, required=False, width="small",
                help="可改指定這筆交易屬於哪位買賣人；改成他人需有該買賣人的權限。新列留空＝目前選定的買賣人。",
            ),
            "交易日期": st.column_config.DateColumn("交易日期", format="YYYY/MM/DD"),
            "買/賣": st.column_config.SelectboxColumn("買/賣", options=["買入", "賣出"], required=True, width="small"),
            "交易股數": st.column_config.NumberColumn("交易股數", min_value=0, step=1000, format="localized"),
            "交易股價": st.column_config.NumberColumn("交易股價", min_value=0.0, step=0.05, format="accounting"),
            "手續費": st.column_config.NumberColumn(
                "手續費 ✏️可改",
                format="accounting",
                help="可直接填券商實收金額（例如折讓後手續費）。儲存時不會自動重算，你改的數字會保留。",
            ),
            "證交稅": st.column_config.NumberColumn(
                "證交稅 ✏️可改",
                format="accounting",
                help="可直接填實際證交稅。賣出才收；買進為 0。儲存時不會自動重算，你改的數字會保留。",
            ),
            "當沖": st.column_config.CheckboxColumn(
                "當沖",
                width="small",
                help="標記此筆為當日沖銷（僅作記號，不會改動你已填的手續費／證交稅）。",
            ),
        },
    )
    st.caption(
        "✏️ 手續費／證交稅可直接編輯，**儲存時不會自動重算**，你改的數字會保留。"
        "（自動帶入費率只在最上方『送出此筆交易』新增時計算。）刪列＝刪交易、加列＝新增交易。"
    )
    st.caption("💡 小提醒：改完最後一格後，先按 Enter 或點一下表格外空白處讓該格生效，再按「儲存修改」，才不會需要按兩次。儲存後此表與下方「當日全部成交」會一起更新。")
    if st.button("💾 儲存修改", key=f"te_txsave_{sid}", type="primary"):
        _save_tx_edits(sid, orig_by_id, edited, trader, is_etf)


def _render_match_table_header():
    labels = ["買進ID", "買進日", "買價", "價差", "淨損益", "可沖銷", "本次沖銷"]
    cols = st.columns(_MATCH_COL_WIDTHS)
    for col, lbl in zip(cols, labels):
        col.markdown(f'<div class="te-match-th">{lbl}</div>', unsafe_allow_html=True)


def _match_widget_key(stock_id: str, buy_id: int) -> str:
    return f"te_mq_{stock_id}_{buy_id}"


def _normalize_match_state(match_plan_key: str) -> dict:
    raw = st.session_state.get(match_plan_key, {})
    if isinstance(raw, list):
        raw = {int(b): int(q) for b, q in raw}
    elif not isinstance(raw, dict):
        raw = {}
    st.session_state[match_plan_key] = raw
    return raw


def _apply_match_plan(stock_id: str, match_plan_key: str, plan: list, open_lots: list) -> None:
    plan_map = {int(b): int(q) for b, q in plan}
    st.session_state[match_plan_key] = plan_map
    for lot in open_lots:
        bid = int(lot["trade_id"])
        st.session_state[_match_widget_key(stock_id, bid)] = int(plan_map.get(bid, 0))


def _read_match_plan(stock_id: str, open_lots: list) -> list:
    plan = []
    for lot in open_lots:
        bid = int(lot["trade_id"])
        max_q = int(lot["remaining_qty"])
        q = min(safe_int_qty(st.session_state.get(_match_widget_key(stock_id, bid), 0)), max_q)
        if q > 0:
            plan.append((bid, q))
    return plan


def _row_op_key(stock_id: str) -> str:
    return f"te_rowop_{stock_id}"


def _apply_pending_row_op(stock_id: str, open_lots: list, sell_qty: int) -> None:
    """處理沖銷列快捷按鈕（補滿／清0／只配此）。

    必須在任何沖銷 number_input 建立「之前」呼叫：直接改寫該列 widget 的
    session_state 值，避開 Streamlit「widget 已建立不可修改」限制。
    每次 rerun 只處理一個操作（按鈕按下時排入、rerun 後於此生效）。
    """
    pending = st.session_state.pop(_row_op_key(stock_id), None)
    if not pending:
        return
    op, bid = pending
    bid = int(bid)
    lot = next((l for l in open_lots if int(l["trade_id"]) == bid), None)
    if not lot:
        return
    max_q = int(lot["remaining_qty"])
    wk = _match_widget_key(stock_id, bid)
    if op == "clear":
        st.session_state[wk] = 0
    elif op == "fill":
        # 補到湊齊賣出股數：其他各列目前已配的合計之外，還缺多少就補多少（上限為此批可沖銷量）
        others = 0
        for l in open_lots:
            oid = int(l["trade_id"])
            if oid == bid:
                continue
            others += min(
                safe_int_qty(st.session_state.get(_match_widget_key(stock_id, oid), 0)),
                int(l["remaining_qty"]),
            )
        need = max(0, int(sell_qty) - others)
        st.session_state[wk] = min(max_q, need)
    elif op == "only":
        # 清掉其他所有列，整筆只用這批配（上限為此批可沖銷量）
        for l in open_lots:
            st.session_state[_match_widget_key(stock_id, int(l["trade_id"]))] = 0
        st.session_state[wk] = min(max_q, int(sell_qty))


def _render_match_panel(
    stock_id: str,
    match_plan_key: str,
    open_lots: list,
    sell_qty: int,
    sell_price: float,
    trades: list,
    sell_fee_est: float,
    sell_tax_est: float,
) -> list:
    """穩定沖銷配對 UI（number_input + session state，表頭與資料列同欄寬）。"""
    match_dict = _normalize_match_state(match_plan_key)
    plan_sum = 0
    total_gross = 0.0
    total_net = 0.0
    sell_price = float(sell_price)
    sell_qty_i = max(1, int(sell_qty))
    trade_by_id = {t.id: t for t in trades}

    # 快捷按鈕（補滿／清0／只配此）的延後處理：務必在下方 number_input 建立前
    _apply_pending_row_op(stock_id, open_lots, sell_qty_i)

    with st.container(border=True):
        st.markdown('<div class="te-match-box">', unsafe_allow_html=True)
        st.caption(
            f"賣出成交價 **{sell_price:.2f}**　｜　**價差** = 賣價 − 買價　｜　"
            f"**淨損益** = 毛損益 − 買進手續費 − 賣出手續費 − 證交稅（依表單與歷史成交估算）"
        )
        _render_match_table_header()

        for lot in open_lots:
            bid = int(lot["trade_id"])
            max_q = int(lot["remaining_qty"])
            buy_price = float(lot["price"])
            buy_trade = trade_by_id.get(bid)
            wkey = _match_widget_key(stock_id, bid)
            if wkey not in st.session_state:
                st.session_state[wkey] = int(match_dict.get(bid, 0))

            c0, c1, c2, c3, c4, c5, c6 = st.columns(_MATCH_COL_WIDTHS)
            diff_html = _html_price_diff(sell_price, buy_price)
            c0.markdown(f'<div class="te-match-td"><code>{bid}</code></div>', unsafe_allow_html=True)
            c1.markdown(f'<div class="te-match-td">{str(lot["date"])[:10]}</div>', unsafe_allow_html=True)
            c2.markdown(f'<div class="te-match-td">{buy_price:.2f}</div>', unsafe_allow_html=True)
            c3.markdown(f'<div class="te-match-td">{diff_html}</div>', unsafe_allow_html=True)

            qty = c6.number_input(
                "本次沖銷",
                min_value=0,
                max_value=max_q,
                step=1,
                key=wkey,
                label_visibility="collapsed",
            )
            bf, bc, bo = c6.columns(3)
            if bf.button("補滿", key=f"{wkey}_fill", use_container_width=True,
                         help="把這批補到湊齊賣出股數（自動算還缺多少）"):
                st.session_state[_row_op_key(stock_id)] = ("fill", bid)
                st.rerun()
            if bc.button("清0", key=f"{wkey}_clr", use_container_width=True,
                         help="取消這批配對（歸零）"):
                st.session_state[_row_op_key(stock_id)] = ("clear", bid)
                st.rerun()
            if bo.button("只此", key=f"{wkey}_only", use_container_width=True,
                         help="清掉其他批，整筆只用這批配"):
                st.session_state[_row_op_key(stock_id)] = ("only", bid)
                st.rerun()
            q = safe_int_qty(qty)
            plan_sum += q

            if q > 0:
                row_gross, row_net = estimate_match_row_net_pnl(
                    sell_price,
                    buy_price,
                    q,
                    buy_trade,
                    sell_fee_est,
                    sell_tax_est,
                    sell_qty_i,
                )
                total_gross += row_gross
                total_net += row_net
                c4.markdown(f'<div class="te-match-td">{_html_pnl_amount(row_net)}</div>', unsafe_allow_html=True)
                c4.caption(f"毛 {_fmt_pnl(row_gross)}")
                c3.caption(f"×{q:,} 股")
            else:
                c4.markdown('<div class="te-match-td" style="color:#94a3b8">—</div>', unsafe_allow_html=True)

            c5.markdown(f'<div class="te-match-td">{max_q:,}</div>', unsafe_allow_html=True)

        sell_qty_i = max(0, int(sell_qty))
        if sell_qty_i > 0:
            ratio = min(1.0, plan_sum / sell_qty_i)
            st.progress(ratio, text=f"配對進度 {plan_sum:,} / {sell_qty_i:,} 股")
            if plan_sum > 0:
                st.markdown(
                    f"配對合計　毛損益 **{_fmt_pnl(total_gross)}**　｜　"
                    f"淨損益（含費稅） **{_fmt_pnl(total_net)}**",
                    unsafe_allow_html=False,
                )
            if plan_sum == sell_qty_i:
                st.success("✓ 配對股數與賣出一致")
            elif plan_sum < sell_qty_i:
                st.caption(f"尚缺 **{sell_qty_i - plan_sum:,}** 股（可用各列「補滿／清0／只此」快捷鈕、上方②排序或手動填入）")
            else:
                st.warning(f"已超出賣出 **{plan_sum - sell_qty_i:,}** 股，請調整各列數字")
        st.markdown("</div>", unsafe_allow_html=True)

    plan = _read_match_plan(stock_id, open_lots)
    st.session_state[match_plan_key] = {b: q for b, q in plan}
    return plan


def _load_data():
    sess = get_session()
    trades = filter_trades_by_permission(sess.query(Trade).all())
    masters = {m.stock_id: m for m in sess.query(StockMaster).all()}
    rules = [(r.sell_trade_id, r.buy_trade_id, r.matched_qty) for r in sess.query(CustomMatchRule).all()]
    stocks = sess.query(StockMaster).all()
    sess.close()
    return trades, masters, rules, stocks


def _ensure_stock_in_master(sess, stock_id: str, masters: dict):
    if stock_id in masters:
        return
    info = {}
    try:
        for s in fetch_stock_list_cached(ttl_seconds=3600):
            if s.get("stock_id") == stock_id:
                info = s
                break
    except Exception:
        pass
    row = StockMaster(
        stock_id=stock_id,
        name=info.get("name"),
        industry_name=info.get("industry_name"),
        market=info.get("market", "TW"),
        exchange=info.get("exchange", "TWSE"),
        is_etf=info.get("is_etf", False),
    )
    sess.add(row)
    sess.commit()
    masters[stock_id] = row


def _tw_now():
    """台灣時間（UTC+8）；Streamlit Cloud 伺服器為 UTC，需自行換算。"""
    from datetime import datetime, timezone, timedelta
    return datetime.now(timezone.utc) + timedelta(hours=8)


def _tw_market_open() -> bool:
    """台股盤中（平日 08:45–13:35，含盤前試撮與收盤緩衝）。"""
    from datetime import time as dtime
    now = _tw_now()
    if now.weekday() >= 5:  # 週六日
        return False
    return dtime(8, 45) <= now.time() <= dtime(13, 35)


def _maybe_autorefresh():
    """開啟且無展開個股、且盤中時，用 fragment 每 N 秒清報價快取並整頁刷新。"""
    if not st.session_state.get("te_autorefresh"):
        return
    interval = int(st.session_state.get("te_ar_interval", 20))
    any_open = any(str(k).startswith("te_open_") and v for k, v in st.session_state.items())
    if any_open:
        st.caption("✏️ 有展開的個股（可能正在編輯），自動更新暫停；收合後恢復。")
        return
    if not _tw_market_open():
        nxt = _tw_now().strftime("%H:%M")
        st.caption(f"💤 非台股交易時段（現在 {nxt}），自動更新暫停；可按 🔄 手動更新。")
        return

    # 每次整頁執行先歸零計數；初次呼叫不重跑，之後由 fragment 計時器每 interval 秒觸發一次整頁 rerun
    st.session_state["_ar_ticks"] = 0

    @st.fragment(run_every=interval)
    def _tick():
        st.session_state["_ar_ticks"] = st.session_state.get("_ar_ticks", 0) + 1
        if st.session_state["_ar_ticks"] <= 1:
            return  # 初次註冊不動作，避免立即無限重跑
        clear_quote_cache()
        st.rerun()  # 整頁刷新，讓現價/漲跌/未實現一起跳動

    _tick()
    st.caption(f"🟢 自動更新中：每 {interval} 秒（僅盤中、且未展開個股時）。")


def _render_add_stock_expander(masters: dict, trader: str):
    with st.expander("➕ 新增股票（搜尋台股代號或名稱）", expanded=False):
        kw = st.text_input("搜尋", placeholder="2330、台積電…", key="te_stock_search")
        if kw and len(kw.strip()) >= 1:
            try:
                full_list = fetch_stock_list_cached(ttl_seconds=3600)
                k = kw.strip().upper()
                matches = [
                    s for s in full_list
                    if k in (s.get("stock_id") or "").upper() or k in (s.get("name") or "")
                ][:60]
                if matches:
                    opts = {s["stock_id"]: f"{s['stock_id']} {s.get('name', '')}" for s in matches}
                    picked = st.selectbox(
                        "選擇",
                        options=list(opts.keys()),
                        format_func=lambda x: opts.get(x, x),
                        key="te_search_pick",
                    )
                    if st.button("加入持倉列表", key="te_add_stock_btn") and picked:
                        sess = get_session()
                        _ensure_stock_in_master(sess, picked, masters)
                        sess.close()
                        added = st.session_state.setdefault(_added_stocks_key(trader), [])
                        if picked not in added:
                            added.append(picked)
                        # 手風琴：新加入並展開此檔前，先收合其他所有檔
                        for k in list(st.session_state.keys()):
                            if str(k).startswith("te_open_"):
                                st.session_state[k] = False
                        st.session_state["te_expand_stock"] = picked
                        st.success(f"已加入 {picked}，請點開下方該股輸入第一筆買進")
                        st.rerun()
                else:
                    st.caption("查無符合股票")
            except Exception as e:
                st.caption(f"搜尋失敗：{e}")


def _render_stock_trade_panel(
    row: dict,
    masters: dict,
    trades: list,
    custom_rules: list,
    policy: str,
    trader: str,
    trade_date: date,
):
    sid = row["stock_id"]
    is_etf = bool(getattr(masters.get(sid), "is_etf", False))
    open_key = f"te_open_{sid}"
    if st.session_state.get("te_expand_stock") == sid:
        st.session_state[open_key] = True
        st.session_state.pop("te_expand_stock", None)
    open_now = bool(st.session_state.get(open_key, False))

    _render_holding_row(row, sid, open_now)

    if not open_now:
        return
    with st.container(border=True):
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("市值", f"{row['market_value']:,.0f}")
        c2.metric("當日已實現", _fmt_pnl(row["realized_today"]), delta_color=_pnl_delta_color(row["realized_today"]))
        c3.metric("當月已實現", _fmt_pnl(row["realized_period"]), delta_color=_pnl_delta_color(row["realized_period"]))
        c4.metric("未實現", _fmt_pnl(row["unrealized"]), delta_color=_pnl_delta_color(row["unrealized"]))

        # ── 交易輸入（可多列）：每列一筆；賣出一律走設定的沖銷口徑（先進先出/接近均價等）自動計算 ──
        rowids_key = f"te_rowids_{sid}"
        seq_key = f"te_rowseq_{sid}"
        # 送出成功後整批重置：在建立 widget 前清掉舊列的值，回到 1 列
        if st.session_state.pop(f"te_rreset_{sid}", False):
            for _k in [k for k in list(st.session_state.keys()) if str(k).startswith(f"te_r_{sid}_")]:
                del st.session_state[_k]
            st.session_state[rowids_key] = [0]
            st.session_state[seq_key] = 1
        # 清除剛被刪除的列殘留值（在建立 widget 前）
        for _rmid in st.session_state.pop(f"te_rowdel_{sid}", []):
            for _k in [k for k in list(st.session_state.keys()) if str(k).startswith(f"te_r_{sid}_{_rmid}_")]:
                del st.session_state[_k]
        rowids = st.session_state.setdefault(rowids_key, [0])
        st.session_state.setdefault(seq_key, 1)

        _bw = [1.0, 1.1, 0.95, 0.95, 0.6, 0.85, 0.85, 1.15, 0.45]
        _hc = st.columns(_bw)
        for _c, _lab in zip(_hc, ["買/賣", "交易日期", "成交價", "股數", "當沖", "手續費", "證交稅", "備註", ""]):
            _c.caption(_lab)

        rows = []
        for _rid in rowids:
            _c0, _c1, _c2, _c3, _c4, _cfee, _ctax, _c5, _c6 = st.columns(_bw)
            _s = _c0.selectbox(
                "買/賣", ["BUY", "SELL"], key=f"te_r_{sid}_{_rid}_side",
                format_func=lambda x: "買入" if x == "BUY" else "賣出",
                label_visibility="collapsed",
            )
            _d = _c1.date_input(
                "交易日期", value=st.session_state.get(f"te_r_{sid}_{_rid}_date", trade_date),
                key=f"te_r_{sid}_{_rid}_date", label_visibility="collapsed",
            )
            # 成交價／股數預設空白（value=None），平板可直接輸入，不必先清掉 0
            _p = _c2.number_input(
                "成交價", min_value=0.0, value=None, step=0.01, format="%.2f",
                key=f"te_r_{sid}_{_rid}_price", label_visibility="collapsed",
            )
            _q = _c3.number_input(
                "股數", min_value=0, value=None, step=100,
                key=f"te_r_{sid}_{_rid}_qty", label_visibility="collapsed",
            )
            _dt = _c4.checkbox("當沖", key=f"te_r_{sid}_{_rid}_dt", label_visibility="collapsed")
            # 即時費稅：輸入當下就算好，不用等送出後到下面明細核對。
            # 用與其他欄同款的唯讀輸入框顯示，高度自動對齊（桌機/平板皆然）。
            if _p is not None and _q is not None and float(_p) > 0 and int(_q) > 0:
                _rf, _rt = fees_for_trade(_s, float(_p), int(_q), is_etf=is_etf, is_daytrade=_dt)
                _fee_txt = f"{_rf:,.0f}"
                _tax_txt = f"{_rt:,.0f}" if _s == "SELL" else "—"  # 買進不收證交稅
            else:
                _fee_txt = _tax_txt = "—"
            _fk = f"te_r_{sid}_{_rid}_feeview"
            _tk = f"te_r_{sid}_{_rid}_taxview"
            st.session_state[_fk] = _fee_txt
            st.session_state[_tk] = _tax_txt
            _cfee.text_input("手續費", key=_fk, disabled=True, label_visibility="collapsed")
            _ctax.text_input("證交稅", key=_tk, disabled=True, label_visibility="collapsed")
            _n = _c5.text_input("備註", key=f"te_r_{sid}_{_rid}_note", label_visibility="collapsed")
            with _c6:
                if len(rowids) > 1:
                    if st.button("🗑", key=f"te_r_{sid}_{_rid}_del", help="刪除這一列"):
                        st.session_state[rowids_key] = [r for r in rowids if r != _rid]
                        st.session_state.setdefault(f"te_rowdel_{sid}", []).append(_rid)
                        st.rerun()
            rows.append((_s, _d, _p, _q, _dt, _n))

        # 「多輸入一筆」：在備註下方，按一下往下再長一列
        if st.button("➕ 多輸入一筆", key=f"te_addrow_{sid}"):
            _nid = st.session_state[seq_key]
            st.session_state[seq_key] = _nid + 1
            st.session_state[rowids_key] = rowids + [_nid]
            st.rerun()

        # ── 單筆賣出：手動沖銷配對介面（一選「賣出」就顯示；預設「接近均價」，可快捷鍵改、逐批微調）──
        _match_key = f"te_match_{sid}"
        manual_plan = None
        _submit_slot = None
        _single_sell = False
        _r0 = rows[0] if len(rows) == 1 else None
        if _r0 is not None and _r0[0] == "SELL":
            _open = get_open_buy_lots(trades, sid, trader, custom_rules, policy)
            _sp = float(_r0[2]) if _r0[2] not in (None, "") else 0.0
            _sq = int(_r0[3]) if _r0[3] not in (None, "") else 0
            _sdt = bool(_r0[4])
            if not _open:
                st.markdown("**沖銷配對**")
                st.caption("此檔目前沒有可沖銷的買進庫存。")
            elif _sp <= 0 or _sq <= 0:
                st.markdown("**沖銷配對**")
                st.info("👉 請在上方輸入『成交價』與『股數』，下方就會依『接近均價』自動配好對應的買進批次（也可用快捷鍵或手動改）。")
            else:
                _single_sell = True
                # 送出後標記重置：在沖銷 number_input 建立前清掉舊值
                if st.session_state.pop(f"te_reset_match_{sid}", False):
                    for _mk in [k for k in list(st.session_state.keys()) if str(k).startswith(f"te_mq_{sid}_")]:
                        del st.session_state[_mk]
                    st.session_state.pop(_match_key, None)
                    st.session_state.pop(f"te_match_autoq_{sid}", None)
                _tkey = f"te_time_{sid}"; _skey = f"te_sortmode_{sid}"
                st.session_state.setdefault(_tkey, "all")
                st.session_state.setdefault(_skey, "nearest_avg")

                def _reapply_manual():
                    _lots = get_open_buy_lots(trades, sid, trader, custom_rules, policy)
                    _apply_match_plan(sid, _match_key, combined_match_plan(
                        _sq, _lots, st.session_state.get(_tkey, "all"),
                        st.session_state.get(_skey, "nearest_avg"), _sp), _lots)

                # 股數一改就依目前策略重配（同一股數內的手動微調會保留）
                if st.session_state.get(f"te_match_autoq_{sid}") != _sq:
                    _reapply_manual()
                    st.session_state[f"te_match_autoq_{sid}"] = _sq

                st.markdown("**沖銷配對** — 這筆賣出要沖銷哪些買進批次（預設『接近均價』，可用快捷鍵改，或在表格逐批微調；空白視為 0）")
                st.caption("① 時間範圍")
                for _col, (_k, _lab) in zip(st.columns(3), [("all", "全部"), ("3d", "近3天"), ("5d", "近5天")]):
                    if _col.button(_lab, key=f"te_tbtn_{sid}_{_k}", use_container_width=True,
                                   type="primary" if st.session_state.get(_tkey) == _k else "secondary"):
                        st.session_state[_tkey] = _k
                        _reapply_manual()
                        st.rerun()
                st.caption("② 沖銷方式（大賺＝賺多、大賠＝賠多…）")
                for _col, (_k, _lab) in zip(st.columns(6), [
                    ("nearest_avg", "⚖️接近均價"), ("fifo", "先進先出"),
                    ("profit_max", "💰賺多"), ("profit_min", "🪙賺少"),
                    ("loss_max", "🔻賠多"), ("loss_min", "🩹賠少")]):
                    if _col.button(_lab, key=f"te_sbtn_{sid}_{_k}", use_container_width=True,
                                   type="primary" if st.session_state.get(_skey) == _k else "secondary"):
                        st.session_state[_skey] = _k
                        _reapply_manual()
                        st.rerun()
                if st.button("🧹 清空配對", key=f"te_clr_{sid}"):
                    _apply_match_plan(sid, _match_key, [], _open)
                    st.rerun()
                _submit_slot = st.container()
                _fe, _te = fees_for_trade("SELL", _sp, _sq, is_etf=is_etf, is_daytrade=_sdt)
                _shown = filter_and_sort_lots(
                    filter_lots_by_time(_open, st.session_state.get(_tkey, "all")),
                    st.session_state.get(_skey, "nearest_avg"), _sp)
                manual_plan = _render_match_panel(sid, _match_key, _shown, _sq, _sp, trades, _fe, _te)

        # 送出鈕改放在清空配對下方、沖銷表上方，單筆賣出免滑到底
        _sc = _submit_slot if _submit_slot is not None else st.container()
        with _sc:
            # 有效列＝成交價與股數都有填（>0）
            valid = [(s, d, float(p), int(q), dt, n) for (s, d, p, q, dt, n) in rows
                     if p is not None and q is not None and float(p) > 0 and int(q) > 0]
            _fee_sum = 0.0
            _tax_sum = 0.0
            for (_s, _d, _p, _q, _dt, _n) in valid:
                _f, _t = fees_for_trade(_s, _p, _q, is_etf=is_etf, is_daytrade=_dt)
                _fee_sum += _f
                _tax_sum += _t
            st.caption(
                f"估算（{len(valid)} 筆有效）：手續費 **{_fee_sum:,.0f}** 元　證交稅 **{_tax_sum:,.0f}** 元"
                f"　（費率可於「主檔/設定」調整，目前 {get_fee_tax_rates()[0]:.4%} / 稅 {get_fee_tax_rates()[1]:.3%}）。"
                "　賣出沖銷：單筆可在下方『沖銷配對』選擇（預設接近均價）；多筆一律接近均價自動配。"
            )

            confirm_key = f"te_confirm_{sid}"
            _btn_label = "✅ 送出此筆交易" if len(valid) <= 1 else f"✅ 送出全部（{len(valid)} 筆）"
            # 第一步：送出 → 驗證 → 進入確認
            if st.button(_btn_label, key=f"te_submit_{sid}", type="primary", disabled=(len(valid) == 0)):
                if not can_access_trader(trader):
                    st.error("無此買賣人權限。")
                else:
                    st.session_state[confirm_key] = True
                    st.rerun()

            # 第二步：確認框
            if st.session_state.get(confirm_key):
                _lines = "；".join(
                    f"{'買入' if s == 'BUY' else '賣出'} {q:,}股 @ {p:.2f}" for (s, d, p, q, dt, n) in valid
                )
                st.warning(f"⚠️ 確認送出 {len(valid)} 筆（{sid} {row['name']}）？　{_lines}")
                _cy, _cn = st.columns(2)
                _go = _cy.button("✅ 確認送出", key=f"te_confirm_yes_{sid}", type="primary", use_container_width=True)
                if _cn.button("✖ 取消", key=f"te_confirm_no_{sid}", use_container_width=True):
                    st.session_state.pop(confirm_key, None)
                    st.rerun()
                if _go:
                    st.session_state.pop(confirm_key, None)
                    # 防連點：同一批 2 秒內重複送出視為誤觸
                    _sig = tuple((s, str(d), p, q, bool(dt), (n or "")) for (s, d, p, q, dt, n) in valid)
                    _last = st.session_state.get("te_last_submit")
                    if _last and _last[0] == _sig and (time.monotonic() - _last[1]) < 2.0:
                        st.warning("偵測到快速重複送出，已忽略這一次（避免重複記錄）。")
                    else:
                        st.session_state["te_last_submit"] = (_sig, time.monotonic())
                        sess = get_session()
                        try:
                            # 賣出以「接近均價」自動配對現有未沖銷買進批次；
                            # 多筆賣出依序扣減剩餘庫存，批次內的買進也納入可配對池
                            avail_lots = [dict(l) for l in get_open_buy_lots(trades, sid, trader, custom_rules, policy)]
                            for (s, d, p, q, dt, n) in valid:
                                _f, _t = fees_for_trade(s, p, q, is_etf=is_etf, is_daytrade=dt)
                                _tr = Trade(
                                    user=trader, stock_id=sid, trade_date=d, side=s,
                                    price=p, quantity=q, is_daytrade=dt,
                                    fee=_f, tax=(_t if s == "SELL" else 0.0), note=(n or None),
                                )
                                sess.add(_tr)
                                sess.flush()
                                if s == "BUY":
                                    avail_lots.append({
                                        "trade_id": _tr.id, "date": str(d), "price": float(p),
                                        "remaining_qty": int(q), "original_qty": int(q), "fee": float(_f),
                                    })
                                else:  # 賣出
                                    if _single_sell and manual_plan:
                                        # 單筆賣出：用使用者在沖銷面板選定/微調的配對
                                        _plan = [(int(b), int(mq)) for b, mq in manual_plan if int(mq) > 0]
                                    else:
                                        # 多筆：接近均價自動配對
                                        _plan = combined_match_plan(int(q), avail_lots, "all", "nearest_avg", float(p))
                                    _consumed = {}
                                    for _bid, _mq in _plan:
                                        sess.add(CustomMatchRule(
                                            sell_trade_id=_tr.id, buy_trade_id=int(_bid), matched_qty=int(_mq),
                                        ))
                                        _consumed[int(_bid)] = _consumed.get(int(_bid), 0) + int(_mq)
                                    for _lot in avail_lots:
                                        _c = _consumed.get(int(_lot["trade_id"]), 0)
                                        if _c:
                                            _lot["remaining_qty"] = int(_lot["remaining_qty"]) - _c
                                    avail_lots = [l for l in avail_lots if int(l["remaining_qty"]) > 0]
                            sess.commit()
                            st.session_state[f"te_rreset_{sid}"] = True
                            st.session_state[f"te_reset_match_{sid}"] = True
                            st.session_state["last_user"] = trader
                            st.success(f"已新增 {len(valid)} 筆交易（{sid} {row['name']}）。")
                            st.rerun()
                        except Exception as e:
                            sess.rollback()
                            st.error(str(e))
                        finally:
                            sess.close()

        # 該股全部交易明細（每一天、每一筆；奇摩股市式逐筆列表，可逐筆刪除）
        stock_ts = [
            t for t in trades
            if t.stock_id == sid and (t.user or "").strip() == trader.strip()
        ]
        stock_ts.sort(key=lambda t: (str(t.trade_date), t.id), reverse=True)
        st.markdown(f"**交易明細（每一天、每一筆）** — {sid} {row['name']}")
        if not stock_ts:
            st.caption("此股尚無交易，於上方輸入第一筆。")
        else:
            _render_stock_tx_list(sid, stock_ts, float(row["price"] or 0), trader, is_etf)

        # ── 從清單移除 / 刪除此股 ──
        st.divider()
        if not stock_ts:
            # 手動加入但還沒輸入任何交易的股：純從畫面清單移除，不動資料庫
            if st.button("✖ 從清單移除此股", key=f"te_remove_{sid}", use_container_width=True):
                added = st.session_state.get(_added_stocks_key(trader), [])
                if sid in added:
                    added.remove(sid)
                st.session_state.pop(f"te_open_{sid}", None)
                st.success(f"已從清單移除 {sid} {row['name']}")
                st.rerun()
        elif not can_access_trader(trader):
            st.caption("（僅本人或管理者可刪除此股全部紀錄）")
        else:
            with st.expander("🗑 刪除此股全部交易紀錄（危險操作，無法復原）", expanded=False):
                st.warning(
                    f"將刪除 **{trader}** 在 **{sid} {row['name']}** 的全部 "
                    f"**{len(stock_ts)}** 筆交易與相關沖銷配對，無法復原。"
                )
                confirm = st.checkbox("我了解，確認刪除此股全部紀錄", key=f"te_delall_confirm_{sid}")
                if st.button(
                    "🗑 確認刪除",
                    key=f"te_delall_{sid}",
                    disabled=not confirm,
                    use_container_width=True,
                ):
                    sess = get_session()
                    try:
                        ids = [int(t.id) for t in stock_ts]
                        for tid in ids:
                            sess.query(CustomMatchRule).filter(CustomMatchRule.sell_trade_id == tid).delete()
                            sess.query(CustomMatchRule).filter(CustomMatchRule.buy_trade_id == tid).delete()
                            sess.query(Trade).filter(Trade.id == tid).delete()
                        sess.commit()
                        added = st.session_state.get(_added_stocks_key(trader), [])
                        if sid in added:
                            added.remove(sid)
                        st.session_state.pop(f"te_open_{sid}", None)
                        st.success(f"已刪除 {sid} {row['name']} 全部 {len(ids)} 筆交易紀錄（含沖銷配對）")
                        st.rerun()
                    except Exception as e:
                        sess.rollback()
                        st.error(str(e))
                    finally:
                        sess.close()


# ---------- 主程式 ----------
_init_session_defaults()
ensure_bootstrap_admin()
login_guard()
render_auth_sidebar()

st.title("交易輸入")
_inject_trade_entry_css()
st.caption(
    "仿奇摩持倉表：在持有股票列直接 Key in 買賣；含手續費/證交稅估算、"
    "賣出時可指定沖銷配對（例如僅配近 3 天買進，不與舊庫存混算）。"
)

# 首次使用時，用既有交易中的買賣人自動補齊名單
ensure_traders_seeded()

try:
    trades, masters, custom_rules, stocks = _load_data()
except OperationalError:
    st.warning("資料庫無法使用。雲端請設定 USE_GOOGLE_SHEET 與 Google Sheet Secrets。")
    st.stop()

today = date.today()
allowed = get_allowed_traders()

# ---------- 工具列（只留必填：買賣人、交易日期；其餘進階設定收合） ----------
tb1, tb2, tb3 = st.columns([1.6, 1.1, 0.9])
with tb1:
    if is_admin():
        trader_names = list_trader_names()
        if trader_names:
            last = st.session_state.get("last_user")
            _def = resolve_default_trader(trader_names)
            idx = trader_names.index(last) if last in trader_names else (trader_names.index(_def) if _def else 0)
            trader = st.selectbox("買賣人", options=trader_names, index=idx, key="te_trader_sel")
        else:
            trader = ""
            st.selectbox("買賣人", options=["（尚無名單，請於下方新增）"], disabled=True, key="te_trader_empty")
    else:
        if not allowed:
            st.warning("帳號尚未綁定買賣人，請聯絡管理者。")
            st.stop()
        last = st.session_state.get("last_user")
        _def = resolve_default_trader(allowed)
        idx = allowed.index(last) if last in allowed else (allowed.index(_def) if _def else 0)
        trader = st.selectbox("買賣人", options=allowed, index=idx, key="te_trader_sel")
with tb2:
    trade_date = st.date_input("交易日期", value=st.session_state.get("te_date", today), key="te_date_in")
    st.session_state["te_date"] = trade_date
with tb3:
    st.markdown("<div style='height:1.75rem'></div>", unsafe_allow_html=True)
    if st.button("🔄 更新現價", use_container_width=True):
        clear_quote_cache()
        st.rerun()

# ---------- 自動更新報價（盤中、未展開個股時每 N 秒跳動） ----------
arc1, arc2, arc3 = st.columns([1.1, 1.0, 2.5])
with arc1:
    st.session_state["te_autorefresh"] = st.toggle(
        "自動更新報價",
        value=st.session_state.get("te_autorefresh", True),
        key="te_autorefresh_tg",
        help="開啟後，盤中且未展開任何個股時，會每隔數秒自動抓最新股價並刷新畫面。",
    )
with arc2:
    st.session_state["te_ar_interval"] = st.selectbox(
        "更新間隔",
        options=[10, 15, 20, 30, 60],
        index=[10, 15, 20, 30, 60].index(st.session_state.get("te_ar_interval", 20))
        if st.session_state.get("te_ar_interval", 20) in [10, 15, 20, 30, 60] else 2,
        format_func=lambda s: f"每 {s} 秒",
        key="te_ar_interval_sel",
    )
with arc3:
    _maybe_autorefresh()

# ---------- 讀取「進階設定 / 費率」目前值（設定面板已移到頁尾，計算在此先取用） ----------
# Streamlit 會在重跑前先把 widget 值寫回其 key，故此處讀 widget key 能拿到最新選擇，
# 設定面板移到頁尾也不會有一次重跑的延遲。
_PERIOD_OPTS = [1, 3, 7, 30, 180]
_POLICY_KEYS = list(_POLICY_OPTIONS.keys())
policy = st.session_state.get("te_policy_sel")
if policy not in _POLICY_KEYS:
    policy = st.session_state.get("te_policy", "CUSTOM_PLUS_FIFO")
st.session_state["te_policy"] = policy
st.session_state["te_auto_fifo"] = st.session_state.get(
    "te_auto_fifo_cb", st.session_state.get("te_auto_fifo", True)
)
# 費率面板在頁尾：用 widget key 回填 session，讓上方費用估算取到最新值
if "te_fee_rate" in st.session_state:
    st.session_state["fee_rate"] = st.session_state["te_fee_rate"]
if "te_tax_rate" in st.session_state:
    st.session_state["tax_rate"] = st.session_state["te_tax_rate"]

# 當月已實現：從交易日所在月份的 1 號到交易日（月初至今）
period_start = trade_date.replace(day=1)
period_end = trade_date

# 批次預抓即時報價（TWSE MIS 一次一包），暖快取後下面逐檔取價直接命中
_sids_for_quote = {
    t.stock_id for t in trades
    if (not trader or (t.user or "").strip() == trader.strip())
}
_sids_for_quote |= set(st.session_state.get(_added_stocks_key(trader), []))
if _sids_for_quote:
    _ex_map = {sid: getattr(masters.get(sid), "exchange", None) for sid in _sids_for_quote}
    get_quotes_cached(list(_sids_for_quote), exchanges=_ex_map)

holdings = build_holdings_summary(
    trades,
    masters,
    trader,
    custom_rules,
    policy,
    get_quote_cached,
    period_start,
    period_end,
    today=trade_date,
)

daily_total, _ = compute_realized_in_range(
    trades, trader, trade_date, trade_date, custom_rules, policy
)
period_total, _ = compute_realized_in_range(
    trades, trader, period_start, period_end, custom_rules, policy
)
unrealized_total = sum(h["unrealized"] for h in holdings)
market_value_total = sum(h["market_value"] for h in holdings)
invested_cost_total = sum(h["total_cost"] for h in holdings)

k1, k2, k3, k4, k5, k6 = st.columns(6)
# 損益數字：賠=紅、賺=藍、中性不上色、保留 +/− 號；成本/市值為中性不上色不加號
_render_kpi(k1, "① 當日已實現", daily_total, "賣出日=所選交易日的已實現淨損益（扣費稅）")
_render_kpi(k2, "② 當月已實現", period_total, f"當月已實現淨損益（扣費稅）：{period_start}～{period_end}")
_render_kpi(k3, "③ 持倉未實現", unrealized_total, "以即時價（TWSE 官方報價，盤中約每 5 秒更新）估算，未扣未來賣出費稅")
_render_kpi(k4, "④ 已投入成本", invested_cost_total, "目前持股的總買進成本（已含買進手續費）。＝持倉總市值 − 持倉未實現。", pnl=False)
_render_kpi(k5, "⑤ 持倉總市值", market_value_total, "所有持股「即時價 × 持有股數」的加總（TWSE 官方報價）。零股與券商/奇摩因整股vs零股收盤價、抓價時點不同，可能差幾檔屬正常。", pnl=False)
_render_kpi(k6, "盤中合計參考", daily_total + unrealized_total, "當日已實現 + 未實現（快速掌握盤中狀態）")

_render_add_stock_expander(masters, trader)

# 今日有交易但已無持倉的標的
today_trades_all = [
    t for t in trades
    if t.trade_date == trade_date and (not trader or (t.user or "").strip() == trader.strip())
]
today_sids = {t.stock_id for t in today_trades_all}
holding_sids = {h["stock_id"] for h in holdings}
# 手動「加入持倉列表」但尚未有交易的股票，也補一列（0 股）供輸入第一筆買進
manual_sids = set(st.session_state.get(_added_stocks_key(trader), []))
# 該買賣人所有曾交易過的標的（含已全部賣出＝0 股者），一律保留在持股列表，
# 要刪就點該股展開後自行刪除（不另設「已清空」區）。
traded_sids = {
    str(t.stock_id).strip() for t in trades
    if getattr(t, "stock_id", None) and (not trader or (t.user or "").strip() == (trader or "").strip())
}
extra_sids = (traded_sids | manual_sids) - holding_sids

for sid in extra_sids:
    quote = get_quote_cached(sid)
    price = float(quote["price"]) if quote else 0.0
    m = masters.get(sid)
    holdings.append({
        "stock_id": sid,
        "name": (getattr(m, "name", None) or sid) if m else sid,
        "qty": 0,
        "avg_price": 0.0,
        "avg_cost": 0.0,
        "total_cost": 0.0,
        "price": price,
        "change": float(quote.get("change", 0)) if quote else 0.0,
        "change_pct": float(quote.get("change_pct", 0)) if quote else 0.0,
        "market_value": 0.0,
        "unrealized": 0.0,
        "realized_today": 0.0,
        "realized_period": 0.0,
        "total_pnl": 0.0,
    })

if holdings:
    st.subheader("持有股票（點「輸入」展開該股買賣）")
    _render_holdings_header()
    for h in sorted(holdings, key=lambda x: (-float(x.get("market_value", 0) or 0), x["stock_id"])):
        _render_stock_trade_panel(h, masters, trades, custom_rules, policy, trader, trade_date)
else:
    st.info("目前無持倉。請用上方「新增股票」加入標的，或至主檔/設定載入種子資料。")

st.divider()
st.subheader("當日全部成交")
sess = get_session()
day_trades = filter_trades_by_permission(
    sess.query(Trade).filter(Trade.trade_date == trade_date).order_by(Trade.id).all()
)
if trader:
    day_trades = [t for t in day_trades if (t.user or "").strip() == trader.strip()]
sess.close()

def _day_stock_label(sid: str) -> str:
    sid = str(sid).strip()
    m = masters.get(sid)
    nm = (getattr(m, "name", None) or "").strip() if m else ""
    return f"{sid} {nm}".strip() if nm else sid


if day_trades:
    df = pd.DataFrame([
        {
            "id": t.id,
            "股票": _day_stock_label(t.stock_id),
            "買賣人": t.user,
            "買/賣": "買入" if str(t.side).upper() == "BUY" else "賣出",
            "價格": t.price,
            "股數": t.quantity,
            "手續費": t.fee,
            "證交稅": t.tax,
            "當沖": "當沖" if t.is_daytrade else "",
            "備註": t.note or "",
        }
        for t in day_trades
    ])
    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "價格": st.column_config.NumberColumn("價格", format="accounting"),
            "股數": st.column_config.NumberColumn("股數", format="localized"),
            "手續費": st.column_config.NumberColumn("手續費", format="accounting"),
            "證交稅": st.column_config.NumberColumn("證交稅", format="accounting"),
            "當沖": st.column_config.TextColumn(
                "當沖",
                help="當日沖銷：同一天買進又賣出、當天軋平的交易；標「當沖」表示這筆屬當沖，空白為一般買賣。",
            ),
        },
    )
    _fr_now, _tr_now = get_fee_tax_rates()
    st.caption(
        f"💡 **手續費公式**（國泰基準）：成交價 × 股數 × 手續費率（目前 **{_fr_now:.4%}**），無條件捨去至整數、未滿 1 元以 1 元計。"
        f"　例：2365 × 100 × {_fr_now:.4%} ＝ {2365*100*_fr_now:.2f} → {int(2365*100*_fr_now)} 元。"
        f"　賣出另收證交稅 ＝ 成交價 × 股數 × **{_tr_now:.3%}**（ETF 0.1%；勾當沖則一般個股減半為 {_tr_now/2:.3%}）。"
        "　券商若有打折，可在下方「手續費／證交稅率」或主檔設定調整費率。"
    )
    st.caption("此表為當日成交總覽（唯讀）。要修改／刪除請用上方各股票展開的「交易明細」，或下方輸入交易 ID 刪除。")
    del_id = st.number_input("刪除交易 ID", min_value=0, value=0, step=1, key="te_del_id")
    if st.button("刪除該筆") and del_id:
        sess = get_session()
        target = sess.query(Trade).filter(Trade.id == int(del_id)).first()
        if not target:
            st.warning("找不到該筆。")
        elif not can_access_trader(target.user):
            st.error("無權限刪除。")
        else:
            sess.query(CustomMatchRule).filter(CustomMatchRule.sell_trade_id == int(del_id)).delete()
            sess.query(CustomMatchRule).filter(CustomMatchRule.buy_trade_id == int(del_id)).delete()
            sess.query(Trade).filter(Trade.id == int(del_id)).delete()
            sess.commit()
            st.success("已刪除（含相關沖銷規則）")
            st.rerun()
        sess.close()
else:
    st.caption("所選日期尚無成交。")

with st.expander("報價連線狀態"):
    dbg = get_finmind_debug("2330")
    if dbg.get("token_set") and not dbg.get("error"):
        st.success(dbg.get("message", "FinMind 正常"))
    elif not dbg.get("token_set"):
        st.warning("未設定 FINMIND_TOKEN，目前為模擬報價。")
    else:
        st.warning(dbg.get("message", ""))

# ---------- 設定（平常不太需要動，收在頁尾） ----------
st.markdown("---")
st.caption("⚙️ 以下為平常不太需要調整的設定，需要時再展開。")

with st.expander("⚙️ 進階設定（沖銷口徑…通常不用改）", expanded=False):
    ac2, ac3 = st.columns([1.4, 1])
    with ac2:
        st.selectbox(
            "沖銷口徑",
            options=_POLICY_KEYS,
            format_func=lambda k: _POLICY_OPTIONS[k],
            index=_POLICY_KEYS.index(st.session_state.get("te_policy", "CUSTOM_PLUS_FIFO"))
            if st.session_state.get("te_policy", "CUSTOM_PLUS_FIFO") in _POLICY_KEYS else 0,
            key="te_policy_sel",
        )
    with ac3:
        st.markdown("<div style='height:1.75rem'></div>", unsafe_allow_html=True)
        st.checkbox(
            "賣出未配對時自動先進先出",
            value=st.session_state.get("te_auto_fifo", True),
            key="te_auto_fifo_cb",
        )
    st.caption("改動會套用到上方『當日／當月已實現』與持股損益的計算（下次互動即生效）。")

with st.expander("⚙️ 手續費 / 證交稅率（寫入交易時自動帶入）", expanded=False):
    fr, tr = get_fee_tax_rates()
    cfa, ctb = st.columns(2)
    with cfa:
        st.number_input("手續費率", value=fr, format="%.8f", key="te_fee_rate")
    with ctb:
        st.number_input("證交稅率（賣出）", value=tr, format="%.4f", key="te_tax_rate")
    st.caption("預設 0.035625%（0.1425% × 2.5 折，國泰基準）／證交稅 0.3%。手續費與稅皆無條件捨去至整數。改率只影響之後新輸入或重算的交易。")

if is_admin():
    with st.expander("👥 管理買賣人名單（新增／刪除，會存到 Google 試算表）", expanded=False):
        ma1, ma2 = st.columns([2, 1])
        with ma1:
            new_trader = st.text_input("新增買賣人", key="te_new_trader", placeholder="輸入名稱，例如 Jonathan")
        with ma2:
            st.markdown("<div style='height:1.75rem'></div>", unsafe_allow_html=True)
            if st.button("➕ 新增", key="te_add_trader_btn", use_container_width=True):
                ok, msg = add_trader(new_trader)
                (st.success if ok else st.warning)(msg)
                if ok:
                    st.rerun()
        names_now = list_trader_names()
        if names_now:
            md1, md2 = st.columns([2, 1])
            with md1:
                del_trader_name = st.selectbox("刪除買賣人", options=names_now, key="te_del_trader_sel")
            with md2:
                st.markdown("<div style='height:1.75rem'></div>", unsafe_allow_html=True)
                if st.button("🗑️ 刪除", key="te_del_trader_btn", use_container_width=True):
                    ok, msg = delete_trader(del_trader_name)
                    (st.success if ok else st.warning)(msg)
                    if ok:
                        st.rerun()
            st.caption("刪除只是把名字從選單移除，該買賣人已輸入的交易與歷史不受影響。")
