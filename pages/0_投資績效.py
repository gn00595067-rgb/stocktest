# -*- coding: utf-8 -*-
"""損益總覽與投資績效：區間內各股盈虧、勝率、回撤與產業分布（合併頁）"""
import streamlit as st
import pandas as pd
import altair as alt
from collections import defaultdict
from datetime import date, timedelta
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
from db.models import Trade, StockMaster, CustomMatchRule
from services.pnl_engine import Lot, compute_matches, net_pnl_for_match
from services.price_service import get_quote_cached
from services.position_cost import compute_position_and_cost_by_stock

st.set_page_config(page_title="損益總覽與投資績效", layout="wide")
st.title("損益總覽與投資績效")

# ---------- 篩選條件：快速區間按鈕 + 日期框（與所選區間連動）、自訂可手動改日期 ----------
today = date.today()
if "pl_start" not in st.session_state:
    st.session_state["pl_start"] = date(2000, 1, 1)
if "pl_end" not in st.session_state:
    st.session_state["pl_end"] = today

st.markdown("#### 篩選條件")
b1, b2, b3, b4, b5, b6 = st.columns(6)
with b1:
    btn_3d = st.button("近3天", key="pl_btn_3d")
with b2:
    btn_1w = st.button("近1週", key="pl_btn_1w")
with b3:
    btn_1m = st.button("近1個月", key="pl_btn_1m")
with b4:
    btn_6m = st.button("近半年", key="pl_btn_6m")
with b5:
    btn_1y = st.button("近1年", key="pl_btn_1y")
with b6:
    btn_all = st.button("全部", key="pl_btn_all")
if btn_3d:
    st.session_state["pl_start"] = today - timedelta(days=3)
    st.session_state["pl_end"] = today
    st.rerun()
if btn_1w:
    st.session_state["pl_start"] = today - timedelta(weeks=1)
    st.session_state["pl_end"] = today
    st.rerun()
if btn_1m:
    st.session_state["pl_start"] = today - timedelta(days=30)
    st.session_state["pl_end"] = today
    st.rerun()
if btn_6m:
    st.session_state["pl_start"] = today - timedelta(days=180)
    st.session_state["pl_end"] = today
    st.rerun()
if btn_1y:
    st.session_state["pl_start"] = today - timedelta(days=365)
    st.session_state["pl_end"] = today
    st.rerun()
if btn_all:
    st.session_state["pl_start"] = date(2000, 1, 1)
    st.session_state["pl_end"] = today
    st.rerun()

col_d1, col_d2, col_p, col_m = st.columns([1, 1, 1.5, 1.2])
with col_d1:
    start_date = st.date_input("開始日期", key="pl_start")
with col_d2:
    end_date = st.date_input("結束日期", key="pl_end")
with col_p:
    policy = st.selectbox(
        "沖銷方式",
        ["CUSTOM"],
        format_func=lambda x: "自定沖銷",
    )
with col_m:
    display_mode = st.selectbox(
        "顯示模式",
        ["合計", "已實現", "未實現"],
        format_func=lambda x: {"合計": "合計（已實現+未實現）", "已實現": "已實現", "未實現": "未實現"}.get(x, x),
    )
st.caption("上方按鈕為快速區間；亦可直接修改開始／結束日期自訂區間。日期與區間連動。")

# ---------- 關鍵績效 KPI 樣式（美學設計） ----------
def _inject_kpi_style():
    st.markdown("""
    <style>
    .kpi-section { margin-bottom: 0.5rem; }
    .kpi-row-label {
        font-size: 0.7rem; font-weight: 600; letter-spacing: 0.08em; color: #64748b;
        margin-bottom: 0.5rem; margin-top: 0.75rem; text-transform: uppercase;
    }
    .kpi-row-label:first-of-type { margin-top: 0.2rem; }
    .portfolio-kpi-card {
        background: #ffffff;
        border: 1px solid #e2e8f0;
        border-radius: 14px;
        padding: 1.25rem 1.35rem;
        margin-bottom: 0.85rem;
        box-shadow: 0 1px 3px rgba(0,0,0,0.04);
        min-height: 8rem;
        display: flex;
        flex-direction: column;
        justify-content: flex-start;
        transition: box-shadow 0.2s ease, border-color 0.2s ease;
    }
    .portfolio-kpi-card:hover { box-shadow: 0 4px 12px rgba(0,0,0,0.06); border-color: #cbd5e1; }
    .portfolio-kpi-label {
        font-size: 0.7rem; font-weight: 600; letter-spacing: 0.06em; color: #64748b;
        margin-bottom: 0.45rem; text-transform: uppercase;
    }
    .portfolio-kpi-value {
        font-size: 1.4rem; font-weight: 700; letter-spacing: -0.02em; line-height: 1.35;
    }
    .portfolio-kpi-value--positive { color: #b91c1c; }
    .portfolio-kpi-value--negative { color: #15803d; }
    .portfolio-kpi-sub {
        font-size: 0.75rem; color: #94a3b8; margin-top: 0.3rem; letter-spacing: 0.01em;
    }
    .portfolio-kpi-sublabel {
        font-size: 0.9rem; font-weight: 600; color: #334155; margin-bottom: 0.25rem;
    }
    .portfolio-kpi-card .portfolio-kpi-value + .portfolio-kpi-value { margin-top: 0.25rem; }
    .kpi-spacer { min-height: 8rem; margin-bottom: 0.85rem; }
    </style>
    """, unsafe_allow_html=True)


sess = get_session()
all_trades = sess.query(Trade).all()
masters = {m.stock_id: m for m in sess.query(StockMaster).all()}
custom_rules = [(r.sell_trade_id, r.buy_trade_id, r.matched_qty) for r in sess.query(CustomMatchRule).all()]
sess.close()

# 區間內交易（start_date / end_date 來自上方按鈕或日期框，可手動改為自訂）
if start_date > end_date:
    start_date, end_date = end_date, start_date
trades_in_range = [t for t in all_trades if start_date <= t.trade_date <= end_date]

trade_by_id = {t.id: t for t in all_trades}
buys_by_stock = defaultdict(list)
sells_by_stock = defaultdict(list)
for t in trades_in_range:
    lot = Lot(t.id, t.quantity, t.price, str(t.trade_date))
    if (t.side or "").upper() == "BUY":
        buys_by_stock[t.stock_id].append(lot)
    else:
        sells_by_stock[t.stock_id].append(lot)

# 已實現：區間內沖銷，並收集每筆配對損益與賣出日（供盈虧比、回撤、累積曲線）
realized_by_stock = defaultdict(float)
match_pnls = []
matches_with_sell_date = []
for sid, sells in sells_by_stock.items():
    buys = [Lot(b.trade_id, b.qty, b.price, b.date) for b in buys_by_stock.get(sid, [])]
    sell_lots = [Lot(s.trade_id, s.qty, s.price, s.date) for s in sells]
    for m in compute_matches(buys, sell_lots, policy, custom_rules=custom_rules):
        net_pnl = net_pnl_for_match(m, trade_by_id)
        realized_by_stock[sid] += net_pnl
        match_pnls.append(net_pnl)
        buy_id, sell_id, _qty, _bp, _sp, _ = m
        sell_t = trade_by_id.get(sell_id)
        if sell_t:
            matches_with_sell_date.append((str(sell_t.trade_date), net_pnl))

# 持倉與未實現：與 庫存損益「同一套」持倉均價計算，避免兩頁均價不一致
position = defaultdict(lambda: {"qty": 0, "cost": 0.0})
for sid, data in compute_position_and_cost_by_stock(all_trades, custom_rules=custom_rules).items():
    position[sid]["qty"] = data["qty"]
    position[sid]["cost"] = data["cost"]

unrealized_by_stock = defaultdict(float)
quote_source_by_sid = {}
last_price_by_sid = {}
industry_exposure = defaultdict(float)
industry_pnl = defaultdict(float)
for sid, p in position.items():
    qty = max(0, p["qty"])
    if qty <= 0:
        continue
    avg = p["cost"] / qty if qty else 0
    q = get_quote_cached(sid)
    if q and q.get("price") is not None:
        last = float(q["price"])
        quote_source_by_sid[sid] = "API現價"
    else:
        last = avg
        quote_source_by_sid[sid] = "持倉均價(無報價)"
    last_price_by_sid[sid] = last
    u = (last - avg) * qty
    unrealized_by_stock[sid] = u
    ind = (getattr(masters.get(sid), "industry_name", None) or "") or "其他"
    industry_exposure[ind] += qty * last
    industry_pnl[ind] += u

# 合併：有已實現或未實現的股票（供各股圖、Top5、產業損益、明細表）
all_sids = set(realized_by_stock.keys()) | set(unrealized_by_stock.keys())
rows = []
for sid in all_sids:
    real = realized_by_stock.get(sid, 0)
    unreal = unrealized_by_stock.get(sid, 0)
    m = masters.get(sid)
    name = getattr(m, "name", None) or "" if m else ""
    industry = (getattr(m, "industry_name", None) or "").strip() or "其他" if m else "其他"
    rows.append({
        "stock_id": sid,
        "name": name,
        "label": f"{sid} {name}".strip() if name else sid,
        "industry": industry,
        "已實現": round(real, 2),
        "未實現": round(unreal, 2),
        "合計": round(real + unreal, 2),
    })
df = pd.DataFrame(rows)
if df.empty:
    st.info("目前區間內無損益資料（無交易或無持倉）")
    st.stop()

pnl_col = display_mode
total_pnl = df[pnl_col].sum()
realized_sum = df["已實現"].sum()
unrealized_sum = df["未實現"].sum()
win_stocks = (df[pnl_col] > 0).sum()
loss_stocks = (df[pnl_col] < 0).sum()
total_count = win_stocks + loss_stocks
win_rate_pct = (win_stocks / total_count * 100) if total_count else 0
best_row = df.loc[df[pnl_col].idxmax()] if len(df) else None
worst_row = df.loc[df[pnl_col].idxmin()] if len(df) else None

# 筆數級 KPIs（盈虧比、回撤、最大單筆）
win_count = sum(1 for p in match_pnls if p > 0)
total_trades = len(match_pnls)
wins_sum = sum(p for p in match_pnls if p > 0)
losses_sum = abs(sum(p for p in match_pnls if p < 0))
profit_factor = (wins_sum / losses_sum) if losses_sum > 0 else (wins_sum if wins_sum > 0 else 0)
max_single = max(match_pnls) if match_pnls else 0
min_single = min(match_pnls) if match_pnls else 0
pnl_by_date = defaultdict(float)
for d, pnl in matches_with_sell_date:
    pnl_by_date[d] += pnl
dates_sorted = sorted(pnl_by_date.keys())
cum_realized = 0.0
cum_series = []
for d in dates_sorted:
    cum_realized += pnl_by_date[d]
    cum_series.append({"date": d, "cumulative_pnl": cum_realized})
max_dd = 0.0
peak = 0.0
for row in cum_series:
    peak = max(peak, row["cumulative_pnl"])
    dd = peak - row["cumulative_pnl"]
    if dd > max_dd:
        max_dd = dd


def _fmt_big(val):
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return "—"
    v = float(val)
    if abs(v) >= 1e8:
        return f"{v/1e8:.2f}億"
    if abs(v) >= 1e4:
        return f"{v/1e4:.2f}萬"
    return f"{v:,.0f}"


def _fmt_pct(x):
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "—"
    try:
        return f"{float(x):.2f}%"
    except Exception:
        return str(x)


def _pnl_color(val):
    """依正負回傳 CSS class（台股：正紅、負綠），與 庫存損益 一致"""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return ""
    try:
        v = float(val)
        return "portfolio-kpi-value--positive" if v >= 0 else "portfolio-kpi-value--negative"
    except Exception:
        return ""


# ---------- KPI 字卡（美學設計：分組標題、統一卡片、間距與層次） ----------
_inject_kpi_style()
st.markdown("#### 關鍵績效")

# 第一列：損益概覽
st.markdown('<p class="kpi-row-label">損益概覽</p>', unsafe_allow_html=True)
row1_1, row1_2, row1_3, row1_4 = st.columns(4)
with row1_1:
    cls = _pnl_color(total_pnl)
    st.markdown(f"""
    <div class="portfolio-kpi-card">
        <div class="portfolio-kpi-label">總損益</div>
        <div class="portfolio-kpi-value {cls}">{_fmt_big(total_pnl)}</div>
    </div>""", unsafe_allow_html=True)
with row1_2:
    cls = _pnl_color(realized_sum)
    st.markdown(f"""
    <div class="portfolio-kpi-card">
        <div class="portfolio-kpi-label">已實現</div>
        <div class="portfolio-kpi-value {cls}">{_fmt_big(realized_sum)}</div>
    </div>""", unsafe_allow_html=True)
with row1_3:
    cls = _pnl_color(unrealized_sum)
    st.markdown(f"""
    <div class="portfolio-kpi-card">
        <div class="portfolio-kpi-label">未實現</div>
        <div class="portfolio-kpi-value {cls}">{_fmt_big(unrealized_sum)}</div>
    </div>""", unsafe_allow_html=True)
with row1_4:
    st.markdown(f"""
    <div class="portfolio-kpi-card">
        <div class="portfolio-kpi-label">勝率（股票）</div>
        <div class="portfolio-kpi-value">{win_rate_pct:.1f}%</div>
        <div class="portfolio-kpi-sub">獲利 {win_stocks} 支 · 虧損 {loss_stocks} 支</div>
    </div>""", unsafe_allow_html=True)

# 第二列：個股表現
st.markdown('<p class="kpi-row-label">個股表現</p>', unsafe_allow_html=True)
row2_1, row2_2, row2_3, row2_4 = st.columns(4)
with row2_1:
    best_label = str(best_row["label"]) if best_row is not None else "—"
    best_val = best_row[pnl_col] if best_row is not None else 0
    cls = _pnl_color(best_val)
    st.markdown(f"""
    <div class="portfolio-kpi-card">
        <div class="portfolio-kpi-label">最佳個股</div>
        <div class="portfolio-kpi-sublabel">{best_label}</div>
        <div class="portfolio-kpi-value {cls}">{_fmt_big(best_val)}</div>
    </div>""", unsafe_allow_html=True)
with row2_2:
    st.markdown("""<div class="kpi-spacer"></div>""", unsafe_allow_html=True)
with row2_3:
    worst_label = str(worst_row["label"]) if worst_row is not None else "—"
    worst_val = worst_row[pnl_col] if worst_row is not None else 0
    cls = _pnl_color(worst_val)
    st.markdown(f"""
    <div class="portfolio-kpi-card">
        <div class="portfolio-kpi-label">最差個股</div>
        <div class="portfolio-kpi-sublabel">{worst_label}</div>
        <div class="portfolio-kpi-value {cls}">{_fmt_big(worst_val)}</div>
    </div>""", unsafe_allow_html=True)
with row2_4:
    st.markdown("""<div class="kpi-spacer"></div>""", unsafe_allow_html=True)

# 第三列：風險與單筆
st.markdown('<p class="kpi-row-label">風險與單筆</p>', unsafe_allow_html=True)
row3_1, row3_2, row3_3, row3_4 = st.columns(4)
with row3_1:
    st.markdown(f"""
    <div class="portfolio-kpi-card">
        <div class="portfolio-kpi-label">盈虧比</div>
        <div class="portfolio-kpi-value">{profit_factor:.2f}</div>
        <div class="portfolio-kpi-sub">獲利 ÷ 虧損</div>
    </div>""", unsafe_allow_html=True)
with row3_2:
    st.markdown(f"""
    <div class="portfolio-kpi-card">
        <div class="portfolio-kpi-label">最大回撤</div>
        <div class="portfolio-kpi-value">{_fmt_big(max_dd)}</div>
    </div>""", unsafe_allow_html=True)
with row3_3:
    st.markdown(f"""
    <div class="portfolio-kpi-card">
        <div class="portfolio-kpi-label">最大單筆</div>
        <div class="portfolio-kpi-value portfolio-kpi-value--positive">盈 {_fmt_big(max_single)}</div>
        <div class="portfolio-kpi-value portfolio-kpi-value--negative">虧 {_fmt_big(min_single)}</div>
    </div>""", unsafe_allow_html=True)
with row3_4:
    st.markdown("""<div class="kpi-spacer"></div>""", unsafe_allow_html=True)

st.markdown("---")
st.markdown("<div style='margin-bottom: 1rem;'></div>", unsafe_allow_html=True)

# ---------- 計算邏輯說明（合併一份） ----------
n_buys_range = sum(len(b) for b in buys_by_stock.values())
n_sells_range = sum(len(s) for s in sells_by_stock.values())
n_stocks_realized = len(realized_by_stock)
n_stocks_position = len(position)
policy_label = "自定沖銷"
n_quote_api = sum(1 for v in quote_source_by_sid.values() if v == "API現價")
n_quote_fallback = sum(1 for v in quote_source_by_sid.values() if v == "持倉均價(無報價)")

with st.expander("📐 計算邏輯說明", expanded=False):
    st.markdown("### 「全部」區間定義（兩頁一致才可比較）")
    st.markdown("""
    - **本頁**：快速區間選「全部」時，使用 **2000-01-01 ～ 今天**，且 **已實現** 的買賣來自「全部交易」（無日期篩選）。
    - **庫存損益頁**：點「全部」按鈕時，開始日期 = **2000-01-01**、結束 = 今天；**已實現** = 該區間內賣出與區間內買進依自定沖銷配對後的淨損益。
    - 兩頁都選「全部」時，區間一致，同一檔股票的 **已實現、未實現、合計** 應相同。若曾不一致，多為持倉頁「全部」先前為 2020-01-01 起算，已改為 2000-01-01 以與本頁對齊。
    """)
    st.markdown("### 本頁 KPI 計算方式")
    st.markdown("""
    | 指標 | 計算邏輯 |
    |------|----------|
    | **總損益** | 已實現 ＋ 未實現（或依「顯示模式」只顯示其一）。 |
    | **已實現** | 區間內所有「賣出」依所選沖銷方式與買進配對，每筆配對的 **淨損益** 加總。淨損益 ＝ 價差損益 － 買進手續費（按沖銷股數比例）－ 賣出手續費 － 證交稅。 |
    | **未實現** | 用 **全部交易** 計算目前持倉，持倉成本含買進手續費；未實現 ＝ (現價 － 持倉均價) × 持倉股數。現價來自報價 API，無報價時以持倉均價代替。 |
    | **勝率（股票）** | 損益為正的股票檔數 ÷ (損益為正 ＋ 損益為負的股票檔數) × 100%。依顯示模式之損益欄位。 |
    | **最佳 / 最差個股** | 依「顯示模式」選定之損益欄位，取該欄最大值與最小值的股票。 |
    | **盈虧比** | 總獲利 ÷ 總虧損（虧損取絕對值）；無虧損時以總獲利表示。 |
    | **最大回撤** | 依「賣出日」累加已實現損益形成累積曲線，從累積高點回落的最大幅度。 |
    | **最大單筆盈/虧** | 單筆配對淨損益的最大值與最小值。 |
    """)
    st.markdown("### 程式計算流程摘要（除錯用）")
    st.markdown("""
    | 項目 | 損益總覽與投資績效 | 庫存損益 |
    |------|-------------------|------------|
    | 已實現 | 區間內交易 → 依 stock_id 分組買/賣 → 每檔 `compute_matches(buys, sells, custom_rules)` → 每筆配對 `net_pnl_for_match` 加總。**「全部」時**：區間 = 全部交易（無日期過濾）。 | `in_range = trades` 落在 start_date～end_date → 同上 per stock。**「全部」時**：start=2000-01-01、end=今天，故 in_range = 全部交易。 |
    | 未實現 | **全部交易** → 每檔持倉成本與股數 → (現價 − 均價) × 股數；現價 = API 或持倉均價。 | 同上：持倉與成本用 **全部** 交易，未實現 = (現價 − 均價) × 股數。 |
    | 沖銷 | 自定沖銷：`custom_rules` (sell_id, buy_id, qty)，`_custom_match` 依規則配對，`min(rule_qty, sl.qty, bl.qty)` 且會扣減 lot 剩餘。 | 同一套 `compute_matches` / `custom_rules`。 |
    """)
    st.markdown("**若「各股損益」圖表與「庫存損益」頁同一檔股票數字不同**：兩頁的 **已實現** 都是「該頁所選日期區間內」的已實現損益。請確認兩頁皆選「全部」（本頁快速區間選「全部」、庫存損益頁點「全部」按鈕），則區間皆為 2000-01-01 至今，數字應一致。")
    st.markdown("---")
    st.markdown("### 本次計算的動態數據")
    logic_df = pd.DataFrame([
        {"項目": "區間", "數值": f"{start_date} ～ {end_date}"},
        {"項目": "沖銷方式", "數值": policy_label},
        {"項目": "顯示模式", "數值": {"合計": "合計（已實現+未實現）", "已實現": "已實現", "未實現": "未實現"}.get(display_mode, display_mode)},
        {"項目": "區間內買進筆數", "數值": n_buys_range},
        {"項目": "區間內賣出筆數", "數值": n_sells_range},
        {"項目": "有已實現損益的股票數", "數值": n_stocks_realized},
        {"項目": "目前有持倉的股票數", "數值": n_stocks_position},
        {"項目": "總損益（本頁）", "數值": f"{_fmt_big(total_pnl)} （{pnl_col}）"},
        {"項目": "已實現加總", "數值": _fmt_big(realized_sum)},
        {"項目": "未實現加總", "數值": _fmt_big(unrealized_sum)},
        {"項目": "未實現現價來源", "數值": f"API現價 {n_quote_api} 檔、持倉均價(無報價) {n_quote_fallback} 檔"},
    ])
    st.dataframe(logic_df, use_container_width=True, hide_index=True, column_config={"項目": st.column_config.TextColumn("項目", width="medium"), "數值": st.column_config.TextColumn("數值", width="large")})
    st.markdown("---")
    st.markdown("### 未實現損益的現價來源")
    st.caption("可由此表確認每檔持倉在計算未實現時是用 **API 現價** 還是 **持倉均價（無報價時）**。")
    if quote_source_by_sid:
        source_rows = []
        for sid in sorted(quote_source_by_sid.keys()):
            p = position.get(sid, {})
            qty = p.get("qty", 0)
            cost = p.get("cost", 0)
            avg_cost = (cost / qty) if qty else 0
            m = masters.get(sid)
            label = (getattr(m, "name", None) or "") or sid if m else sid
            source_rows.append({
                "股票": f"{sid} {label}".strip() if label else sid,
                "現價來源": quote_source_by_sid[sid],
                "計算用現價": round(last_price_by_sid.get(sid, 0), 2),
                "持倉均價": round(avg_cost, 2),
                "持倉股數": qty,
                "未實現": round(unrealized_by_stock.get(sid, 0), 2),
            })
        st.dataframe(pd.DataFrame(source_rows), use_container_width=True, hide_index=True)
    else:
        st.caption("目前無持倉，無未實現現價來源資料。")

# ---------- 各股損益（由大至小） ----------
st.subheader("各股損益（由大至小）")
st.caption("此圖依 **所選日期區間** 與 **顯示模式**（合計／已實現／未實現）計算。若與「庫存損益」頁同一檔股票的數字不同，通常是 **兩頁所選的日期區間不一樣**：本頁為區間內已實現 ＋ 目前未實現；庫存損益頁的已實現也是依該頁選的區間計算。")
df_chart = df.sort_values(pnl_col, ascending=False).copy()
df_chart["label_short"] = df_chart["label"].str[:14]
y_scale = alt.Scale(paddingInner=0.25)
bar = alt.Chart(df_chart).mark_bar().encode(
    y=alt.Y("label_short:N", sort="-x", title="", scale=y_scale),
    x=alt.X(f"{pnl_col}:Q", title="損益"),
    color=alt.condition(alt.datum[pnl_col] >= 0, alt.value("#c00"), alt.value("#0d7a0d")),
)
text = alt.Chart(df_chart).mark_text(dx=8, align="left").encode(
    y=alt.Y("label_short:N", sort="-x", title="", scale=y_scale),
    x=alt.X(f"{pnl_col}:Q", title=""),
    text=alt.Text(f"{pnl_col}:Q", format=",.0f"),
)
st.altair_chart(bar + text, use_container_width=True)

# ---------- 獲利 Top5 | 虧損 Top5 ----------
st.subheader("獲利 Top5 與 虧損 Top5")
st.caption("左表：損益為正的股票，占比 = 該檔獲利 ÷ 全部獲利總和 × 100%。右表：損益為負的股票，占比 = 該檔虧損 ÷ 全部虧損總和 × 100%。")
col_top5_win, col_top5_loss = st.columns(2)
with col_top5_win:
    top5_win = df[df[pnl_col] > 0].nlargest(5, pnl_col)
    if not top5_win.empty:
        top5_win = top5_win.copy()
        wins_sum = df[df[pnl_col] > 0][pnl_col].sum()
        top5_win["占比%"] = (top5_win[pnl_col] / wins_sum * 100) if wins_sum > 0 else 0
        st.dataframe(top5_win[["label", pnl_col, "占比%"]].rename(columns={"label": "股票", pnl_col: "損益"}).style.format({"損益": "{:,.0f}", "占比%": "{:.1f}%"}), use_container_width=True, hide_index=True)
    else:
        st.caption("無獲利個股")
with col_top5_loss:
    top5_loss = df[df[pnl_col] < 0].nsmallest(5, pnl_col)
    if not top5_loss.empty:
        top5_loss = top5_loss.copy()
        loss_total = df[df[pnl_col] < 0][pnl_col].sum()
        top5_loss["占比%"] = (top5_loss[pnl_col] / loss_total * 100) if loss_total != 0 else 0
        st.dataframe(top5_loss[["label", pnl_col, "占比%"]].rename(columns={"label": "股票", pnl_col: "損益"}).style.format({"損益": "{:,.0f}", "占比%": "{:.1f}%"}), use_container_width=True, hide_index=True)
    else:
        st.caption("無虧損個股")

# ---------- 產業持股分布 ----------
st.subheader("產業持股分布")
if industry_exposure:
    total_mv = sum(industry_exposure.values())
    df_exp = pd.DataFrame([
        {"產業": k, "市值": v, "佔比%": round(v / total_mv * 100, 1) if total_mv else 0}
        for k, v in sorted(industry_exposure.items(), key=lambda x: -x[1])
    ])
    st.dataframe(df_exp.style.format({"市值": "{:,.0f}".format}), use_container_width=True, hide_index=True)
    df_pie = df_exp.copy()
    pie_chart = (
        alt.Chart(df_pie)
        .mark_arc(innerRadius=0, stroke="white", strokeWidth=1.5)
        .encode(
            theta=alt.Theta("市值:Q"),
            color=alt.Color("產業:N", legend=alt.Legend(title="產業", orient="right"), scale=alt.Scale(range=["#4a90d9", "#e85d75", "#50c878", "#9b59b6", "#f39c12"])),
            tooltip=[alt.Tooltip("產業:N", title="產業"), alt.Tooltip("佔比%:Q", title="佔比 (%)", format=".1f"), alt.Tooltip("市值:Q", title="市值", format=",.0f")],
        )
        .configure_view(strokeWidth=0)
        .configure_axis(disable=True)
        .properties(height=320)
    )
    st.altair_chart(pie_chart, use_container_width=True)
else:
    st.caption("區間內無持倉或無產業資料")

# ---------- 累積損益時間序列（已實現） ----------
st.subheader("累積損益時間序列（已實現）")
st.caption("依賣出日累加已實現損益，與沖銷方式一致。")
if cum_series:
    st.line_chart(pd.DataFrame(cum_series).set_index("date"))
else:
    st.caption("區間內尚無已實現損益")

# ---------- 產業損益柱狀 ----------
st.subheader("產業損益")
df_ind = df.groupby("industry", as_index=False)[["已實現", "未實現", "合計"]].sum()
df_ind["顯示損益"] = df_ind[pnl_col]
if not df_ind.empty:
    chart_ind = (
        alt.Chart(df_ind)
        .mark_bar(size=28)
        .encode(
            x=alt.X("industry:N", sort="-y", title=""),
            y=alt.Y("顯示損益:Q", title="損益"),
            color=alt.condition(alt.datum.顯示損益 > 0, alt.value("#c00"), alt.value("#0d7a0d")),
        )
    )
    st.altair_chart(chart_ind, use_container_width=True)
else:
    st.caption("無產業資料")

# ---------- 完整明細表 ----------
with st.expander("完整明細表（可排序、匯出 CSV）", expanded=False):
    display_df = df[["stock_id", "name", "industry", "已實現", "未實現", "合計"]].copy()
    display_df = display_df.rename(columns={"stock_id": "代號", "name": "名稱", "industry": "產業"})
    st.dataframe(display_df.sort_values("合計", ascending=False).style.format({"已實現": "{:,.2f}", "未實現": "{:,.2f}", "合計": "{:,.2f}"}), use_container_width=True, hide_index=True)
    st.download_button("匯出 CSV", data=display_df.to_csv(index=False).encode("utf-8-sig"), file_name="pnl_overview.csv", mime="text/csv")
