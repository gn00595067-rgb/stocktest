# -*- coding: utf-8 -*-
"""投資績效：區間內損益、勝率、回撤與產業分布"""
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

st.set_page_config(page_title="投資績效", layout="wide")
st.title("投資績效")

# 時間區間與沖銷方式（與 Portfolio、損益總覽一致）
today = date.today()
quick_options = {
    "本週": (today - timedelta(days=7), today),
    "近一個月": (today - timedelta(days=30), today),
    "近3月": (today - timedelta(days=90), today),
    "近6月": (today - timedelta(days=180), today),
    "今年": (date(today.year, 1, 1), today),
    "全部": (None, None),
    "自訂": ("custom", "custom"),
}
col_q, col_d1, col_d2, col_p = st.columns([1.5, 1, 1, 2])
with col_q:
    quick = st.selectbox(
        "快速區間",
        list(quick_options.keys()),
        format_func=lambda x: x,
        key="perf_quick",
    )

# 依選項計算區間，日期欄位恆顯示（非自訂時唯讀、顯示該區間）
if quick == "自訂":
    _start, _end = today - timedelta(days=365), today
else:
    _start, _end = quick_options[quick]
    if _start is None:
        _start, _end = date(2000, 1, 1), today

with col_d1:
    start_date = st.date_input("開始日期", value=_start, key=f"perf_start_{quick}", disabled=(quick != "自訂"))
with col_d2:
    end_date = st.date_input("結束日期", value=_end, key=f"perf_end_{quick}", disabled=(quick != "自訂"))

if quick != "自訂":
    start_date, end_date = _start, _end

with col_p:
    policy = st.selectbox(
        "損益沖銷方式",
        ["FIFO", "LIFO", "AVERAGE", "MINCOST", "MAXCOST", "CLOSEST", "CUSTOM"],
        index=6,
        format_func=lambda x: {
            "FIFO": "FIFO（先買先賣）",
            "LIFO": "LIFO（後買先賣）",
            "AVERAGE": "AVERAGE（均價）",
            "MINCOST": "MINCOST（樂觀）",
            "MAXCOST": "MAXCOST（保守）",
            "CLOSEST": "CLOSEST（最接近兩平）",
            "CUSTOM": "自定沖銷",
        }.get(x, x),
    )

sess = get_session()
all_trades = sess.query(Trade).all()
masters = {m.stock_id: m for m in sess.query(StockMaster).all()}
custom_rules = None
if policy == "CUSTOM":
    custom_rules = [(r.sell_trade_id, r.buy_trade_id, r.matched_qty) for r in sess.query(CustomMatchRule).all()]
sess.close()

# 只考慮區間內的交易（已實現）；持倉改為用全部交易計算（與損益總覽一致）
if quick == "全部":
    trades = all_trades
else:
    if start_date > end_date:
        start_date, end_date = end_date, start_date
    trades = [t for t in all_trades if start_date <= t.trade_date <= end_date]

trade_by_id = {t.id: t for t in all_trades}

buys_by_stock = defaultdict(list)
sells_by_stock = defaultdict(list)
for t in trades:
    lot = Lot(t.id, t.quantity, t.price, str(t.trade_date))
    if (t.side or "").upper() == "BUY":
        buys_by_stock[t.stock_id].append(lot)
    else:
        sells_by_stock[t.stock_id].append(lot)

realized_total = 0.0
match_pnls = []
matches_with_sell_date = []  # (sell_date_str, pnl) 用於累積損益時間序列
for sid, sells in sells_by_stock.items():
    buys = [Lot(b.trade_id, b.qty, b.price, b.date) for b in buys_by_stock.get(sid, [])]
    sell_lots = [Lot(s.trade_id, s.qty, s.price, s.date) for s in sells]
    for m in compute_matches(buys, sell_lots, policy, custom_rules=custom_rules if policy == "CUSTOM" else None):
        net_pnl = net_pnl_for_match(m, trade_by_id)
        realized_total += net_pnl
        match_pnls.append(net_pnl)
        buy_id, sell_id, _qty, _bp, _sp, _ = m
        sell_t = trade_by_id.get(sell_id)
        if sell_t:
            matches_with_sell_date.append((str(sell_t.trade_date), net_pnl))

# 持倉與未實現：用全部交易計算（與損益總覽一致），剩餘成本含買進手續費
buys_all_by_stock = defaultdict(list)
sells_all_by_stock = defaultdict(list)
for t in all_trades:
    lot = Lot(t.id, t.quantity, t.price, str(t.trade_date))
    if (t.side or "").upper() == "BUY":
        buys_all_by_stock[t.stock_id].append(lot)
    else:
        sells_all_by_stock[t.stock_id].append(lot)

position = defaultdict(lambda: {"qty": 0, "cost": 0.0})
for sid in set(buys_all_by_stock.keys()) | set(sells_all_by_stock.keys()):
    buys = buys_all_by_stock.get(sid, [])
    sells = sells_all_by_stock.get(sid, [])
    total_buy_cost = sum(b.qty * b.price for b in buys)
    total_buy_fee = sum(float(getattr(trade_by_id.get(b.trade_id), "fee", None) or 0) for b in buys)
    total_buy_cost_with_fee = total_buy_cost + total_buy_fee
    matches = compute_matches(buys, sells, policy, custom_rules=custom_rules if policy == "CUSTOM" else None)
    matched_cost = sum(m[2] * m[3] for m in matches)
    matched_buy_fee = 0.0
    for m in matches:
        buy_t = trade_by_id.get(m[0])
        if buy_t and getattr(buy_t, "quantity", 0):
            matched_buy_fee += float(getattr(buy_t, "fee", None) or 0) * (m[2] / buy_t.quantity)
    remaining_cost = total_buy_cost_with_fee - matched_cost - matched_buy_fee
    position_qty = sum(b.qty for b in buys) - sum(s.qty for s in sells)
    if position_qty > 0:
        position[sid]["qty"] = position_qty
        position[sid]["cost"] = remaining_cost

unrealized_total = 0.0
industry_exposure = defaultdict(float)
industry_pnl = defaultdict(float)
quote_source_by_sid = {}
last_price_by_sid = {}
unrealized_by_stock = defaultdict(float)
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
    unrealized_total += u
    unrealized_by_stock[sid] = u
    ind = (masters.get(sid).industry_name if masters.get(sid) else None) or "其他"
    industry_exposure[ind] += qty * last
    industry_pnl[ind] += u

total_pnl = realized_total + unrealized_total
win_count = sum(1 for p in match_pnls if p > 0)
total_trades = len(match_pnls)
win_rate = (win_count / total_trades * 100) if total_trades else 0
max_single = max(match_pnls) if match_pnls else 0
min_single = min(match_pnls) if match_pnls else 0

# 盈虧比（Profit Factor）：總獲利/總虧損（虧損取絕對值）
wins_sum = sum(p for p in match_pnls if p > 0)
losses_sum = abs(sum(p for p in match_pnls if p < 0))
profit_factor = (wins_sum / losses_sum) if losses_sum > 0 else (wins_sum if wins_sum > 0 else 0)

# 累積損益時間序列：依「賣出日」累加已實現損益（正確做法）
pnl_by_date = defaultdict(float)
for d, pnl in matches_with_sell_date:
    pnl_by_date[d] += pnl
dates_sorted = sorted(pnl_by_date.keys())
cum_realized = 0.0
cum_series = []
for d in dates_sorted:
    cum_realized += pnl_by_date[d]
    cum_series.append({"date": d, "cumulative_pnl": cum_realized})

# 最大回撤：從累積序列計算
max_dd = 0.0
peak = 0.0
for row in cum_series:
    peak = max(peak, row["cumulative_pnl"])
    dd = peak - row["cumulative_pnl"]
    if dd > max_dd:
        max_dd = dd


def _fmt_big(val):
    """大數字改為 萬/億 顯示，避免被截斷"""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return "—"
    v = float(val)
    if abs(v) >= 1e8:
        return f"{v/1e8:.2f}億"
    if abs(v) >= 1e4:
        return f"{v/1e4:.2f}萬"
    return f"{v:,.0f}"


def _pnl_color(val):
    """依正負回傳顏色（台股慣例：正紅、負綠）"""
    if val is None: return "#212529"
    v = float(val)
    return "#c00" if v >= 0 else "#0d7a0d"


# ---------- KPI 區塊（卡片式、數字不截斷） ----------
st.subheader("關鍵績效")
# 第一列：已實現、未實現、總損益、勝率
r1_1, r1_2, r1_3, r1_4 = st.columns(4)
with r1_1:
    st.markdown(f"""
    <div style="background: linear-gradient(135deg, #f1f8e9 0%, #dcedc8 100%); border-radius: 12px; padding: 1rem 1.25rem; border-left: 4px solid #558b2f; box-shadow: 0 1px 3px rgba(0,0,0,0.08);">
        <div style="color: #33691e; font-size: 0.85rem; margin-bottom: 0.25rem;">已實現損益</div>
        <div style="font-size: 1.5rem; font-weight: 700; color: {_pnl_color(realized_total)};">{_fmt_big(realized_total)}</div>
    </div>""", unsafe_allow_html=True)
with r1_2:
    st.markdown(f"""
    <div style="background: linear-gradient(135deg, #ffebee 0%, #ffcdd2 100%); border-radius: 12px; padding: 1rem 1.25rem; border-left: 4px solid #c62828; box-shadow: 0 1px 3px rgba(0,0,0,0.08);">
        <div style="color: #b71c1c; font-size: 0.85rem; margin-bottom: 0.25rem;">未實現損益</div>
        <div style="font-size: 1.5rem; font-weight: 700; color: {_pnl_color(unrealized_total)};">{_fmt_big(unrealized_total)}</div>
    </div>""", unsafe_allow_html=True)
with r1_3:
    st.markdown(f"""
    <div style="background: linear-gradient(135deg, #f8f9fa 0%, #e9ecef 100%); border-radius: 12px; padding: 1rem 1.25rem; border-left: 4px solid #495057; box-shadow: 0 1px 3px rgba(0,0,0,0.08);">
        <div style="color: #6c757d; font-size: 0.85rem; margin-bottom: 0.25rem;">總損益</div>
        <div style="font-size: 1.5rem; font-weight: 700; color: {_pnl_color(total_pnl)};">{_fmt_big(total_pnl)}</div>
    </div>""", unsafe_allow_html=True)
with r1_4:
    st.markdown(f"""
    <div style="background: linear-gradient(135deg, #e3f2fd 0%, #bbdefb 100%); border-radius: 12px; padding: 1rem 1.25rem; border-left: 4px solid #1565c0; box-shadow: 0 1px 3px rgba(0,0,0,0.08);">
        <div style="color: #0d47a1; font-size: 0.85rem; margin-bottom: 0.25rem;">勝率</div>
        <div style="font-size: 1.5rem; font-weight: 700; color: #212529;">{win_rate:.1f}%</div>
        <div style="font-size: 0.8rem; color: #546e7a; margin-top: 0.2rem;">獲利 {win_count} 筆 / 虧損 {total_trades - win_count} 筆</div>
    </div>""", unsafe_allow_html=True)

# 第二列：盈虧比、最大回撤、最大單筆盈/虧
r2_1, r2_2, r2_3 = st.columns(3)
with r2_1:
    st.markdown(f"""
    <div style="background: linear-gradient(135deg, #fff8e1 0%, #ffecb3 100%); border-radius: 12px; padding: 1rem 1.25rem; border-left: 4px solid #f9a825; box-shadow: 0 1px 3px rgba(0,0,0,0.08);">
        <div style="color: #f57f17; font-size: 0.85rem; margin-bottom: 0.25rem;">盈虧比</div>
        <div style="font-size: 1.5rem; font-weight: 700; color: #212529;">{profit_factor:.2f}</div>
        <div style="font-size: 0.8rem; color: #546e7a; margin-top: 0.2rem;">獲利 ÷ 虧損</div>
    </div>""", unsafe_allow_html=True)
with r2_2:
    st.markdown(f"""
    <div style="background: linear-gradient(135deg, #fce4ec 0%, #f8bbd9 100%); border-radius: 12px; padding: 1rem 1.25rem; border-left: 4px solid #ad1457; box-shadow: 0 1px 3px rgba(0,0,0,0.08);">
        <div style="color: #880e4f; font-size: 0.85rem; margin-bottom: 0.25rem;">最大回撤</div>
        <div style="font-size: 1.5rem; font-weight: 700; color: #212529;">{_fmt_big(max_dd)}</div>
    </div>""", unsafe_allow_html=True)
with r2_3:
    st.markdown(f"""
    <div style="background: linear-gradient(135deg, #e8eaf6 0%, #c5cae9 100%); border-radius: 12px; padding: 1rem 1.25rem; border-left: 4px solid #3949ab; box-shadow: 0 1px 3px rgba(0,0,0,0.08);">
        <div style="color: #283593; font-size: 0.85rem; margin-bottom: 0.25rem;">最大單筆盈/虧</div>
        <div style="font-size: 1.2rem; font-weight: 700; color: #c00;">{_fmt_big(max_single)}</div>
        <div style="font-size: 1.2rem; font-weight: 700; color: #0d7a0d;">{_fmt_big(min_single)}</div>
    </div>""", unsafe_allow_html=True)

st.markdown("---")
st.markdown("<div style='margin-bottom: 1rem;'></div>", unsafe_allow_html=True)

# ---------- 計算邏輯說明面板（與損益總覽一致） ----------
n_buys_range = sum(len(b) for b in buys_by_stock.values())
n_sells_range = sum(len(s) for s in sells_by_stock.values())
n_stocks_position = len(position)
policy_label = {"FIFO": "FIFO", "LIFO": "LIFO", "AVERAGE": "均價", "MINCOST": "MINCOST", "MAXCOST": "MAXCOST", "CLOSEST": "CLOSEST", "CUSTOM": "自定沖銷"}.get(policy, policy)
n_quote_api = sum(1 for v in quote_source_by_sid.values() if v == "API現價")
n_quote_fallback = sum(1 for v in quote_source_by_sid.values() if v == "持倉均價(無報價)")

with st.expander("📐 計算邏輯說明", expanded=False):
    st.markdown("### 本頁 KPI 計算方式")
    st.markdown("""
    | 指標 | 計算邏輯 |
    |------|----------|
    | **已實現損益** | 區間內所有「賣出」依所選沖銷方式與買進配對，每筆配對的 **淨損益** 加總。淨損益 ＝ 價差損益 － 買進手續費（按沖銷股數比例）－ 賣出手續費 － 證交稅。 |
    | **未實現損益** | 用 **全部交易** 計算目前持倉，持倉成本含買進手續費；未實現 ＝ (現價 － 持倉均價) × 持倉股數。現價來自報價 API，無報價時以持倉均價代替。 |
    | **總損益** | 已實現 ＋ 未實現。 |
    | **勝率** | 獲利筆數（淨損益＞0 的配對數）÷ 總配對筆數 × 100%。 |
    | **盈虧比** | 總獲利 ÷ 總虧損（虧損取絕對值）；無虧損時以總獲利表示。 |
    | **最大回撤** | 依「賣出日」累加已實現損益形成累積曲線，從累積高點回落的最大幅度。 |
    | **最大單筆盈/虧** | 單筆配對淨損益的最大值（盈）與最小值（虧）。 |
    """)
    st.markdown("---")
    st.markdown("### 本次計算的動態數據")
    logic_df = pd.DataFrame([
        {"項目": "區間", "數值": f"{start_date} ～ {end_date}"},
        {"項目": "沖銷方式", "數值": policy_label},
        {"項目": "區間內買進筆數", "數值": n_buys_range},
        {"項目": "區間內賣出筆數", "數值": n_sells_range},
        {"項目": "目前有持倉的股票數", "數值": n_stocks_position},
        {"項目": "總損益", "數值": _fmt_big(total_pnl)},
        {"項目": "已實現加總", "數值": _fmt_big(realized_total)},
        {"項目": "未實現加總", "數值": _fmt_big(unrealized_total)},
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
            label = (masters.get(sid).name if masters.get(sid) else "") or sid
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

# 產業持股分布：表格 + 圓餅圖（佔比）
st.subheader("產業持股分布")
if industry_exposure:
    total_mv = sum(industry_exposure.values())
    df_exp = pd.DataFrame([
        {
            "產業": k,
            "市值": v,
            "佔比%": round(v / total_mv * 100, 1) if total_mv else 0,
        }
        for k, v in sorted(industry_exposure.items(), key=lambda x: -x[1])
    ])
    st.dataframe(df_exp.style.format({"市值": "{:,.0f}".format}), use_container_width=True, hide_index=True)
    # 圓餅圖：滑鼠懸停顯示佔比（不顯示圖上文字）
    df_pie = df_exp.copy()
    pie_chart = (
        alt.Chart(df_pie)
        .mark_arc(innerRadius=0, stroke="white", strokeWidth=1.5)
        .encode(
            theta=alt.Theta("市值:Q"),
            color=alt.Color(
                "產業:N",
                legend=alt.Legend(title="產業", orient="right"),
                scale=alt.Scale(range=["#4a90d9", "#e85d75", "#50c878", "#9b59b6", "#f39c12"]),
            ),
            tooltip=[
                alt.Tooltip("產業:N", title="產業"),
                alt.Tooltip("佔比%:Q", title="佔比 (%)", format=".1f"),
                alt.Tooltip("市值:Q", title="市值", format=",.0f"),
            ],
        )
        .configure_view(strokeWidth=0)
        .configure_axis(disable=True)
        .properties(height=320)
    )
    st.altair_chart(pie_chart, use_container_width=True)
else:
    st.caption("區間內無持倉或無產業資料")

st.subheader("累積損益時間序列（已實現）")
st.caption("依賣出日累加已實現損益，與沖銷方式一致。")
if cum_series:
    df_ts = pd.DataFrame(cum_series)
    st.line_chart(df_ts.set_index("date"))
else:
    st.caption("區間內尚無已實現損益")

st.subheader("產業損益柱狀")
if industry_pnl:
    df_ind = pd.DataFrame([{"產業": k, "損益": v} for k, v in industry_pnl.items()])
    chart = (
        alt.Chart(df_ind)
        .mark_bar(size=28)
        .encode(
            x=alt.X("產業:N", sort="-y"),
            y="損益:Q",
            color=alt.condition(
                alt.datum.損益 > 0,
                alt.value("#c00"),
                alt.value("#0d7a0d"),
            ),
        )
    )
    st.altair_chart(chart, use_container_width=True)
else:
    st.caption("區間內無產業損益")
