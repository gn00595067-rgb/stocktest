# -*- coding: utf-8 -*-
"""交易輸入（仿奇摩）：持倉清單內直接 Key in 買賣，即時損益與沖銷配對"""
import os
import sys
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
from services.trade_entry_service import (
    build_holdings_summary,
    get_open_buy_lots,
    fifo_match_plan,
    recent_days_match_plan,
    profit_ranked_match_plan,
    nearest_avg_match_plan,
    sort_lots_by_strategy,
    preview_avg_cost_after_buy,
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
    st.session_state.setdefault("fee_rate", 0.001425)
    st.session_state.setdefault("tax_rate", 0.003)
    st.session_state.setdefault("te_date", date.today())
    st.session_state.setdefault("te_period_days", 3)
    st.session_state.setdefault("te_policy", "CUSTOM_PLUS_FIFO")
    st.session_state.setdefault("te_auto_fifo", True)
    st.session_state.setdefault("te_added_stocks", [])


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


# 沖銷表欄寬（表頭與資料列必須一致）
_MATCH_COL_WIDTHS = [0.52, 0.92, 0.58, 0.62, 0.82, 0.68, 1.08]


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
                st.caption(f"尚缺 **{sell_qty_i - plan_sum:,}** 股（可點快捷配對或手動填入）")
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


def _render_add_stock_expander(masters: dict):
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
                        added = st.session_state.setdefault("te_added_stocks", [])
                        if picked not in added:
                            added.append(picked)
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
    expand = st.session_state.get("te_expand_stock") == sid
    label = f"**{row['name']}** `{sid}`　現價 {row['price']:.2f} ({row['change_pct']:+.2f}%)　"
    label += f"持有 {row['qty']:,}　均價 {row['avg_cost']:.2f}　未實現 {_fmt_pnl(row['unrealized'])}"
    with st.expander(label, expanded=expand):
        if expand:
            st.session_state.pop("te_expand_stock", None)
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("市值", f"{row['market_value']:,.0f}")
        c2.metric("當日已實現", _fmt_pnl(row["realized_today"]), delta_color=_pnl_delta_color(row["realized_today"]))
        c3.metric("期間已實現", _fmt_pnl(row["realized_period"]), delta_color=_pnl_delta_color(row["realized_period"]))
        c4.metric("未實現", _fmt_pnl(row["unrealized"]), delta_color=_pnl_delta_color(row["unrealized"]))
        c5.metric("買進後均價預覽", "—", help="填寫下方表單後顯示")

        pos_map = compute_position_and_cost_by_stock(
            [t for t in trades if (t.user or "").strip() == trader.strip()],
            custom_rules=custom_rules,
            policy=policy,
        )
        cur = pos_map.get(sid, {"qty": 0, "cost": 0.0})
        cur_qty, cur_cost = cur["qty"], cur["cost"]

        quote = get_quote_cached(sid)
        default_price = float(quote["price"]) if quote else row["price"]

        fc1, fc2, fc3, fc4 = st.columns([1, 1, 1, 2])
        with fc1:
            side = st.radio("買/賣", ["BUY", "SELL"], horizontal=True, key=f"te_side_{sid}")
        with fc2:
            price = st.number_input(
                "成交價",
                min_value=0.0,
                value=float(default_price),
                step=0.01,
                format="%.2f",
                key=f"te_price_{sid}",
            )
        with fc3:
            quantity = st.number_input(
                "股數",
                min_value=1,
                value=1000,
                step=100,
                key=f"te_qty_{sid}",
            )
        with fc4:
            is_daytrade = st.checkbox("當沖", key=f"te_dt_{sid}")
            note = st.text_input("備註", key=f"te_note_{sid}")

        fee_est, tax_est = fees_for_trade(side, price, int(quantity), is_etf=is_etf)
        st.caption(
            f"估算：手續費 **{fee_est:,.0f}** 元"
            + (f"　證交稅 **{tax_est:,.0f}** 元" if side == "SELL" else "")
            + f"　（費率可於「主檔/設定」調整，目前 {get_fee_tax_rates()[0]:.4%} / 稅 {get_fee_tax_rates()[1]:.3%}）"
        )

        match_plan_key = f"te_match_{sid}"
        open_lots = []
        match_plan = []

        if side == "SELL":
            open_lots = get_open_buy_lots(trades, sid, trader, custom_rules, policy)
            if not open_lots:
                st.warning("無可沖銷的買進庫存。")
            else:
                st.markdown("**沖銷配對** — 指定此筆賣出對應的買進批次（空白視為 0，不會造成錯誤）")
                b1, b2, b3, b4 = st.columns(4)
                sell_qty = int(quantity)
                sort_key = f"te_sort_{sid}"
                with b1:
                    if st.button("FIFO 自動", key=f"te_fifo_{sid}", use_container_width=True):
                        st.session_state[sort_key] = "fifo"
                        _apply_match_plan(sid, match_plan_key, fifo_match_plan(sell_qty, open_lots), open_lots)
                        st.rerun()
                with b2:
                    if st.button("僅近3天", key=f"te_r3_{sid}", use_container_width=True):
                        st.session_state[sort_key] = "recent"
                        _apply_match_plan(
                            sid, match_plan_key, recent_days_match_plan(sell_qty, open_lots, 3), open_lots
                        )
                        st.rerun()
                with b3:
                    if st.button("僅近5天", key=f"te_r5_{sid}", use_container_width=True):
                        st.session_state[sort_key] = "recent"
                        _apply_match_plan(
                            sid, match_plan_key, recent_days_match_plan(sell_qty, open_lots, 5), open_lots
                        )
                        st.rerun()
                with b4:
                    if st.button("清空配對", key=f"te_clr_{sid}", use_container_width=True):
                        st.session_state[sort_key] = None
                        _apply_match_plan(sid, match_plan_key, [], open_lots)
                        st.rerun()

                st.caption("依獲利快速篩選（賣價固定，賺多＝買價最低先配）；按下後表格會依該策略排序")
                f1, f2, f3 = st.columns(3)
                with f1:
                    if st.button(
                        "💰 賺多優先", key=f"te_pmax_{sid}", use_container_width=True,
                        help="優先配對買價最低（獲利最多）的批次，並將表格由賺多到賺少排序",
                    ):
                        st.session_state[sort_key] = "profit_max"
                        _apply_match_plan(
                            sid, match_plan_key,
                            profit_ranked_match_plan(sell_qty, open_lots, most_profit=True),
                            open_lots,
                        )
                        st.rerun()
                with f2:
                    if st.button(
                        "🪙 賺少優先", key=f"te_pmin_{sid}", use_container_width=True,
                        help="優先配對買價最高（獲利最少）的批次，並將表格由賺少到賺多排序",
                    ):
                        st.session_state[sort_key] = "profit_min"
                        _apply_match_plan(
                            sid, match_plan_key,
                            profit_ranked_match_plan(sell_qty, open_lots, most_profit=False),
                            open_lots,
                        )
                        st.rerun()
                with f3:
                    if st.button(
                        "⚖️ 接近均價", key=f"te_pavg_{sid}", use_container_width=True,
                        help="優先配對買價最接近庫存加權平均成本的批次，並依接近程度排序",
                    ):
                        st.session_state[sort_key] = "nearest_avg"
                        _apply_match_plan(
                            sid, match_plan_key,
                            nearest_avg_match_plan(sell_qty, open_lots),
                            open_lots,
                        )
                        st.rerun()

                # 依最近按下的策略排序表格顯示（不影響股數計算）
                open_lots = sort_lots_by_strategy(open_lots, st.session_state.get(sort_key))

                match_plan = _render_match_panel(
                    sid,
                    match_plan_key,
                    open_lots,
                    sell_qty,
                    float(price),
                    trades,
                    fee_est,
                    tax_est,
                )
                trade_by_id = {t.id: t for t in trades}
                est_realized = realized_pnl_for_sell_plan(
                    price, sell_qty, fee_est, tax_est, match_plan, trade_by_id
                )
                st.info(f"依目前配對，預估本次賣出淨損益：**{_fmt_pnl(est_realized)}** 元（含手續費與證交稅）")
        else:
            new_qty, new_cost, new_avg = preview_avg_cost_after_buy(
                cur_qty, cur_cost, price, int(quantity), fee_est
            )
            st.info(
                f"若以此價買進 {int(quantity):,} 股，買進後均價約 **{new_avg:.2f}** 元"
                f"（持股 {cur_qty:,} → {new_qty:,}）"
            )

        if st.button("✅ 送出此筆交易", key=f"te_submit_{sid}", type="primary"):
            if not can_access_trader(trader):
                st.error("無此買賣人權限。")
                return
            if side == "SELL" and open_lots:
                if not match_plan and not st.session_state.get("te_auto_fifo"):
                    st.error("請設定沖銷配對，或勾選上方「賣出未配對時自動 FIFO」。")
                    return
                plan_sum = sum(q for _, q in match_plan)
                if match_plan and plan_sum != int(quantity):
                    st.error("請調整沖銷配對，使合計股數等於賣出股數。")
                    return
            sess = get_session()
            try:
                t = Trade(
                    user=trader,
                    stock_id=sid,
                    trade_date=trade_date,
                    side=side,
                    price=float(price),
                    quantity=int(quantity),
                    is_daytrade=is_daytrade,
                    fee=fee_est,
                    tax=tax_est if side == "SELL" else 0.0,
                    note=(note or None),
                )
                sess.add(t)
                sess.flush()
                if side == "SELL" and match_plan:
                    for buy_id, mq in match_plan:
                        existing = sess.query(CustomMatchRule).filter(
                            CustomMatchRule.sell_trade_id == t.id,
                            CustomMatchRule.buy_trade_id == buy_id,
                        ).first()
                        if existing:
                            existing.matched_qty = int(existing.matched_qty) + int(mq)
                        else:
                            sess.add(
                                CustomMatchRule(
                                    sell_trade_id=t.id,
                                    buy_trade_id=buy_id,
                                    matched_qty=int(mq),
                                )
                            )
                elif side == "SELL" and st.session_state.get("te_auto_fifo") and open_lots:
                    for buy_id, mq in fifo_match_plan(int(quantity), open_lots):
                        sess.add(
                            CustomMatchRule(
                                sell_trade_id=t.id,
                                buy_trade_id=buy_id,
                                matched_qty=int(mq),
                            )
                        )
                sess.commit()
                _apply_match_plan(sid, match_plan_key, [], open_lots if side == "SELL" else [])
                st.session_state["last_user"] = trader
                st.session_state["last_date"] = trade_date
                st.success(f"已新增 {sid} {side} {int(quantity):,} 股")
                st.rerun()
            except Exception as e:
                sess.rollback()
                st.error(str(e))
            finally:
                sess.close()

        # 該股今日成交
        today_ts = [
            t for t in trades
            if t.stock_id == sid
            and t.trade_date == trade_date
            and (t.user or "").strip() == trader.strip()
        ]
        if today_ts:
            st.caption("本日此股成交")
            st.dataframe(
                pd.DataFrame([
                    {
                        "ID": t.id,
                        "買/賣": t.side,
                        "價格": t.price,
                        "股數": t.quantity,
                        "手續費": t.fee,
                        "稅": t.tax,
                        "當沖": t.is_daytrade,
                    }
                    for t in today_ts
                ]),
                use_container_width=True,
                hide_index=True,
            )


# ---------- 主程式 ----------
_init_session_defaults()
ensure_bootstrap_admin()
login_guard()
render_auth_sidebar()

st.title("交易輸入")
_inject_trade_entry_css()
st.caption(
    "仿奇摩持倉表：在持有股票列直接 Key in 買賣；含手續費/證交稅估算、買進後均價預覽、"
    "賣出時可指定沖銷配對（例如僅配近 3 天買進，不與舊庫存混算）。"
)

try:
    trades, masters, custom_rules, stocks = _load_data()
except OperationalError:
    st.warning("資料庫無法使用。雲端請設定 USE_GOOGLE_SHEET 與 Google Sheet Secrets。")
    st.stop()

today = date.today()
allowed = get_allowed_traders()

# ---------- 工具列 ----------
tb1, tb2, tb3, tb4, tb5, tb6 = st.columns([1.2, 1, 1, 1.2, 1, 0.8])
with tb1:
    if is_admin():
        trader = st.text_input("買賣人", value=st.session_state.get("last_user", ""), key="te_trader")
    else:
        if not allowed:
            st.warning("帳號尚未綁定買賣人，請聯絡管理者。")
            st.stop()
        last = st.session_state.get("last_user")
        idx = allowed.index(last) if last in allowed else 0
        trader = st.selectbox("買賣人", options=allowed, index=idx, key="te_trader_sel")
with tb2:
    trade_date = st.date_input("交易日期", value=st.session_state.get("te_date", today), key="te_date_in")
    st.session_state["te_date"] = trade_date
with tb3:
    period_days = st.selectbox(
        "期間獲利",
        options=[1, 3, 7, 30, 180],
        format_func=lambda d: {1: "今日", 3: "近3天", 7: "近1週", 30: "近1月", 180: "近半年"}[d],
        index=[1, 3, 7, 30, 180].index(st.session_state.get("te_period_days", 3))
        if st.session_state.get("te_period_days", 3) in [1, 3, 7, 30, 180]
        else 1,
        key="te_period_sel",
    )
    st.session_state["te_period_days"] = period_days
with tb4:
    policy = st.selectbox(
        "沖銷口徑",
        options=list(_POLICY_OPTIONS.keys()),
        format_func=lambda k: _POLICY_OPTIONS[k],
        index=list(_POLICY_OPTIONS.keys()).index(st.session_state.get("te_policy", "CUSTOM_PLUS_FIFO")),
        key="te_policy_sel",
    )
    st.session_state["te_policy"] = policy
with tb5:
    st.session_state["te_auto_fifo"] = st.checkbox(
        "賣出未配對時自動 FIFO",
        value=st.session_state.get("te_auto_fifo", True),
        key="te_auto_fifo_cb",
    )
with tb6:
    if st.button("🔄 更新現價"):
        clear_quote_cache()
        st.rerun()

period_start = trade_date - timedelta(days=max(0, period_days - 1))
period_end = trade_date

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

k1, k2, k3, k4 = st.columns(4)
k1.metric(
    "① 當日已實現",
    f"{daily_total:,.0f}",
    help="賣出日=所選交易日的已實現淨損益（扣費稅）",
)
k2.metric(
    "② 期間已實現",
    f"{period_total:,.0f}",
    help=f"{period_start}～{period_end}",
)
k3.metric(
    "③ 持倉未實現",
    f"{unrealized_total:,.0f}",
    help="以 FinMind 即時價估算，未扣未來賣出費稅",
)
k4.metric(
    "盤中合計參考",
    f"{daily_total + unrealized_total:,.0f}",
    help="當日已實現 + 未實現（快速掌握盤中狀態）",
)

with st.expander("⚙️ 手續費 / 證交稅率（寫入交易時自動帶入）", expanded=False):
    fr, tr = get_fee_tax_rates()
    cfa, ctb = st.columns(2)
    with cfa:
        st.session_state["fee_rate"] = st.number_input(
            "手續費率", value=fr, format="%.6f", key="te_fee_rate"
        )
    with ctb:
        st.session_state["tax_rate"] = st.number_input(
            "證交稅率（賣出）", value=tr, format="%.4f", key="te_tax_rate"
        )

_render_add_stock_expander(masters)

# 今日有交易但已無持倉的標的
today_trades_all = [
    t for t in trades
    if t.trade_date == trade_date and (not trader or (t.user or "").strip() == trader.strip())
]
today_sids = {t.stock_id for t in today_trades_all}
holding_sids = {h["stock_id"] for h in holdings}
# 手動「加入持倉列表」但尚未有交易的股票，也補一列（0 股）供輸入第一筆買進
manual_sids = set(st.session_state.get("te_added_stocks", []))
extra_sids = (today_sids | manual_sids) - holding_sids

for sid in extra_sids:
    quote = get_quote_cached(sid)
    price = float(quote["price"]) if quote else 0.0
    m = masters.get(sid)
    holdings.append({
        "stock_id": sid,
        "name": (getattr(m, "name", None) or sid) if m else sid,
        "qty": 0,
        "avg_cost": 0.0,
        "price": price,
        "change_pct": float(quote.get("change_pct", 0)) if quote else 0.0,
        "market_value": 0.0,
        "unrealized": 0.0,
        "realized_today": 0.0,
        "realized_period": 0.0,
        "total_pnl": 0.0,
    })

if holdings:
    st.subheader("持有股票（點開列輸入買賣）")
    summary_df = pd.DataFrame([
        {
            "股名": h["name"],
            "代號": h["stock_id"],
            "現價": h["price"],
            "漲跌%": h["change_pct"],
            "股數": h["qty"],
            "均價": h["avg_cost"] if h["qty"] else None,
            "市值": h["market_value"],
            "當日已實現": h["realized_today"],
            "期間已實現": h["realized_period"],
            "未實現": h["unrealized"],
        }
        for h in sorted(holdings, key=lambda x: (-x["qty"], x["stock_id"]))
    ])
    st.dataframe(summary_df, use_container_width=True, hide_index=True)
    for h in sorted(holdings, key=lambda x: (-x["qty"], x["stock_id"])):
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

if day_trades:
    df = pd.DataFrame([
        {
            "id": t.id,
            "股票": t.stock_id,
            "買賣人": t.user,
            "買/賣": t.side,
            "價格": t.price,
            "股數": t.quantity,
            "手續費": t.fee,
            "證交稅": t.tax,
            "當沖": t.is_daytrade,
            "備註": t.note or "",
        }
        for t in day_trades
    ])
    st.data_editor(df, use_container_width=True, disabled=["id"], hide_index=True)
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
