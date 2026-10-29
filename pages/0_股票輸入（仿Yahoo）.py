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

from services.fresh import reload_stale_modules
reload_stale_modules()   # 部署後自動重新載入過期的 services／reports（免 Reboot）
from services.stock_list_loader import ensure_google_sheet_loaded, search_stocks

ensure_google_sheet_loaded()

try:
    if hasattr(st, "secrets") and st.secrets.get("FINMIND_TOKEN"):
        os.environ.setdefault("FINMIND_TOKEN", str(st.secrets["FINMIND_TOKEN"]).strip())
except Exception:
    pass

from db.database import get_session
from db.models import Trade, StockMaster, CustomMatchRule, PortfolioGroup
from services.auth_service import (
    ensure_bootstrap_admin, login_guard, render_auth_sidebar, is_admin, get_current_user,
    get_allowed_traders, can_access_trader, filter_trades_by_permission,
)
from services.price_service import get_quotes_cached, fetch_stock_list_cached, clear_quote_cache, _tw_market_open
from services.trade_fees import fees_for_trade, daytrade_capacity
from services.prefs import resolve_default_trader
from services.trader_service import (
    list_trader_names, ensure_traders_seeded, add_trader, trader_discounts, trader_fee_rate, discount_label,
)
from services.position_cost import compute_position_and_cost_by_stock
from services.pledge_service import load_pledges, save_pledge, pledge_sell_warning, pledge_label
from services.trade_entry_service import (
    get_open_buy_lots, combined_match_plan, sort_lots_by_strategy, estimate_match_row_net_pnl,
    sequential_match_plans,
)
import pandas as pd
from services.mobile_ui import inject_mobile_css
import services.group_portfolio as gp
import importlib
import inspect
# Streamlit Cloud 部署後只重跑頁面、不一定重載 services 模組：簽名不對就是舊版，強制重載一次
if (not hasattr(gp, "ensure_owner_groups") or not hasattr(gp, "validate_edit_trade")
        or "group" not in inspect.signature(gp.summarize_group).parameters):
    gp = importlib.reload(gp)
if not hasattr(PortfolioGroup, "owner"):   # 資料模型（db.models）是舊版：不能熱重載，要重開 app 才會補欄位
    st.error("系統剛更新，資料表需要重新載入。請管理者到 Streamlit Cloud「Manage app → Reboot app」重開一次。")
    st.stop()

st.set_page_config(page_title="股票輸入（仿Yahoo）", layout="wide")
_PAGE_T0 = time.monotonic()
inject_mobile_css()
ensure_bootstrap_admin()
login_guard()
render_auth_sidebar()

POLICY = "CUSTOM_PLUS_FIFO"   # 與庫存損益、交易輸入頁同口徑
# 展開區：新增列與交易明細共用前 7 欄寬度（日期｜買賣｜股數｜股價｜手續費｜稅｜金額/市值），上下對齊
_DETAIL_COLS = [1.1, 0.95, 0.95, 0.95, 0.8, 0.8, 1.2, 0.35, 0.35]   # 最後兩欄＝✏️、🗑
_FORM_COLS = [1.1, 0.95, 0.95, 0.95, 0.8, 0.8, 1.2, 0.7]           # 最後一欄＝當沖（賣出）或 ✕（多筆買入）
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
/* 表頭固定：往下捲持股表時表頭黏在畫面上方（top 讓出 Streamlit 頂端列 3.75rem） */
/* Streamlit 會在 container 外再包一層同高的 stLayoutWrapper，sticky 要加在那層才黏得住 */
div[data-testid="stLayoutWrapper"]:has(> .st-key-yh_thead) { position: sticky; top: 3.75rem; z-index: 20;
    background: #fff; padding: .35rem 0; border-bottom: 1px solid #eee; }
.yh-th.l { text-align: left; }
.yh-td { text-align: right; line-height: 1.35; padding: .2rem 0; }
.yh-td.l { text-align: left; }
.yh-td .sub { color: #999; font-size: .85rem; }
.yh-td .l2.pledge { display: block; margin-left: 0; color: #e65100; font-size: .78rem; font-weight: 600; }   /* 質借：永遠獨立一行 */
[class*="st-key-yh_pledge_save_"] button p { white-space: nowrap; }
.yh-td.sm { color: #888; }
.yh-td.c, .yh-th.c { text-align: center; }
[class*="st-key-yh_exp_"] input { text-align: center; }
[class*="st-key-yh_tr_"] [data-testid="stColumn"]:nth-last-child(-n+2) [data-testid="stButton"] { display: flex; justify-content: center; }
.yh-td, .yh-th { font-variant-numeric: tabular-nums; }
.yh-th .u { font-size: .75rem; color: #aaa; margin-left: .15rem; }
.yh-td .unit { color: #999; font-size: .8rem; margin-left: .2rem; }
.yh-up { color: #e0262b; } .yh-down { color: #12a150; } .yh-flat { color: #666; }
[class*="st-key-yh_row_"] { background: #f4f5f7; border-radius: 6px; padding: .15rem .4rem; margin-bottom: .35rem; }
[class*="st-key-yh_row_"] button { border: none; background: transparent; }
/* 列與列之間不要 Streamlit 預設的 1rem 空白；展開鈕不撐高列（整列都在 2.1rem 內，手指仍好點） */
div[data-testid="stLayoutWrapper"]:has(> [class*="st-key-yh_row_"]) { margin-bottom: -.75rem; }
[class*="st-key-yh_row_"] [data-testid="stMarkdownContainer"],
.st-key-yh_thead [data-testid="stMarkdownContainer"] { margin-bottom: 0 !important; }   /* 原生 -1rem 會讓兩行時溢出到下一列 */
[class*="st-key-yh_tg_"] button { min-height: 2.1rem !important; height: 2.1rem; padding: 0 .3rem; }
/* 交易明細的 ✏️🗑：小方鈕、不撐高列、不超出右框 */
[class*="st-key-yh_tr_"] button { min-height: 0; height: 1.9rem; width: 1.9rem; padding: 0; }
[class*="st-key-yh_tr_"] button p { font-size: .95rem; line-height: 1; }
[class*="st-key-yh_tr_"] [data-testid="stColumn"] { min-width: 0; }
[class*="st-key-yh_tg_"] button p { font-size: 1.5rem; font-weight: 700; line-height: 1; }
[class*="st-key-yh_exp_"] [data-testid="stNumberInputStepDown"],
[class*="st-key-yh_exp_"] [data-testid="stNumberInputStepUp"] { display: none; }
.yh-desc { color: #888; font-size: .9rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
/* 持股表每列只用一個 HTML 區塊（原本 9 個元件），重畫快很多 */
.yh-grid { display: grid; grid-template-columns: 1.15fr 1.15fr .9fr 1.05fr 1.05fr 1.2fr 1.2fr 1.2fr 0.7fr;
           align-items: center; column-gap: .8rem; }
/* 持股表一列一行（Peggy 2026-10-22：一畫面多看幾檔）：寬度夠時漲跌、％、代號接在同一行；
   不夠寬才退回兩行。依持股表自身寬度判斷（側邊欄開關會改變寬度） */
[data-testid="stMarkdownContainer"]:has(> .yh-grid) { container-type: inline-size; }
.yh-td .l2 { display: block; }
.yh-td .pct { font-size: .82rem; }
@container (min-width: 880px) {
  .yh-td .l2 { display: inline; margin-left: .3rem; }
  .yh-td .unit.twd { display: none; }   /* 表頭已是金額欄，省下寬度給一行排版 */
  .yh-grid { grid-template-columns: 1.25fr 1.75fr .8fr .95fr .95fr 1.15fr 1.6fr 1.75fr .5fr; column-gap: .6rem; }
  .yh-td { padding: 0; }
}
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
            f'<span class="l2 pct">{pct_s}</span></div>')


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


_NEW_PERSON = "＋ 新增人…"
_KIND_FULL, _KIND_FILTER = "full", "filter"


# 盤中自動更新股價（Peggy 2026-10-14：盤中不可能一直按刷新）。
# 自動更新是「整頁重跑」，會關掉對話框、可能洗掉打到一半的輸入，所以下列情況暫停：
# 有展開某檔（在輸入或看明細）、或對話框 90 秒內有動作。
AUTO_REFRESH_SEC = 30
_DIALOG_PAUSE_SEC = 90


def _mark_dialog():
    st.session_state["yh_dialog_ts"] = time.time()


@st.dialog("新增分頁")
def _dlg_new_group(owner: str, trader_opts: list, default_trader: str):
    _mark_dialog()
    _group_form(owner, None, trader_opts, default_trader)


@st.dialog("編輯此分頁")
def _dlg_edit_group(owner: str, group_id: int, trader_opts: list, default_trader: str):
    _mark_dialog()
    _group_form(owner, group_id, trader_opts, default_trader)


def _group_form(owner: str, group_id, trader_opts: list, default_trader: str):
    """分頁＝投資組合：哪個人＋（可選）日期／股票條件。"""
    sess = get_session()
    try:
        g = next((x for x in gp.list_groups(sess, owner) if x.id == group_id), None) if group_id else None
    finally:
        sess.close()
    people = list(trader_opts)
    if g and g.trader not in people:
        people.insert(0, g.trader)
    if is_admin():
        people.append(_NEW_PERSON)
    cur_person = g.trader if g else default_trader
    person = st.selectbox("這是誰的帳", people, index=people.index(cur_person) if cur_person in people else 0,
                          help="在這個分頁輸入的交易，會記在這個人名下。")
    new_person = ""
    if person == _NEW_PERSON:
        new_person = st.text_input("新的人名", placeholder="例：小孩、王小姐", max_chars=50).strip()
    kind = st.radio("分頁類型", [_KIND_FULL, _KIND_FILTER], horizontal=True,
                    index=0 if (g is None or g.is_full) else 1,
                    format_func={_KIND_FULL: "這個人的全部帳", _KIND_FILTER: "只看某段日期／某些股票"}.get)
    who = new_person if person == _NEW_PERSON else person
    name = st.text_input("分頁名稱", value=g.name if g else "", max_chars=gp.NAME_MAX_LEN,
                         placeholder=(who or "例：小孩") if kind == _KIND_FULL else "例：1001起、10月短線")
    use_start = use_end = only = False
    start = end = gp.tw_today()
    cur = list(g.stock_ids) if g else []
    picked = cur
    if kind == _KIND_FILTER:
        c1, c2 = st.columns(2)
        use_start = c1.checkbox("限定起始日（這天起買進的）", value=bool(g and g.start_date))
        start = c1.date_input("起始日", value=(g.start_date if g and g.start_date else gp.tw_today()),
                              disabled=not use_start, format="YYYY/MM/DD")
        use_end = c2.checkbox("限定結束日", value=bool(g and g.end_date))
        end = c2.date_input("結束日", value=(g.end_date if g and g.end_date else gp.tw_today()),
                            disabled=not use_end, format="YYYY/MM/DD")
        opts = _stock_options()
        for s_ in cur:
            opts.setdefault(s_, s_)
        picked = st.multiselect("股票清單（可搜尋代號或名稱）", options=list(opts.keys()), default=cur,
                                format_func=lambda k: opts.get(k, k))
        only = st.checkbox("只看清單內的股票（不勾＝這個人的全部股票）", value=bool(g and g.only_listed))
    st.caption("分頁只是看帳的方式：同一筆交易可以出現在好幾個分頁；刪除分頁不會刪任何交易。")
    if st.button("儲存", type="primary", use_container_width=True):
        if person == _NEW_PERSON:
            if not new_person:
                st.error("請輸入新的人名。")
                return
            if new_person not in trader_opts:
                ok, msg = add_trader(new_person)
                if not ok:
                    st.error(msg)
                    return
        final_name = (name or "").strip() or (who if kind == _KIND_FULL else "")
        s_from = start if (kind == _KIND_FILTER and use_start) else None
        s_to = end if (kind == _KIND_FILTER and use_end) else None
        s2 = get_session()
        try:
            if g:
                err = gp.update_group(s2, g.id, final_name, s_from, s_to, picked, only, trader=who)
                new_id = g.id
            else:
                new_id, err = gp.create_group(s2, owner, who, final_name, s_from, s_to, picked, only)
        finally:
            s2.close()
        if err:
            st.error(err)
        else:
            st.session_state[f"yh_goto_{owner}"] = new_id  # 下一輪建立分頁列前再切過去（不能直接改已建立的 widget）
            st.rerun()


@st.dialog("刪除此分頁")
def _dlg_delete_group(owner: str, g: gp.GroupSpec):
    _mark_dialog()
    st.warning(f"確定刪除分頁「{g.name}」？\n\n只會刪除這個分頁，**{g.trader} 的交易一筆都不會刪**。")
    c1, c2 = st.columns(2)
    if c1.button("確定刪除", type="primary", use_container_width=True):
        sess = get_session()
        try:
            gp.delete_group(sess, g.id)
        finally:
            sess.close()
        st.session_state.pop(f"yh_group_{owner}", None)   # 回到第一個分頁
        st.rerun()
    if c2.button("取消", use_container_width=True):
        st.rerun()


@st.dialog("新增股票")
def _dlg_add_stock(trader: str, g: gp.GroupSpec):
    _mark_dialog()
    # 自己比對（全形「－」、空白、沒打破折號都找得到）；內建下拉搜尋遇到 iPad 全形破折號會查無結果
    kw = st.text_input("搜尋股票", placeholder="輸入代號或名稱，例：3665 或 貿聯")
    sid = None
    if kw.strip():
        try:
            lst = fetch_stock_list_cached(ttl_seconds=3600) or []
        except Exception:
            lst = []
        hits, sug = search_stocks(lst, kw, limit=10)   # 對話框只列前 10 筆，打越多字越準
        if not hits and sug:
            st.caption(f"找不到「{kw.strip()}」，你是不是要找：")
        cand = hits or sug
        if cand:
            opts = {s["stock_id"]: f'{s["stock_id"]} {s.get("name") or ""}' for s in cand}
            if len(opts) <= 8:
                sid = st.radio("選擇股票", list(opts), format_func=lambda k: opts[k], label_visibility="collapsed")
            else:   # 建議很多（例：只差一碼的代號）：用可再打字篩選的下拉
                sid = st.selectbox("選擇股票", list(opts), format_func=lambda k: opts[k], index=None,
                                   placeholder=f"有 {len(opts)} 檔可能，點開選擇或再打字篩選")
        else:
            st.caption("查無符合股票。可以只打代號，或打名稱前兩個字試試（例：貿聯）。")
    st.caption("加入後會在列表顯示一列（0 股），展開即可輸入第一筆交易。")
    if st.button("加入", type="primary", use_container_width=True, disabled=not sid):
        sess = get_session()
        try:
            gp.add_stock_to_group(sess, g.id, sid)   # 存進分頁，重整也還在
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


def _match_method_picker(k: str):
    """沖銷方式＋時間範圍兩排選項。預設沿用這次登入上次選的（連續 key 賣出不用每次重選）。"""
    m1, m2 = st.columns([3, 1.2])
    mm, mt = st.session_state.get("yh_mm_pref", "nearest_avg"), st.session_state.get("yh_mt_pref", "all")
    method = m1.radio("沖銷方式", list(_MATCH_METHODS), format_func=_MATCH_METHODS.get, horizontal=True,
                      index=list(_MATCH_METHODS).index(mm), key=f"{k}_mm", label_visibility="collapsed")
    tmode = m2.radio("時間範圍", list(_MATCH_TIMES), format_func=_MATCH_TIMES.get, horizontal=True,
                     index=list(_MATCH_TIMES).index(mt), key=f"{k}_mt", label_visibility="collapsed")
    st.session_state["yh_mm_pref"], st.session_state["yh_mt_pref"] = method, tmode
    return method, tmode


def _multi_sell_match_ui(sid: str, trader: str, entries: list, k: str):
    """多筆賣出：選一種沖銷方式，每一筆照輸入順序自動配（前一筆配掉的批次，後一筆不會再配）。

    回傳 (每筆的 plan 清單, 錯誤訊息清單)。要逐批手動改請一次賣一筆。
    """
    lots = get_open_buy_lots(trades, sid, trader, rules, POLICY)
    st.markdown(f"**沖銷配對**：{len(entries)} 筆賣出用同一種方式自動配對（照上面的順序一筆接一筆配）")
    if not lots:
        return [], ["這一檔目前沒有可沖銷的買進庫存。"]
    method, tmode = _match_method_picker(k)
    plans = sequential_match_plans([(int(e["qty"]), float(e["price"])) for e in entries], lots, tmode, method)
    by_id, lot_px = {t.id: t for t in trades}, {l["trade_id"]: float(l["price"]) for l in lots}
    rows, errs, est_total = [], [], 0.0
    for j, (e, plan) in enumerate(zip(entries, plans)):
        qty, price, got = int(e["qty"]), float(e["price"]), sum(q for _, q in plan)
        est = sum(estimate_match_row_net_pnl(price, lot_px[b], q, by_id.get(b), e["fee"], e["tax"], qty)[1]
                  for b, q in plan)
        est_total += est
        if got != qty:
            errs.append(f"第 {j + 1} 筆：符合「{_MATCH_METHODS[method]}／{_MATCH_TIMES[tmode]}」的買進只夠配 "
                        f"{got:,} / {qty:,} 股，請換沖銷方式或時間範圍。")
        rows.append({
            "筆": j + 1, "賣出": f"{qty:,} 股 @ {price:,.2f}",
            "沖掉的買進": "、".join(f"#{b}（{lot_px[b]:,.2f}）{q:,} 股" for b, q in plan) or "—",
            "預估淨損益": round(est),
        })
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True,
                 column_config={"筆": st.column_config.NumberColumn(width="small"),
                                "預估淨損益": st.column_config.NumberColumn(format="%d 元")})
    if not errs:
        st.success(f"{len(entries)} 筆都已配好　預估合計已實現淨損益："
                   f"{'▲' if est_total > 0 else ('▼' if est_total < 0 else '')}{abs(est_total):,.0f} 元")
    st.caption("要逐批手動改沖銷股數，請一次賣一筆（只剩一筆時會出現可修改的配對表）。")
    return plans, errs


def _sell_match_ui(sid: str, trader: str, qty: int, price: float, fee: float, tax: float, k: str):
    """賣出沖銷配對（同舊「交易輸入」頁的口徑：預設接近均價，可換方式、可逐批改）。

    回傳 (plan=[(買進ID, 股數)], 錯誤訊息或 None)。
    """
    lots = get_open_buy_lots(trades, sid, trader, rules, POLICY)
    st.markdown("**沖銷配對**：這筆賣出要沖掉哪幾批買進（預設接近均價，可換方式或直接改「本次沖銷」）")
    if not lots:
        return [], "這一檔目前沒有可沖銷的買進庫存。"
    method, tmode = _match_method_picker(k)
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


def _discount_picker(trader: str, key: str, qty: int = 0, price: float = 0.0, stored_fee: float = None):
    """手續費折數：一般人直接用設定的折數（不出現選項）；有設「另一個折數」的人（如王小姐）多一排讓這筆自己選。

    修改既有交易時，預設選「能算出原本手續費」的那個折數；新增交易時，預設用這個人上一次選的折數
    （同一次登入內，連續 key 2.5 折不用每筆重點）。回傳 (折數, 是否改過原本的折數)。
    """
    main, alt = trader_discounts(trader)
    if not alt:
        return main, False
    opts = [main, alt]
    idx = 0
    is_edit = stored_fee is not None
    last_key = f"yh_last_disc_{trader}"
    if is_edit and qty and price:
        from services.trade_fees import estimate_broker_fee
        hit = [i for i, d in enumerate(opts)
               if abs(estimate_broker_fee(price, qty, fee_rate=trader_fee_rate(trader, d)) - stored_fee) < 0.5]
        idx = hit[0] if hit else 0
    elif not is_edit and st.session_state.get(last_key) in opts:
        idx = opts.index(st.session_state[last_key])
    disc = st.radio("手續費折數", opts, index=idx, key=key, horizontal=True, format_func=discount_label,
                    help=f"{trader} 有兩種折數，這筆用哪個就選哪個")
    if is_edit:
        st.caption("只改折數也可以：點另一個折數，手續費會自動重算，再按「儲存修改」。")
    else:
        st.session_state[last_key] = disc
    return disc, is_edit and disc != opts[idx]


def _edit_trade_form(t, holding_qty: int, quote: dict, edit_key: str):
    """交易明細的「✏️ 修改」：改日期／股數／股價。買賣方向不給改（要改請刪掉重 key）。

    股數或股價有改 → 手續費／稅依費率重算（勾「手動輸入」則以手填為準）；股數有改 → 這筆的自定沖銷配對清掉，改回預設沖銷（同舊頁口徑）。
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
            is_today=(d == gp.tw_today() and (d != t.trade_date or float(price or 0) != old_price)),
        )
        is_etf = bool(getattr(_MASTERS.get(t.stock_id), "is_etf", False))
        disc, changed_disc = _discount_picker(t.user, f"{k}_disc", old_qty, old_price, float(t.fee or 0))
        changed_core = bool(qty) and int(qty) != old_qty
        changed_amt = changed_core or (bool(price) and abs(float(price) - old_price) > 1e-9) or changed_disc
        if changed_amt and not errors:
            dt_qty = (min(int(qty), daytrade_capacity(trades, t.user, t.stock_id, d, exclude_id=t.id))
                      if t.is_daytrade else None)
            fee, tax = fees_for_trade(side, float(price), int(qty), is_etf=is_etf, is_daytrade=bool(t.is_daytrade),
                                      fee_rate=trader_fee_rate(t.user, disc), daytrade_qty=dt_qty)
            tax = tax if side == "SELL" else 0.0
        else:
            fee, tax = float(t.fee or 0), float(t.tax or 0)
        # 手動輸入（雅雲 10-29：模擬時一筆混 2.5／2.8 折，不想拆筆）：勾了以手填為準，預設帶目前算出的值
        manual = st.checkbox("手動輸入手續費／稅", key=f"{k}_man",
                       help="一筆裡混不同折數等特殊情況才用；平常不用勾，系統自動算")
        if manual:
            st.session_state.setdefault(f"{k}_mfee", float(fee))
            st.session_state.setdefault(f"{k}_mtax", float(tax))
            m = st.columns([1, 1, 3])
            fee = float(m[0].number_input("手續費", min_value=0.0, step=1.0, format="%.0f", key=f"{k}_mfee"))
            if side == "SELL":
                tax = float(m[1].number_input("證交稅", min_value=0.0, step=1.0, format="%.0f", key=f"{k}_mtax"))
            changed_amt = changed_amt or abs(fee - float(t.fee or 0)) > 1e-9 or abs(tax - float(t.tax or 0)) > 1e-9
        if changed_amt and not errors:
            f[3].markdown(f'<div class="yh-td">{int(qty) * float(price):,.0f}<br>'
                          f'<span class="sub">手續費 {fee:,.0f}・稅 {tax:,.0f}（{"手填" if manual else "重算"}）</span></div>',
                          unsafe_allow_html=True)
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


def _pledge_editor(trader: str, sid: str, holding_qty: int, pledged: int):
    """質押中股數（docs/specs/質借.md）：只是標記，不動交易與損益。"""
    p = st.columns([1.6, 0.8, 3.6], vertical_alignment="bottom")
    new_val = p[0].number_input("🏦 質押中股數", min_value=0, value=int(pledged), step=1000, key=f"yh_pledge_{trader}_{sid}",
                                help="拿去銀行質借、目前不能賣的股數；0＝沒有質押。只用來顯示可動用股數，不影響均價與損益。")
    if p[1].button("儲存", key=f"yh_pledge_save_{trader}_{sid}", disabled=int(new_val) == int(pledged)):
        if not can_access_trader(trader):
            st.error("無此買賣人權限。")
            return
        from db.database import get_engine
        save_pledge(get_engine(), trader, sid, int(new_val))
        st.session_state["yh_flash"] = f"已更新 {sid} 質押股數：{int(new_val):,} 股"
        st.rerun()   # 整頁：持股表的「可動用」小字一起更新
    if pledged:
        p[2].caption(f"持股 {holding_qty:,} 股，質押 {pledged:,} 股 → **可動用 {max(0, holding_qty - pledged):,} 股**")


def _expanded(row: dict, group: gp.GroupSpec, stock_trades: list, holding_qty: int, quote: dict, pledged: int = 0):
    sid = row["stock_id"]
    price_now = row["price"] or 0.0
    _pledge_editor(group.trader, sid, holding_qty, pledged)
    # 新增交易（送出後換一組 key，輸入框自動清空）
    st.markdown("**新增交易**")
    n = st.session_state.get(f"yh_form_n_{sid}", 0)
    entry_date = st.session_state.get("yh_entry_date") or gp.tw_today()   # 上方「輸入日期」（fragment 重跑也讀得到）
    k = f"yh_new_{sid}_{n}_{entry_date:%Y%m%d}"   # 日期也進 key：上方改了輸入日期，表單日期跟著換
    rows_key = f"{k}_rows"          # 買入、賣出都可多列一次送出（多筆賣出用同一種沖銷方式自動配）
    if rows_key not in st.session_state:
        st.session_state[rows_key] = [0]
    row_ids = st.session_state[rows_key]
    is_etf = bool(getattr(_MASTERS.get(sid), "is_etf", False))
    prev_close = (quote or {}).get("prev_close")
    disc, _ = _discount_picker(group.trader, f"{k}_disc")
    fee_rate = trader_fee_rate(group.trader, disc)
    side, entries = "BUY", []
    sold_before, dt_before = 0, {}   # 多筆賣出：前面幾列已賣的股數、各日已用掉的當沖額度
    for i, rid in enumerate(row_ids):
        rk = f"{k}_{rid}"
        f = st.columns(_FORM_COLS)
        d0 = st.session_state.get(f"{k}_{row_ids[i - 1]}_d", entry_date) if i else entry_date
        d = f[0].date_input("交易日期", value=d0, key=f"{rk}_d", format="YYYY/MM/DD", label_visibility="collapsed")
        if i == 0:
            side = f[1].selectbox("買/賣", ["BUY", "SELL"], key=f"{k}_s", label_visibility="collapsed",
                                  format_func=lambda x: "買入" if x == "BUY" else "賣出")
        elif side == "BUY":
            f[1].markdown('<div class="yh-td l">買入</div>', unsafe_allow_html=True)
        else:   # 賣出的 ✕ 放在「賣出」旁邊（最後一欄是當沖）
            s1, s2 = f[1].columns([2, 1], vertical_alignment="center")
            s1.markdown('<div class="yh-td l">賣出</div>', unsafe_allow_html=True)
            if s2.button("✕", key=f"{rk}_x", help="移除這一列"):
                row_ids.remove(rid)
                _rerun_fragment()
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
        errors, warns = gp.validate_new_trade(side, qty, price, prev_close, holding_qty - sold_before,
                                              is_today=(d == gp.tw_today()))
        if not gp.trade_in_group(_NS(user=group.trader, trade_date=d, stock_id=sid), group):
            warns.append(f"日期或股票不在分頁「{group.name}」的條件內，送出後不會算進此分頁（其他分頁仍看得到）。")
        fee = tax = 0.0
        if qty and price and not [e for e in errors if "請輸入" in e]:
            # 當沖只有「當天買進可對沖」的股數減半，其餘是賣庫存、照一般稅率
            dt_qty = (min(int(qty), max(0, daytrade_capacity(trades, group.trader, sid, d) - dt_before.get(d, 0)))
                      if is_dt else None)
            if is_dt:
                dt_before[d] = dt_before.get(d, 0) + dt_qty
            fee, tax = fees_for_trade(side, float(price), int(qty), is_etf=is_etf, is_daytrade=is_dt,
                                      fee_rate=fee_rate, daytrade_qty=dt_qty)
            tax = tax if side == "SELL" else 0.0
            if is_dt and dt_qty < int(qty):
                warns.append(f"當天可當沖的只有 {dt_qty:,} 股（當天買進扣掉已當沖的部分），"
                             f"其餘 {int(qty) - dt_qty:,} 股是賣庫存，證交稅照一般 0.3% 計算。")
            f[4].markdown(f'<div class="yh-td sm c">{fee:,.0f}</div>', unsafe_allow_html=True)
            f[5].markdown(f'<div class="yh-td sm c">{tax:,.0f}</div>', unsafe_allow_html=True)
            f[6].markdown(f'<div class="yh-td c">{int(qty) * float(price):,.0f}</div>', unsafe_allow_html=True)
        if side == "SELL" and qty:
            sold_before += int(qty)
        entries.append(dict(d=d, qty=qty, price=price, is_dt=is_dt, fee=fee, tax=tax, errors=errors, warns=warns))
    side_lb = "買入" if side == "BUY" else "賣出"
    if st.button(f"＋ 再加一筆{side_lb}", key=f"{k}_addrow", help=f"一次輸入多筆{side_lb}，最後一起送出"):
        row_ids.append(max(row_ids) + 1)
        _rerun_fragment()

    multi = len(entries) > 1
    e0 = entries[0]
    errors, warns = [], []
    for j, e in enumerate(entries):
        pre = f"第 {j + 1} 筆：" if multi else ""
        errors += [pre + x for x in e["errors"] if not ("請輸入" in x and not (e["qty"] or e["price"]))]
        warns += [pre + x for x in e["warns"]]
    if side == "SELL":   # 質借：賣超過可動用只提醒、不擋（可能已還款忘了改）
        _pw = pledge_sell_warning(sum(int(e["qty"] or 0) for e in entries), holding_qty, pledged)
        if _pw:
            warns.append(_pw)
    filled = all(e["qty"] and e["price"] for e in entries)
    plans = [[] for _ in entries]   # 每筆賣出各自的沖銷配對 [(買進ID, 股數)]
    if side == "SELL" and not multi and e0["qty"] and e0["price"] and not e0["errors"]:
        plans[0], match_err = _sell_match_ui(sid, group.trader, int(e0["qty"]), float(e0["price"]),
                                             e0["fee"], e0["tax"], k)
        if match_err:
            errors.append(match_err)
    elif side == "SELL" and multi and filled and not any(e["errors"] for e in entries):
        plans, match_errs = _multi_sell_match_ui(sid, group.trader, entries, k)
        errors += match_errs
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
        label = f"✅ 確認新增 {len(entries)} 筆{side_lb}（共 {sum(int(e['qty']) for e in entries):,} 股，{e0['d']:%m/%d}）"
    elif can_send:
        label = (f"✅ 確認新增：{'買入' if side == 'BUY' else '賣出'}{'（當沖）' if e0['is_dt'] else ''} "
                 f"{int(e0['qty']):,} 股 @ {float(e0['price']):,.2f}（{e0['d']:%m/%d}）")
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
                for e, plan in zip(entries, plans):   # 多筆同一次存檔：要嘛全成功、要嘛全不存
                    new_t = Trade(user=group.trader, stock_id=sid, trade_date=e["d"], side=side,
                                  price=float(e["price"]), quantity=int(e["qty"]), is_daytrade=e["is_dt"],
                                  fee=e["fee"], tax=e["tax"])
                    sess.add(new_t)
                    sess.flush()
                    if side == "SELL":   # 賣出：照上面配好的批次寫自定沖銷規則（與交易同一筆存檔）
                        for buy_id, mq in plan:
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
            # Peggy 2026-10-19：送出後自動收合，不用再手動按收合；結果訊息在整頁重跑後顯示
            if st.session_state.get(f"yh_open_{group.trader}") == sid:
                st.session_state[f"yh_open_{group.trader}"] = None
            _nm = getattr(_MASTERS.get(sid), "name", "") or ""
            st.session_state["yh_flash"] = (f"已新增 {len(entries)} 筆{'買入' if side == 'BUY' else '賣出'}"
                                            f"（{sid} {_nm}，{e0['d']:%m/%d}）")
            st.rerun()   # 整頁：持股、均價、總覽一起更新

    st.markdown("**交易明細**")
    _dcols = _DETAIL_COLS
    hdr = st.columns(_dcols)
    for c, lab, left in zip(hdr, ["交易日期", "買入/賣出", '交易股數<span class="u">股</span>',
                                  '交易股價<span class="u">元</span>', "手續費", "稅", "市值"],
                            [1, 1, 0, 0, 0, 0, 0]):
        c.markdown(f'<div class="yh-th c">{lab}</div>', unsafe_allow_html=True)
    pend_key = f"yh_del_pending_{sid}"
    edit_key = f"yh_edit_pending_{sid}"
    _all = sorted(stock_trades, key=lambda x: (x.trade_date, x.id), reverse=True)
    _show_all = st.session_state.get(f"yh_showall_{sid}", False)
    for t in (_all if _show_all else _all[:10]):
        with st.container(key=f"yh_tr_{t.id}"):   # 一筆交易一列：文字與 ✏️🗑 垂直置中
            c = st.columns(_dcols, vertical_alignment="center")
        is_buy = str(t.side).upper() == "BUY"
        c[0].markdown(f'<div class="yh-td c">{t.trade_date:%Y/%m/%d}</div>', unsafe_allow_html=True)
        side_s = "買入" if is_buy else ("賣出（當沖）" if t.is_daytrade else "賣出")
        c[1].markdown(f'<div class="yh-td c">{side_s}</div>', unsafe_allow_html=True)
        c[2].markdown(f'<div class="yh-td c">{int(t.quantity):,}</div>', unsafe_allow_html=True)
        c[3].markdown(f'<div class="yh-td c">{float(t.price):,.2f}</div>', unsafe_allow_html=True)
        c[4].markdown(f'<div class="yh-td sm c">{float(t.fee or 0):,.0f}</div>', unsafe_allow_html=True)
        c[5].markdown(f'<div class="yh-td sm c">{float(t.tax or 0):,.0f}</div>', unsafe_allow_html=True)
        c[6].markdown(f'<div class="yh-td c">{int(t.quantity) * price_now:,.2f}</div>', unsafe_allow_html=True)
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
owner = str((get_current_user() or {}).get("username") or "")

sess = get_session()
try:
    gp.ensure_owner_groups(sess, owner, _def, can_access=can_access_trader)   # 第一次進來：建預設分頁、認領舊分頁
    groups = gp.list_groups(sess, owner, can_access=can_access_trader)
finally:
    sess.close()
gkey = f"yh_group_{owner}"
ids = [g.id for g in groups]
_goto = st.session_state.pop(f"yh_goto_{owner}", None)
if _goto is not None and _goto in ids:
    st.session_state[gkey] = _goto
if st.session_state.get(gkey) not in ids:
    st.session_state[gkey] = ids[0]
# Peggy 2026-10-22：新增股票／搜尋／分頁設定／輸入日期平常收起來（有需要再點開），分頁列緊貼總覽卡，
# 讓下面持股表多露幾檔。收合區要用到下面選定的分頁，所以先佔位置、選完分頁再填內容。
_tools = st.expander("⚙️ 新增股票・搜尋・編輯分頁・輸入日期", expanded=False)
tc, nc = st.columns([6, 1.1], vertical_alignment="center")
with tc:
    with st.container(key="yh_tabs"):
        gid = st.radio("分頁", ids, key=gkey, horizontal=True, label_visibility="collapsed",
                       format_func=lambda i, _n={g.id: g.name for g in groups}: _n.get(i, str(i)))
if nc.button("＋ 新增分頁", key="yh_new_group"):
    _dlg_new_group(owner, trader_opts, _def)
group = next(g for g in groups if g.id == gid)
trader = group.trader                      # 這個分頁是誰的帳：輸入的交易記在這個人名下
st.session_state["last_user"] = trader

# 分頁操作列（在收合區裡）
# 搜尋框放第二格，但選項要等算完分頁內股票才知道，所以先佔位、稍後才填
a = _tools.columns([1.1, 2.1, 1.3, 1.3, 0.5, 0.5, 1.3], vertical_alignment="center")
if a[0].button("新增股票", key="yh_add_stock"):
    _dlg_add_stock(trader, group)
if a[2].button("編輯此分頁", key="yh_edit_group", help="改名、改成誰的帳、改日期／股票條件"):
    _dlg_edit_group(owner, group.id, trader_opts, _def)
if a[3].button("刪除此分頁", key="yh_del_group"):
    _dlg_delete_group(owner, group)
if a[4].button("◀", key="yh_move_l", help="分頁往左移"):
    s_ = get_session(); gp.move_group(s_, owner, group.id, -1); s_.close(); st.rerun()
if a[5].button("▶", key="yh_move_r", help="分頁往右移"):
    s_ = get_session(); gp.move_group(s_, owner, group.id, +1); s_.close(); st.rerun()
a[6].markdown(f'<div class="yh-desc" style="text-align:right" title="{group.describe()}">{group.describe()}</div>', unsafe_allow_html=True)

# 輸入日期：設一次，之後展開任何一檔「新增交易」都預設這天（補登好幾天前的交易用）。
# 存在 session：重新打開系統就回到今天；快速鈕用交易日（跳過週末）。
_today = gp.tw_today()
st.session_state.setdefault("yh_entry_date", _today)


def _set_entry_date(d):
    st.session_state["yh_entry_date"] = d


_ed = _tools.columns([1.8, 1.2, 1.2, 1.2, 1.6], vertical_alignment="bottom")
_ed[0].date_input("輸入日期", key="yh_entry_date", format="YYYY/MM/DD",
                  help="新增交易的日期預設用這天；單筆仍可在那一列另外改。重新打開系統會回到今天。")
_WD = "一二三四五六日"
for _i, (_c, _d) in enumerate(zip(_ed[1:4], gp.recent_trading_days(_today, 3))):
    _lab = f"今天 {_d:%m/%d}" if _i == 0 else f"{_d:%m/%d}（{_WD[_d.weekday()]}）"
    _c.button(_lab, key=f"yh_edq_{_i}", on_click=_set_entry_date, args=(_d,),
              help=None if _i == 0 else ("上個交易日" if _i == 1 else "再前一個交易日"),
              type="primary" if st.session_state["yh_entry_date"] == _d else "secondary",
              use_container_width=True)
entry_date = st.session_state["yh_entry_date"]
_yh_flash = st.session_state.pop("yh_flash", None)
if _yh_flash:
    st.success(f"✅ {_yh_flash}")
    st.toast(_yh_flash, icon="✅")
if entry_date != _today:
    st.warning(f"目前輸入日期是 **{entry_date:%Y/%m/%d}**（不是今天），新增的交易都會記在這天。補登完記得按「今天」改回來。")

# 計算
g_trades = gp.filter_trades(trades, group)
extra = list(group.stock_ids)
all_sids = sorted({str(t.stock_id).strip() for t in g_trades} | set(extra))
quotes = get_quotes_cached(all_sids, exchanges={s: getattr(_MASTERS.get(s), "exchange", None) for s in all_sids}) if all_sids else {}
summary = gp.summarize_group(trades, group, rules, POLICY, quotes, _MASTERS, extra_stock_ids=extra)

# 搜尋股票：選了就只顯示那一檔並自動展開（每個分頁各自記；清除＝回全部）
search_key = f"yh_search_{group.id}"
_row_names = {r["stock_id"]: r["name"] for r in summary["rows"]}
if st.session_state.get(search_key) not in _row_names:
    st.session_state[search_key] = None   # 那檔已不在這個分頁（例如被移出清單）


def _on_search(_sk=search_key, _ok=f"yh_open_{trader}"):
    if st.session_state.get(_sk):
        st.session_state[_ok] = st.session_state[_sk]


a[1].selectbox("搜尋股票", list(_row_names), key=search_key, index=None,
               placeholder="🔍 股名／股號", label_visibility="collapsed",
               format_func=lambda sid: f"{sid} {_row_names.get(sid, '')}", on_change=_on_search)
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
_live = _tw_market_open()
_open_key = f"yh_open_{trader}"
# 展開／收合只重跑持股表、不會重畫這行字，所以暫停條件直接寫在說明裡
_ar_note = (f"盤中每 {AUTO_REFRESH_SEC} 秒自動更新股價（展開某檔輸入時暫停）" if _live
            else "已收盤，股價不再自動更新")
rc1.caption(f"畫面更新：{_tw:%H:%M:%S}（台灣時間）・紅▲賺、綠▼賠・{_ar_note}")
if rc2.button("🔄 更新股價", key="yh_refresh"):
    clear_quote_cache()
    st.rerun()

if _live:
    st.session_state["yh_page_ts"] = time.time()

    @st.fragment(run_every=AUTO_REFRESH_SEC)
    def _auto_refresh(open_key=_open_key):
        """計時器每 30 秒叫一次：距上次整頁更新夠久、且沒在輸入，就整頁重跑抓新股價。"""
        now = time.time()
        if now - st.session_state.get("yh_page_ts", 0) < AUTO_REFRESH_SEC - 2:
            return   # 整頁剛跑過（含這次整頁執行本身）
        if st.session_state.get(open_key):
            return   # 有展開某檔：可能正在打字，不打斷
        if now - st.session_state.get("yh_dialog_ts", 0) < _DIALOG_PAUSE_SEC:
            return   # 剛開過對話框：整頁重跑會把對話框關掉
        st.rerun()

    _auto_refresh()

# 持股表（fragment）：展開／收合、在展開區輸入都只重畫這張表；送出或刪除成功才整頁更新
@st.fragment
def _holdings_table(summary, trader, group, g_trades, trader_pos, quotes, search_sid=None, pledges=None):
    rows = summary["rows"]
    if search_sid:
        rows = [r for r in rows if r["stock_id"] == search_sid]
        st.caption(f"🔍 只顯示搜尋結果：{search_sid}；按搜尋框的 ✕ 回到全部股票。")
    st.write("")
    thead = st.container(key="yh_thead")
    h0, h1 = thead.columns(_ROW_COLS)
    # 底價：與舊版交易輸入頁同名同說明（滑鼠停留看已含賣出費稅）
    _be_tip = "全部股數賣在此價，扣掉賣出手續費＋證交稅後剛好不虧（不是均價進位；賣出費稅約 0.34%，所以底價約＝持股成本均價×1.0034 再依升降單位進位）"
    h1.markdown('<div class="yh-grid">' + "".join(
        (f'<div class="yh-th" title="{_be_tip}">{lab} ⓘ</div>' if lab == "底價"
         else f'<div class="yh-th{" l" if left else ""}">{lab}</div>')
        for lab, left in zip(["股名/股號", "股價/漲跌(%)", "持有股數", "持股成本均價", "底價", "市值", "已實現損益", "未實現損益", "交易筆數"],
                             [1, 0, 0, 0, 0, 0, 0, 0, 0])) + '</div>', unsafe_allow_html=True)

    open_key = f"yh_open_{trader}"
    trades_by_sid = {}
    for t in g_trades:
        trades_by_sid.setdefault(str(t.stock_id).strip(), []).append(t)

    if not summary["rows"]:
        st.info("這個分頁目前沒有交易。按「新增股票」加入股票，再展開輸入第一筆交易。")

    for r in rows:
        sid = r["stock_id"]
        is_open = st.session_state.get(open_key) == sid
        with st.container(key=f"yh_row_{sid}"):
            c0, c1 = st.columns(_ROW_COLS, vertical_alignment="center")
            if c0.button("▾" if is_open else "▸", key=f"yh_tg_{sid}", help="收合" if is_open else "展開交易明細／新增交易"):
                st.session_state[open_key] = None if is_open else sid
                _rerun_fragment()   # 只重畫持股表，不重跑整頁（不重讀資料庫、不重抓報價）
            suffix = ".TWO" if str(r.get("exchange") or "").upper() in ("TPEX", "OTC") else ".TW"
            if r["price"] is not None:
                price_html = (f'<div class="yh-td"><b class="{_cls(r["change"])}">{r["price"]:,.2f}</b>'
                              f'<span class="l2 pct {_cls(r["change"])}">{_arrow(r["change"])} {abs(r["change"]):,.2f} '
                              f'({abs(r["change_pct"]):.2f}%)</span></div>')
            else:
                price_html = '<div class="yh-td">-</div>'
            avg_s = f"{r['avg_cost']:,.2f}" if r["qty"] else "-"
            mv_s = f"{r['market_value']:,.2f}" if r["qty"] else "-"
            # 底價藍字（同舊版），提醒「賣到這個價才打平」
            be_s = (f'<span style="color:#1565c0;font-weight:700">{r["breakeven"]:,.2f}</span>'
                    if r["qty"] and r.get("breakeven") else "-")
            # 質借：股數下加一行「質押 X・可動用 Y」（可動用以這個人全部持股計，不受分頁日期影響）
            _pq = int((pledges or {}).get(sid, 0))
            _pl = "".join(f'<span class="l2 pledge">{x}</span>'
                          for x in pledge_label(int(trader_pos.get(sid, {}).get("qty", 0)), _pq))
            c1.markdown(
                '<div class="yh-grid">'
                f'<div class="yh-td l"><b>{r["name"]}</b><span class="l2 sub">{sid}{suffix}</span></div>'
                + price_html
                + f'<div class="yh-td">{r["qty"]:,}<span class="unit">股</span>{_pl}</div>'
                + f'<div class="yh-td">{avg_s}<span class="unit twd">TWD</span></div>'
                + f'<div class="yh-td">{be_s}<span class="unit twd">TWD</span></div>'
                + f'<div class="yh-td">{mv_s}</div>'
                + _pnl_html(r["realized"], r["realized_pct"], show=r["has_realized"])
                + _pnl_html(r["unrealized"], r["unrealized_pct"], show=bool(r["qty"]))
                + f'<div class="yh-td">{r["n_trades"]}筆</div>'
                '</div>', unsafe_allow_html=True)
        if is_open:
            with st.container(border=True, key=f"yh_exp_{sid}"):
                _expanded(r, group, trades_by_sid.get(sid, []), int(trader_pos.get(sid, {}).get("qty", 0)), quotes.get(sid) or {},
                          pledged=int((pledges or {}).get(sid, 0)))


try:
    from db.database import get_engine
    _pledges = load_pledges(get_engine(), trader)
except Exception as _e:   # 質押只是顯示用，讀不到不影響記帳
    print(f"[page] 讀取質押失敗：{_e}", flush=True)
    _pledges = {}
_holdings_table(summary, trader, group, g_trades, trader_pos, quotes, st.session_state.get(search_key), pledges=_pledges)

print(f"[page] 股票輸入（仿Yahoo） 整頁執行 {time.monotonic() - _PAGE_T0:.1f} 秒", flush=True)
