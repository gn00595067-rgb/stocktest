# -*- coding: utf-8 -*-
"""股票輸入（仿 Yahoo 奇摩「持股明細」）：分頁＝存起來的篩選條件，每個買賣人各自一組。

規格：docs/specs/股票輸入_仿Yahoo.md
"""
import os
import sys
import time
from datetime import date

import streamlit as st

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
from services.auth_service import (
    ensure_bootstrap_admin, login_guard, render_auth_sidebar, is_admin,
    get_allowed_traders, can_access_trader, filter_trades_by_permission,
)
from services.price_service import get_quotes_cached, fetch_stock_list_cached, clear_quote_cache
from services.trade_fees import fees_for_trade
from services.prefs import resolve_default_trader
from services.trader_service import list_trader_names, ensure_traders_seeded
from services.position_cost import compute_position_and_cost_by_stock
from services.trade_entry_service import (
    get_open_buy_lots, combined_match_plan, sort_lots_by_strategy, estimate_match_row_net_pnl,
)
import pandas as pd
from services.mobile_ui import inject_mobile_css
import services.group_portfolio as gp
import importlib
import inspect
# Streamlit Cloud 部署後只重跑頁面、不一定重載 services 模組：簽名不對就是舊版，強制重載一次
if "group" not in inspect.signature(gp.summarize_group).parameters or not hasattr(gp, "validate_edit_trade"):
    gp = importlib.reload(gp)

st.set_page_config(page_title="股票輸入（仿Yahoo）", layout="wide")
_PAGE_T0 = time.monotonic()
inject_mobile_css()
ensure_bootstrap_admin()
login_guard()
render_auth_sidebar()

POLICY = "CUSTOM_PLUS_FIFO"   # 與庫存損益、交易輸入頁同口徑
# 展開區：新增列與交易明細共用前 7 欄寬度（日期｜買賣｜股數｜股價｜手續費｜稅｜金額/市值），上下對齊
_DETAIL_COLS = [1.15, 1.05, 0.9, 0.95, 0.6, 0.6, 1.1, 0.35, 0.35]   # 最後兩欄＝✏️、🗑
_FORM_COLS = [1.15, 1.05, 0.9, 0.95, 0.6, 0.6, 1.1, 0.7]          # 最後一欄＝當沖（賣出）或 ✕（多筆買入）
_ROW_COLS = [0.35, 9.45]   # 展開鈕｜其餘 9 欄合成一個 HTML grid（.yh-grid）

st.markdown("""
<style>
/* 分頁列：像 Yahoo 的文字分頁＋底線 */
.st-key-yh_tabs div[role="radiogroup"] { gap: 1.6rem; flex-wrap: wrap; }
.st-key-yh_tabs div[role="radiogroup"] label > div:first-child { display: none; }
.st-key-yh_tabs div[role="radiogroup"] label { padding: 0.25rem 0 0.4rem 0; border-bottom: 3px solid transparent; }
.st-key-yh_tabs div[role="radiogroup"] label p { font-size: 1.15rem; font-weight: 600; color: #555; }
.st-key-yh_tabs div[role="radiogroup"] label:has(input:checked) { border-bottom-color: #222; }
.st-key-yh_tabs div[role="radiogroup"] label:has(input:checked) p { color: #111; }
.yh-card { border: 1px solid #e5e5e5; border-radius: 10px; display: grid;
           grid-template-columns: 1.3fr 1fr 1fr; overflow: hidden; }
/* 總覽卡：三格等高、標題在上數字在下；數字不換行、字級隨寬度縮放，窄螢幕改成上下排 */
.yh-card .cell { padding: 1rem 1.5rem; min-width: 0; }
.yh-card .cell + .cell { border-left: 1px solid #eee; }
.yh-card .lbl { color: #777; font-size: .9rem; margin-bottom: .25rem; white-space: nowrap; }
.yh-card .val { font-weight: 800; line-height: 1.15; white-space: nowrap; font-variant-numeric: tabular-nums; }
.yh-card .big { font-size: clamp(1.5rem, 2.6vw, 2.3rem); color: #111; }
.yh-card .mid { font-size: clamp(1.2rem, 1.9vw, 1.65rem); }
.yh-card .unit, .yh-card .pct { font-size: .9rem; font-weight: 600; margin-left: .3rem; }
.yh-card .unit { color: #999; }
.yh-cardwrap { container-type: inline-size; }
@container (max-width: 820px) {   /* 中等寬：市值一整行，兩個損益並排 */
  .yh-card { grid-template-columns: 1fr 1fr; }
  .yh-card .cell:first-child { grid-column: 1 / -1; border-bottom: 1px solid #eee; }
  .yh-card .cell:nth-child(2) { border-left: none; }
  .yh-card .cell { padding: .8rem 1.1rem; }
}
@container (max-width: 480px) {   /* 手機：全部上下排 */
  .yh-card { grid-template-columns: 1fr; }
  .yh-card .cell + .cell { border-left: none; }
  .yh-card .cell:nth-child(3) { border-top: 1px solid #eee; }
}
/* 按鈕、表頭、數字一律不折行（折行會讓列高不一、看起來歪） */
.st-key-yh_new_group button p, .st-key-yh_refresh button p, .st-key-yh_add_stock button p,
.st-key-yh_edit_group button p, .st-key-yh_del_group button p { white-space: nowrap; }
.yh-th, .yh-td { white-space: nowrap; }
.yh-grid { font-size: .92rem; }
.yh-grid .yh-th { font-size: .82rem; }
.yh-th { color: #888; font-size: .9rem; text-align: right; }
.yh-th.l { text-align: left; }
.yh-td { text-align: right; line-height: 1.35; padding: .2rem 0; }
.yh-td.l { text-align: left; }
.yh-td .sub { color: #999; font-size: .85rem; }
.yh-td.sm { font-size: .85rem; color: #777; }
.yh-td .unit { color: #999; font-size: .8rem; margin-left: .2rem; }
.yh-up { color: #e0262b; } .yh-down { color: #12a150; } .yh-flat { color: #666; }
[class*="st-key-yh_row_"] { background: #f4f5f7; border-radius: 6px; padding: .15rem .4rem; margin-bottom: .35rem; }
[class*="st-key-yh_row_"] button { border: none; background: transparent; }
/* 交易明細的 ✏️🗑：小方鈕、不撐高列、不超出右框 */
[class*="st-key-yh_tr_"] button { min-height: 0; height: 1.9rem; width: 1.9rem; padding: 0; }
[class*="st-key-yh_tr_"] button p { font-size: .95rem; line-height: 1; }
[class*="st-key-yh_tr_"] [data-testid="stColumn"] { min-width: 0; }
[class*="st-key-yh_tg_"] button p { font-size: 1.5rem; font-weight: 700; line-height: 1; }
[class*="st-key-yh_exp_"] [data-testid="stNumberInputStepDown"],
[class*="st-key-yh_exp_"] [data-testid="stNumberInputStepUp"] { display: none; }
.yh-desc { color: #888; font-size: .9rem; }
/* 持股表每列只用一個 HTML 區塊（原本 9 個元件），重畫快很多 */
.yh-grid { display: grid; grid-template-columns: 1.15fr 1.15fr .9fr 1.05fr 1.05fr 1.2fr 1.2fr 1.2fr 0.7fr;
           align-items: center; column-gap: .8rem; }
</style>
""", unsafe_allow_html=True)


# ─── 小工具 ───────────────────────────────────────────────────────────────

def _cls(v) -> str:
    if v is None or abs(v) < 1e-9:
        return "yh-flat"
    return "yh-up" if v > 0 else "yh-down"


def _arrow(v) -> str:
    if v is None or abs(v) < 1e-9:
        return ""
    return "▲" if v > 0 else "▼"


def _pnl_html(amount, pct, show: bool = True) -> str:
    if not show:
        return '<div class="yh-td">-</div>'
    pct_s = f"({abs(pct):.2f}%)" if pct is not None else ""
    return (f'<div class="yh-td {_cls(amount)}">{_arrow(amount)} {abs(amount):,.2f}'
            f'<br><span>{pct_s}</span></div>')


def _load():
    """交易、沖銷規則、主檔：資料沒變就用快取（不重讀遠端資料庫），再依權限篩交易。"""
    from services.data_cache import load_trades_rules_masters
    all_trades, rules, masters = load_trades_rules_masters()
    return filter_trades_by_permission(all_trades), rules, masters


# ─── 分頁的新增／編輯／刪除／新增股票（對話框） ─────────────────────────────

def _stock_options():
    try:
        lst = fetch_stock_list_cached(ttl_seconds=3600) or []
    except Exception:
        lst = []
    return {s["stock_id"]: f'{s["stock_id"]} {s.get("name") or ""}' for s in lst if s.get("stock_id")}


@st.dialog("新增分頁")
def _dlg_new_group(trader: str):
    _group_form(trader, None)


@st.dialog("編輯此分頁")
def _dlg_edit_group(trader: str, group_id: int):
    _group_form(trader, group_id)


def _group_form(trader: str, group_id):
    sess = get_session()
    try:
        g = next((x for x in gp.list_groups(sess, trader) if x.id == group_id), None) if group_id else None
    finally:
        sess.close()
    name = st.text_input("分頁名稱", value=g.name if g else "", placeholder="例：1001起、10月短線", max_chars=gp.NAME_MAX_LEN)
    c1, c2 = st.columns(2)
    use_start = c1.checkbox("限定起始日", value=bool(g and g.start_date))
    start = c1.date_input("起始日", value=(g.start_date if g and g.start_date else date.today()),
                          disabled=not use_start, format="YYYY/MM/DD")
    use_end = c2.checkbox("限定結束日", value=bool(g and g.end_date))
    end = c2.date_input("結束日", value=(g.end_date if g and g.end_date else date.today()),
                        disabled=not use_end, format="YYYY/MM/DD")
    opts = _stock_options()
    cur = list(g.stock_ids) if g else []
    for s in cur:
        opts.setdefault(s, s)
    picked = st.multiselect("股票清單（可搜尋代號或名稱）", options=list(opts.keys()), default=cur,
                            format_func=lambda k: opts.get(k, k))
    only = st.checkbox("只看清單內的股票（不勾＝這位買賣人的全部股票）", value=bool(g and g.only_listed))
    st.caption("分頁只是篩選條件：同一筆交易可以同時出現在好幾個分頁；刪除分頁不會刪任何交易。")
    if st.button("儲存", type="primary", use_container_width=True):
        s2 = get_session()
        try:
            if g:
                err = gp.update_group(s2, g.id, name, start if use_start else None, end if use_end else None, picked, only)
                new_id = g.id
            else:
                new_id, err = gp.create_group(s2, trader, name, start if use_start else None,
                                              end if use_end else None, picked, only)
        finally:
            s2.close()
        if err:
            st.error(err)
        else:
            st.session_state[f"yh_goto_{trader}"] = new_id  # 下一輪建立分頁列前再切過去（不能直接改已建立的 widget）
            st.rerun()


@st.dialog("刪除此分頁")
def _dlg_delete_group(trader: str, g: gp.GroupSpec):
    st.warning(f"確定刪除分頁「{g.name}」？\n\n只會刪除這個分頁的條件，**不會刪除任何交易**。")
    c1, c2 = st.columns(2)
    if c1.button("確定刪除", type="primary", use_container_width=True):
        sess = get_session()
        try:
            gp.delete_group(sess, g.id)
        finally:
            sess.close()
        st.session_state[f"yh_goto_{trader}"] = gp.ALL_GROUP_ID
        st.rerun()
    if c2.button("取消", use_container_width=True):
        st.rerun()


@st.dialog("新增股票")
def _dlg_add_stock(trader: str, g: gp.GroupSpec):
    opts = _stock_options()
    sid = st.selectbox("股票（可輸入代號或名稱搜尋）", options=[""] + list(opts.keys()),
                       format_func=lambda k: opts.get(k, "請選擇…") if k else "請選擇…")
    st.caption("加入後會在列表顯示一列（0 股），展開即可輸入第一筆交易。")
    if st.button("加入", type="primary", use_container_width=True, disabled=not sid):
        if g.is_builtin:
            st.session_state.setdefault(f"yh_extra_{trader}", [])
            if sid not in st.session_state[f"yh_extra_{trader}"]:
                st.session_state[f"yh_extra_{trader}"].append(sid)
        else:
            sess = get_session()
            try:
                gp.add_stock_to_group(sess, g.id, sid)
            finally:
                sess.close()
        st.session_state[f"yh_open_{trader}"] = sid
        st.rerun()


def _rerun_fragment():
    """只重跑展開區（fragment）；不在 fragment 重跑中就退回整頁。"""
    from streamlit.errors import StreamlitAPIException
    try:
        st.rerun(scope="fragment")
    except StreamlitAPIException:
        st.rerun()


# ─── 展開區：交易明細＋新增交易（fragment：輸入時不整頁重畫） ──────────────

_MATCH_METHODS = {
    "nearest_avg": "⚖️ 接近均價", "fifo": "先進先出", "profit_max": "💰 賺多",
    "profit_min": "🪙 賺少", "loss_max": "🔻 賠多", "loss_min": "🩹 賠少",
}
_MATCH_TIMES = {"all": "全部", "3d": "近3天", "5d": "近5天"}


def _sell_match_ui(sid: str, trader: str, qty: int, price: float, fee: float, tax: float, k: str):
    """賣出沖銷配對（同舊「交易輸入」頁的口徑：預設接近均價，可換方式、可逐批改）。

    回傳 (plan=[(買進ID, 股數)], 錯誤訊息或 None)。
    """
    lots = get_open_buy_lots(trades, sid, trader, rules, POLICY)
    st.markdown("**沖銷配對**：這筆賣出要沖掉哪幾批買進（預設接近均價，可換方式或直接改「本次沖銷」）")
    if not lots:
        return [], "這一檔目前沒有可沖銷的買進庫存。"
    m1, m2 = st.columns([3, 1.2])
    method = m1.radio("沖銷方式", list(_MATCH_METHODS), format_func=_MATCH_METHODS.get, horizontal=True,
                      key=f"{k}_mm", label_visibility="collapsed")
    tmode = m2.radio("時間範圍", list(_MATCH_TIMES), format_func=_MATCH_TIMES.get, horizontal=True,
                     key=f"{k}_mt", label_visibility="collapsed")
    plan = dict(combined_match_plan(qty, lots, tmode, method, price))
    shown = sort_lots_by_strategy(lots, method)  # 表格順序跟著沖銷方式走，配到的批次排在前面
    df = pd.DataFrame([{
        "買進ID": int(l["trade_id"]), "買進日": str(l["date"])[:10], "買價": float(l["price"]),
        "可沖銷": int(l["remaining_qty"]), "本次沖銷": int(plan.get(l["trade_id"], 0)),
    } for l in shown])
    # 方式、範圍、股數、價格一改就換 key：表格回到新的預設配對
    edited = st.data_editor(
        df, key=f"{k}_mx_{method}_{tmode}_{qty}_{price}", hide_index=True, use_container_width=True,
        height=35 * (len(df) + 1) + 3, num_rows="fixed",   # 全部展開不留捲軸，一眼看完庫存
        column_config={
            "買進ID": st.column_config.NumberColumn(disabled=True, width="small"),
            "買進日": st.column_config.TextColumn(disabled=True),
            "買價": st.column_config.NumberColumn(disabled=True, format="%.2f"),
            "可沖銷": st.column_config.NumberColumn(disabled=True, format="%d 股"),
            "本次沖銷": st.column_config.NumberColumn(min_value=0, step=1, format="%d 股",
                                                  help="可直接改；空白或 0＝不沖這批"),
        },
    )
    rows = [(int(r["買進ID"]), int(r["本次沖銷"] or 0), int(r["可沖銷"])) for _, r in edited.iterrows()]
    err = gp.validate_match_plan(rows, qty)
    final = [(b, q) for b, q, _ in rows if q > 0]
    total = sum(q for _, q in final)
    by_id = {t.id: t for t in trades}
    est = sum(estimate_match_row_net_pnl(price, float(next(l["price"] for l in lots if l["trade_id"] == b)), q,
                                         by_id.get(b), fee, tax, qty)[1] for b, q in final)
    if err:
        st.error(f"已配 {total:,} / {qty:,} 股。{err}")
    else:
        st.success(f"已配 {total:,} / {qty:,} 股　預估這筆已實現淨損益："
                   f"{'▲' if est > 0 else ('▼' if est < 0 else '')}{abs(est):,.0f} 元")
    return final, err


def _edit_trade_form(t, holding_qty: int, quote: dict, edit_key: str):
    """交易明細的「✏️ 修改」：改日期／股數／股價。買賣方向不給改（要改請刪掉重 key）。

    股數或股價有改 → 手續費／稅依費率重算；股數有改 → 這筆的自定沖銷配對清掉，改回預設沖銷（同舊頁口徑）。
    """
    is_buy = str(t.side).upper() == "BUY"
    side = "BUY" if is_buy else "SELL"
    old_qty, old_price = int(t.quantity), float(t.price)
    k = f"yh_edf_{t.id}"
    with st.container(border=True):
        st.markdown(f"**修改這筆{'買入' if is_buy else '賣出'}**（ID {t.id}）")
        f = st.columns([1.4, 1.0, 1.0, 1.6])
        d = f[0].date_input("交易日期", value=t.trade_date, key=f"{k}_d", format="YYYY/MM/DD")
        qty = f[1].number_input("股數", min_value=0, value=old_qty, step=1000, key=f"{k}_q")
        price = f[2].number_input("股價", min_value=0.0, value=old_price, step=0.05, format="%.2f", key=f"{k}_p")
        errors, warns = gp.validate_edit_trade(
            side, old_qty, qty, price, (quote or {}).get("prev_close"), holding_qty,
            is_today=(d == date.today() and (d != t.trade_date or float(price or 0) != old_price)),
        )
        changed_core = bool(qty) and int(qty) != old_qty
        changed_amt = changed_core or (bool(price) and abs(float(price) - old_price) > 1e-9)
        if changed_amt and not errors:
            is_etf = bool(getattr(_MASTERS.get(t.stock_id), "is_etf", False))
            fee, tax = fees_for_trade(side, float(price), int(qty), is_etf=is_etf, is_daytrade=bool(t.is_daytrade))
            tax = tax if side == "SELL" else 0.0
            f[3].markdown(f'<div class="yh-td">{int(qty) * float(price):,.0f}<br>'
                          f'<span class="sub">手續費 {fee:,.0f}・稅 {tax:,.0f}（重算）</span></div>',
                          unsafe_allow_html=True)
        else:
            fee, tax = float(t.fee or 0), float(t.tax or 0)
        n_rules = sum(1 for s_id, b_id, _ in rules if s_id == t.id or b_id == t.id)
        if changed_core and n_rules:
            warns.append(f"股數有改，這筆原本的 {n_rules} 筆沖銷配對會清除，改用預設沖銷（自定沖銷＋其餘先進先出）。"
                         "要指定配對請改完到「自定沖銷設定」。")
        for e in errors:
            st.error(e)
        ok = True
        if warns and not errors:
            for w in warns:
                st.warning(w)
            ok = st.checkbox("我確認以上沒問題", key=f"{k}_ack")
        dirty = d != t.trade_date or changed_amt
        b1, b2, _ = st.columns([1, 1, 3])
        if b1.button("💾 儲存修改", key=f"{k}_save", type="primary", disabled=bool(errors) or not ok or not dirty):
            if not can_access_trader(t.user):
                st.error("無此買賣人權限。")
                return
            sess = get_session()
            try:
                row = sess.query(Trade).filter(Trade.id == t.id).first()
                if row is None:
                    st.error("找不到這筆交易（可能已被刪除），請重新整理。")
                    return
                row.trade_date, row.quantity, row.price = d, int(qty), float(price)
                row.fee, row.tax = fee, tax
                if changed_core:
                    sess.query(CustomMatchRule).filter(CustomMatchRule.sell_trade_id == t.id).delete()
                    sess.query(CustomMatchRule).filter(CustomMatchRule.buy_trade_id == t.id).delete()
                sess.commit()
            except Exception as e:
                sess.rollback()
                st.error(f"存檔失敗：{e}")
                return
            finally:
                sess.close()
            st.session_state.pop(edit_key, None)
            st.rerun()   # 整頁：持股、均價、總覽一起更新
        if b2.button("取消", key=f"{k}_no"):
            st.session_state.pop(edit_key, None)
            _rerun_fragment()


def _expanded(row: dict, group: gp.GroupSpec, stock_trades: list, holding_qty: int, quote: dict):
    sid = row["stock_id"]
    price_now = row["price"] or 0.0
    # 新增交易（送出後換一組 key，輸入框自動清空）
    st.markdown("**新增交易**")
    n = st.session_state.get(f"yh_form_n_{sid}", 0)
    k = f"yh_new_{sid}_{n}"
    rows_key = f"{k}_rows"          # 買入可多列一次送出；賣出一次一筆（要配沖銷）
    if rows_key not in st.session_state:
        st.session_state[rows_key] = [0]
    row_ids = st.session_state[rows_key]
    is_etf = bool(getattr(_MASTERS.get(sid), "is_etf", False))
    prev_close = (quote or {}).get("prev_close")
    side, entries = "BUY", []
    for i, rid in enumerate(row_ids):
        if i > 0 and side == "SELL":
            break
        rk = f"{k}_{rid}"
        f = st.columns(_FORM_COLS)
        d0 = st.session_state.get(f"{k}_{row_ids[i - 1]}_d", date.today()) if i else date.today()
        d = f[0].date_input("交易日期", value=d0, key=f"{rk}_d", format="YYYY/MM/DD", label_visibility="collapsed")
        if i == 0:
            side = f[1].selectbox("買/賣", ["BUY", "SELL"], key=f"{k}_s", label_visibility="collapsed",
                                  format_func=lambda x: "買入" if x == "BUY" else "賣出")
        else:
            f[1].markdown('<div class="yh-td l">買入</div>', unsafe_allow_html=True)
        qty = f[2].number_input("股數", min_value=0, value=None, step=1000, key=f"{rk}_q",
                                placeholder="股數", label_visibility="collapsed")
        price = f[3].number_input("股價", min_value=0.0, value=None, step=0.05, format="%.2f", key=f"{rk}_p",
                                  placeholder="股價", label_visibility="collapsed")
        is_dt = False
        if side == "SELL":
            is_dt = f[7].checkbox("當沖", key=f"{rk}_dt", help="當沖賣出：證交稅減半")
        elif i > 0 and f[7].button("✕", key=f"{rk}_x", help="移除這一列"):
            row_ids.remove(rid)
            _rerun_fragment()
        if i > 0 and not qty and not price:
            continue   # 多出來的空白列：略過
        errors, warns = gp.validate_new_trade(side, qty, price, prev_close, holding_qty, is_today=(d == date.today()))
        if not gp.trade_in_group(_NS(user=group.trader, trade_date=d, stock_id=sid), group):
            warns.append(f"日期或股票不在分頁「{group.name}」的條件內，送出後不會算進此分頁（其他分頁仍看得到）。")
        fee = tax = 0.0
        if qty and price and not [e for e in errors if "請輸入" in e]:
            fee, tax = fees_for_trade(side, float(price), int(qty), is_etf=is_etf, is_daytrade=is_dt)
            tax = tax if side == "SELL" else 0.0
            f[4].markdown(f'<div class="yh-td sm">{fee:,.0f}</div>', unsafe_allow_html=True)
            f[5].markdown(f'<div class="yh-td sm">{tax:,.0f}</div>', unsafe_allow_html=True)
            f[6].markdown(f'<div class="yh-td">{int(qty) * float(price):,.0f}</div>', unsafe_allow_html=True)
        entries.append(dict(d=d, qty=qty, price=price, is_dt=is_dt, fee=fee, tax=tax, errors=errors, warns=warns))
    if side == "BUY":
        if st.button("＋ 再加一筆買入", key=f"{k}_addrow", help="一次輸入多筆買入，最後一起送出"):
            row_ids.append(max(row_ids) + 1)
            _rerun_fragment()
    elif len(row_ids) > 1:
        st.caption("賣出一次一筆（要配沖銷）；其他買入列先藏起來，切回「買入」就會出現。")

    multi = len(entries) > 1
    e0 = entries[0]
    match_plan, errors, warns = [], [], []
    for j, e in enumerate(entries):
        pre = f"第 {j + 1} 筆：" if multi else ""
        errors += [pre + x for x in e["errors"] if not ("請輸入" in x and not (e["qty"] or e["price"]))]
        warns += [pre + x for x in e["warns"]]
    filled = all(e["qty"] and e["price"] for e in entries)
    if side == "SELL" and e0["qty"] and e0["price"] and not e0["errors"]:
        match_plan, match_err = _sell_match_ui(sid, group.trader, int(e0["qty"]), float(e0["price"]),
                                               e0["fee"], e0["tax"], k)
        if match_err:
            errors.append(match_err)
    for e in errors:
        if not e.endswith("這一檔目前沒有可沖銷的買進庫存。") and not e.startswith(("沖銷股數合計", "買進 ID")):
            st.error(e)
    ok_warn = True
    if warns and not errors and filled:
        for w in warns:
            st.warning(w)
        ok_warn = st.checkbox("我確認以上沒問題", key=f"{k}_ack")
    can_send = (not errors) and ok_warn and filled
    if can_send and multi:
        label = f"✅ 確認新增 {len(entries)} 筆買入（共 {sum(int(e['qty']) for e in entries):,} 股）"
    elif can_send:
        label = (f"✅ 確認新增：{'買入' if side == 'BUY' else '賣出'}{'（當沖）' if e0['is_dt'] else ''} "
                 f"{int(e0['qty']):,} 股 @ {float(e0['price']):,.2f}")
    else:
        label = "✅ 新增交易"
    if st.button(label, key=f"{k}_go", type="primary", disabled=not can_send):
        sig = (group.trader, sid, side, tuple((str(e["d"]), int(e["qty"]), float(e["price"])) for e in entries))
        last = st.session_state.get("yh_last_submit")
        if last and last[0] == sig and time.monotonic() - last[1] < 2.0:
            st.warning("偵測到快速重複送出，已忽略這一次（避免重複記錄）。")
        elif not can_access_trader(group.trader):
            st.error("無此買賣人權限。")
        else:
            st.session_state["yh_last_submit"] = (sig, time.monotonic())
            sess = get_session()
            try:
                for e in entries:   # 多筆買入同一次存檔：要嘛全成功、要嘛全不存
                    new_t = Trade(user=group.trader, stock_id=sid, trade_date=e["d"], side=side,
                                  price=float(e["price"]), quantity=int(e["qty"]), is_daytrade=e["is_dt"],
                                  fee=e["fee"], tax=e["tax"])
                    sess.add(new_t)
                    sess.flush()
                    if side == "SELL":   # 賣出：照上面配好的批次寫自定沖銷規則（與交易同一筆存檔）
                        for buy_id, mq in match_plan:
                            sess.add(CustomMatchRule(sell_trade_id=new_t.id, buy_trade_id=int(buy_id),
                                                     matched_qty=int(mq)))
                sess.commit()
            except Exception as e:
                sess.rollback()
                st.error(f"存檔失敗：{e}")
                st.stop()
            finally:
                sess.close()
            st.session_state[f"yh_form_n_{sid}"] = n + 1
            st.rerun()   # 整頁：持股、均價、總覽一起更新

    st.markdown("**交易明細**")
    _dcols = _DETAIL_COLS
    hdr = st.columns(_dcols)
    for c, lab, left in zip(hdr, ["交易日期", "買入/賣出", "交易股數", "交易股價", "手續費", "稅", "市值"],
                            [1, 1, 0, 0, 0, 0, 0]):
        c.markdown(f'<div class="yh-th{" l" if left else ""}">{lab}</div>', unsafe_allow_html=True)
    pend_key = f"yh_del_pending_{sid}"
    edit_key = f"yh_edit_pending_{sid}"
    _all = sorted(stock_trades, key=lambda x: (x.trade_date, x.id), reverse=True)
    _show_all = st.session_state.get(f"yh_showall_{sid}", False)
    for t in (_all if _show_all else _all[:10]):
        with st.container(key=f"yh_tr_{t.id}"):   # 一筆交易一列：文字與 ✏️🗑 垂直置中
            c = st.columns(_dcols, vertical_alignment="center")
        is_buy = str(t.side).upper() == "BUY"
        c[0].markdown(f'<div class="yh-td l">{t.trade_date:%Y/%m/%d}</div>', unsafe_allow_html=True)
        side_s = "買入" if is_buy else ("賣出（當沖）" if t.is_daytrade else "賣出")
        c[1].markdown(f'<div class="yh-td l">{side_s}</div>', unsafe_allow_html=True)
        c[2].markdown(f'<div class="yh-td">{int(t.quantity):,}<span class="unit">股</span></div>', unsafe_allow_html=True)
        c[3].markdown(f'<div class="yh-td">{float(t.price):,.2f}<span class="unit">TWD</span></div>', unsafe_allow_html=True)
        c[4].markdown(f'<div class="yh-td sm">{float(t.fee or 0):,.0f}</div>', unsafe_allow_html=True)
        c[5].markdown(f'<div class="yh-td sm">{float(t.tax or 0):,.0f}</div>', unsafe_allow_html=True)
        c[6].markdown(f'<div class="yh-td">{int(t.quantity) * price_now:,.2f}</div>', unsafe_allow_html=True)
        if c[7].button("✏️", key=f"yh_ed_{t.id}", help="修改這筆交易（日期、股數、股價）"):
            st.session_state[edit_key] = t.id
            st.session_state.pop(pend_key, None)
            _rerun_fragment()
        if c[8].button("🗑", key=f"yh_del_{t.id}", help="刪除這筆交易"):
            st.session_state[pend_key] = t.id
            st.session_state.pop(edit_key, None)
            _rerun_fragment()
        if st.session_state.get(edit_key) == t.id:
            _edit_trade_form(t, holding_qty, quote, edit_key)
        if st.session_state.get(pend_key) == t.id:
            st.warning(f"確定刪除 {t.trade_date:%Y/%m/%d} {'買入' if is_buy else '賣出'} "
                       f"{int(t.quantity):,} 股 @ {float(t.price):,.2f}？相關沖銷配對也會一起刪除。")
            d1, d2, _ = st.columns([1, 1, 3])
            if d1.button("確定刪除", key=f"yh_delok_{t.id}", type="primary"):
                if not can_access_trader(t.user):
                    st.error("無此買賣人權限。")
                else:
                    sess = get_session()
                    try:
                        sess.query(CustomMatchRule).filter(CustomMatchRule.sell_trade_id == t.id).delete()
                        sess.query(CustomMatchRule).filter(CustomMatchRule.buy_trade_id == t.id).delete()
                        sess.query(Trade).filter(Trade.id == t.id).delete()
                        sess.commit()
                    finally:
                        sess.close()
                    st.session_state.pop(pend_key, None)
                    st.rerun()   # 整頁：持股、均價、總覽一起更新
            if d2.button("取消", key=f"yh_delno_{t.id}"):
                st.session_state.pop(pend_key, None)
                _rerun_fragment()
    if len(_all) > 10:
        if st.button("收起，只看最近 10 筆" if _show_all else f"顯示全部 {len(_all)} 筆交易", key=f"yh_more_{sid}"):
            st.session_state[f"yh_showall_{sid}"] = not _show_all
            _rerun_fragment()


class _NS:
    def __init__(self, **kw):
        self.__dict__.update(kw)


# ─── 主畫面 ───────────────────────────────────────────────────────────────

ensure_traders_seeded()
trades, rules, _MASTERS = _load()

if is_admin():
    trader_opts = list_trader_names()
else:
    trader_opts = get_allowed_traders() or []
if not trader_opts:
    st.warning("帳號尚未綁定買賣人，請聯絡管理者。")
    st.stop()
_def = st.session_state.get("last_user")
if _def not in trader_opts:
    _def = resolve_default_trader(trader_opts) or trader_opts[0]

top_l, top_r = st.columns([4, 1.2])
with top_r:
    trader = st.selectbox("買賣人", trader_opts, index=trader_opts.index(_def), key="yh_trader")
st.session_state["last_user"] = trader

sess = get_session()
try:
    groups = gp.list_groups(sess, trader)
finally:
    sess.close()
gkey = f"yh_group_{trader}"
ids = [g.id for g in groups]
_goto = st.session_state.pop(f"yh_goto_{trader}", None)
if _goto is not None and _goto in ids:
    st.session_state[gkey] = _goto
if st.session_state.get(gkey) not in ids:
    st.session_state[gkey] = gp.ALL_GROUP_ID
with top_l:
    tc, nc = st.columns([5, 1.3], vertical_alignment="center")
    with tc:
        with st.container(key="yh_tabs"):
            gid = st.radio("分頁", ids, key=gkey, horizontal=True, label_visibility="collapsed",
                           format_func=lambda i, _n={g.id: g.name for g in groups}: _n.get(i, str(i)))
    if nc.button("＋ 新增分頁", key="yh_new_group"):
        _dlg_new_group(trader)
group = next(g for g in groups if g.id == gid)

# 分頁操作列
a = st.columns([1.1, 1.15, 1.15, 0.45, 0.45, 3.7], vertical_alignment="center")
if a[0].button("新增股票", key="yh_add_stock"):
    _dlg_add_stock(trader, group)
if not group.is_builtin:
    if a[1].button("編輯此分頁", key="yh_edit_group"):
        _dlg_edit_group(trader, group.id)
    if a[2].button("刪除此分頁", key="yh_del_group"):
        _dlg_delete_group(trader, group)
    if a[3].button("◀", key="yh_move_l", help="分頁往左移"):
        s_ = get_session(); gp.move_group(s_, trader, group.id, -1); s_.close(); st.rerun()
    if a[4].button("▶", key="yh_move_r", help="分頁往右移"):
        s_ = get_session(); gp.move_group(s_, trader, group.id, +1); s_.close(); st.rerun()
a[5].markdown(f'<div class="yh-desc" style="text-align:right">分頁條件：{group.describe()}</div>', unsafe_allow_html=True)

# 計算
g_trades = gp.filter_trades(trades, group)
extra = list(group.stock_ids) + (st.session_state.get(f"yh_extra_{trader}", []) if group.is_builtin else [])
all_sids = sorted({str(t.stock_id).strip() for t in g_trades} | set(extra))
quotes = get_quotes_cached(all_sids, exchanges={s: getattr(_MASTERS.get(s), "exchange", None) for s in all_sids}) if all_sids else {}
summary = gp.summarize_group(trades, group, rules, POLICY, quotes, _MASTERS, extra_stock_ids=extra)
trader_trades = [t for t in trades if (t.user or "").strip() == trader]
trader_pos = compute_position_and_cost_by_stock(trader_trades, custom_rules=rules, policy=POLICY)

# 總覽卡
rp, up = summary["realized_pct"], summary["unrealized_pct"]
st.markdown(f"""
<div class="yh-cardwrap"><div class="yh-card">
  <div class="cell"><div class="lbl">持有股票市值</div>
    <div class="val big">${summary['market_value']:,.0f}<span class="unit">TWD</span></div></div>
  <div class="cell"><div class="lbl">已實現損益</div>
    <div class="val mid {_cls(summary['realized'])}">{_arrow(summary['realized'])}{abs(summary['realized']):,.2f}<span class="pct">{f"({abs(rp):.2f}%)" if rp is not None else ""}</span></div></div>
  <div class="cell"><div class="lbl">未實現損益</div>
    <div class="val mid {_cls(summary['unrealized'])}">{_arrow(summary['unrealized'])}{abs(summary['unrealized']):,.2f}<span class="pct">{f"({abs(up):.2f}%)" if up is not None else ""}</span></div></div>
</div></div>
""", unsafe_allow_html=True)
rc1, rc2 = st.columns([5, 1], vertical_alignment="center")
from datetime import datetime, timezone, timedelta
_tw = datetime.now(timezone(timedelta(hours=8)))
rc1.caption(f"畫面更新：{_tw:%H:%M:%S}（台灣時間）・紅▲賺、綠▼賠・盤中股價約 20 秒更新一次")
if rc2.button("🔄 更新股價", key="yh_refresh"):
    clear_quote_cache()
    st.rerun()

# 持股表（fragment）：展開／收合、在展開區輸入都只重畫這張表；送出或刪除成功才整頁更新
@st.fragment
def _holdings_table(summary, trader, group, g_trades, trader_pos, quotes):
    st.write("")
    h0, h1 = st.columns(_ROW_COLS)
    h1.markdown('<div class="yh-grid">' + "".join(
        f'<div class="yh-th{" l" if left else ""}">{lab}</div>'
        for lab, left in zip(["股名/股號", "股價/漲跌(%)", "持有股數", "持股成本均價", "賣出平本底價", "市值", "已實現損益", "未實現損益", "交易筆數"],
                             [1, 0, 0, 0, 0, 0, 0, 0, 0])) + '</div>', unsafe_allow_html=True)

    open_key = f"yh_open_{trader}"
    trades_by_sid = {}
    for t in g_trades:
        trades_by_sid.setdefault(str(t.stock_id).strip(), []).append(t)

    if not summary["rows"]:
        st.info("這個分頁目前沒有交易。按「新增股票」加入股票，再展開輸入第一筆交易。")

    for r in summary["rows"]:
        sid = r["stock_id"]
        is_open = st.session_state.get(open_key) == sid
        with st.container(key=f"yh_row_{sid}"):
            c0, c1 = st.columns(_ROW_COLS, vertical_alignment="center")
            if c0.button("▾" if is_open else "▸", key=f"yh_tg_{sid}", help="收合" if is_open else "展開交易明細／新增交易"):
                st.session_state[open_key] = None if is_open else sid
                _rerun_fragment()   # 只重畫持股表，不重跑整頁（不重讀資料庫、不重抓報價）
            suffix = ".TWO" if str(r.get("exchange") or "").upper() in ("TPEX", "OTC") else ".TW"
            if r["price"] is not None:
                price_html = (f'<div class="yh-td"><b class="{_cls(r["change"])}">{r["price"]:,.2f}</b><br>'
                              f'<span class="{_cls(r["change"])}">{_arrow(r["change"])} {abs(r["change"]):,.2f} '
                              f'({abs(r["change_pct"]):.2f}%)</span></div>')
            else:
                price_html = '<div class="yh-td">-</div>'
            avg_s = f"{r['avg_cost']:,.2f}" if r["qty"] else "-"
            mv_s = f"{r['market_value']:,.2f}" if r["qty"] else "-"
            be_s = f"{r['breakeven']:,.2f}" if r["qty"] and r.get("breakeven") else "-"
            c1.markdown(
                '<div class="yh-grid">'
                f'<div class="yh-td l"><b>{r["name"]}</b><br><span class="sub">{sid}{suffix}</span></div>'
                + price_html
                + f'<div class="yh-td">{r["qty"]:,}<span class="unit">股</span></div>'
                + f'<div class="yh-td">{avg_s}<span class="unit">TWD</span></div>'
                + f'<div class="yh-td">{be_s}<span class="unit">TWD</span></div>'
                + f'<div class="yh-td">{mv_s}</div>'
                + _pnl_html(r["realized"], r["realized_pct"], show=r["has_realized"])
                + _pnl_html(r["unrealized"], r["unrealized_pct"], show=bool(r["qty"]))
                + f'<div class="yh-td">{r["n_trades"]}筆</div>'
                '</div>', unsafe_allow_html=True)
        if is_open:
            with st.container(border=True, key=f"yh_exp_{sid}"):
                _expanded(r, group, trades_by_sid.get(sid, []), int(trader_pos.get(sid, {}).get("qty", 0)), quotes.get(sid) or {})


_holdings_table(summary, trader, group, g_trades, trader_pos, quotes)

print(f"[page] 股票輸入（仿Yahoo） 整頁執行 {time.monotonic() - _PAGE_T0:.1f} 秒", flush=True)
