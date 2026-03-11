# -*- coding: utf-8 -*-
"""損益總覽：區間內各股盈虧排名與視覺比較"""
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

st.set_page_config(page_title="損益總覽", layout="wide")
st.title("損益總覽")

# ---------- 篩選列 ----------
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
col_q, col_d1, col_d2, col_p, col_m = st.columns([1.5, 1, 1, 1.5, 1.2])
with col_q:
    quick = st.selectbox(
        "快速區間",
        list(quick_options.keys()),
        format_func=lambda x: x,
    )

# 依選項計算區間，日期欄位恆顯示（非自訂時唯讀、顯示該區間）
if quick == "自訂":
    _start, _end = today - timedelta(days=365), today
else:
    _start, _end = quick_options[quick]
    if _start is None:
        _start, _end = date(2000, 1, 1), today

with col_d1:
    start_date = st.date_input("開始日期", value=_start, key=f"pl_start_{quick}", disabled=(quick != "自訂"))
with col_d2:
    end_date = st.date_input("結束日期", value=_end, key=f"pl_end_{quick}", disabled=(quick != "自訂"))

if quick != "自訂":
    start_date, end_date = _start, _end

with col_p:
    policy = st.selectbox(
        "沖銷方式",
        ["FIFO", "LIFO", "AVERAGE", "MINCOST", "MAXCOST", "CLOSEST", "CUSTOM"],
        index=6,
        format_func=lambda x: {
            "FIFO": "FIFO", "LIFO": "LIFO", "AVERAGE": "均價",
            "MINCOST": "MINCOST", "MAXCOST": "MAXCOST", "CLOSEST": "CLOSEST", "CUSTOM": "自定沖銷",
        }.get(x, x),
    )
with col_m:
    display_mode = st.selectbox(
        "顯示模式",
        ["合計", "已實現", "未實現"],
        format_func=lambda x: {"合計": "合計（已實現+未實現）", "已實現": "已實現", "未實現": "未實現"}.get(x, x),
    )

sess = get_session()
all_trades = sess.query(Trade).all()
masters = {m.stock_id: m for m in sess.query(StockMaster).all()}
custom_rules = None
if policy == "CUSTOM":
    custom_rules = [(r.sell_trade_id, r.buy_trade_id, r.matched_qty) for r in sess.query(CustomMatchRule).all()]
sess.close()

# 區間內交易
if quick == "全部":
    trades_in_range = all_trades
else:
    if start_date > end_date:
        start_date, end_date = end_date, start_date
    trades_in_range = [t for t in all_trades if start_date <= t.trade_date <= end_date]

# ---------- 已實現：區間內沖銷（淨損益：價差 - 買/賣手續費 - 證交稅） ----------
buys_by_stock = defaultdict(list)
sells_by_stock = defaultdict(list)
for t in trades_in_range:
    lot = Lot(t.id, t.quantity, t.price, str(t.trade_date))
    if (t.side or "").upper() == "BUY":
        buys_by_stock[t.stock_id].append(lot)
    else:
        sells_by_stock[t.stock_id].append(lot)

trade_by_id = {t.id: t for t in all_trades}
realized_by_stock = defaultdict(float)
for sid, sells in sells_by_stock.items():
    buys = [Lot(b.trade_id, b.qty, b.price, b.date) for b in buys_by_stock.get(sid, [])]
    sell_lots = [Lot(s.trade_id, s.qty, s.price, s.date) for s in sells]
    for m in compute_matches(buys, sell_lots, policy, custom_rules=custom_rules if policy == "CUSTOM" else None):
        realized_by_stock[sid] += net_pnl_for_match(m, trade_by_id)

# ---------- 持倉與未實現（用全部交易算當前部位；剩餘成本含買進手續費） ----------
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

unrealized_by_stock = defaultdict(float)
quote_source_by_sid = {}  # sid -> "API現價" | "持倉均價(無報價)"
last_price_by_sid = {}    # sid -> 計算未實現時用的現價
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
    unrealized_by_stock[sid] = (last - avg) * qty

# ---------- 合併：有已實現或未實現的股票 ----------
all_sids = set(realized_by_stock.keys()) | set(unrealized_by_stock.keys())
rows = []
for sid in all_sids:
    real = realized_by_stock.get(sid, 0)
    unreal = unrealized_by_stock.get(sid, 0)
    total = real + unreal
    m = masters.get(sid)
    name = getattr(m, "name", None) or ""
    industry = (getattr(m, "industry_name", None) or "").strip() or "其他"
    rows.append({
        "stock_id": sid,
        "name": name,
        "label": f"{sid} {name}".strip() if name else sid,
        "industry": industry,
        "已實現": round(real, 2),
        "未實現": round(unreal, 2),
        "合計": round(total, 2),
    })
df = pd.DataFrame(rows)
if df.empty:
    st.info("目前區間內無損益資料（無交易或無持倉）")
    st.stop()

# 顯示用數值欄位
pnl_col = display_mode
total_pnl = df[pnl_col].sum()
win_stocks = (df[pnl_col] > 0).sum()
loss_stocks = (df[pnl_col] < 0).sum()
total_count = win_stocks + loss_stocks
win_rate_pct = (win_stocks / total_count * 100) if total_count else 0
best_row = df.loc[df[pnl_col].idxmax()] if len(df) else None
worst_row = df.loc[df[pnl_col].idxmin()] if len(df) else None


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


# ---------- KPI 列（自訂卡片樣式，數字不截斷、版面清晰） ----------
st.subheader("KPI")
realized_sum = df["已實現"].sum()
unrealized_sum = df["未實現"].sum()

# 第一列：總損益、已實現、未實現、勝率
row1_1, row1_2, row1_3, row1_4 = st.columns(4)
with row1_1:
    st.markdown(
        f"""
        <div style="
            background: linear-gradient(135deg, #f8f9fa 0%, #e9ecef 100%);
            border-radius: 12px;
            padding: 1rem 1.25rem;
            border-left: 4px solid #495057;
            box-shadow: 0 1px 3px rgba(0,0,0,0.08);
        ">
            <div style="color: #6c757d; font-size: 0.85rem; margin-bottom: 0.25rem;">總損益</div>
            <div style="font-size: 1.5rem; font-weight: 700; color: {_pnl_color(total_pnl)}; word-break: break-all;">{_fmt_big(total_pnl)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
with row1_2:
    st.markdown(
        f"""
        <div style="
            background: linear-gradient(135deg, #f1f8e9 0%, #dcedc8 100%);
            border-radius: 12px;
            padding: 1rem 1.25rem;
            border-left: 4px solid #558b2f;
            box-shadow: 0 1px 3px rgba(0,0,0,0.08);
        ">
            <div style="color: #33691e; font-size: 0.85rem; margin-bottom: 0.25rem;">已實現</div>
            <div style="font-size: 1.5rem; font-weight: 700; color: {_pnl_color(realized_sum)};">{_fmt_big(realized_sum)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
with row1_3:
    st.markdown(
        f"""
        <div style="
            background: linear-gradient(135deg, #ffebee 0%, #ffcdd2 100%);
            border-radius: 12px;
            padding: 1rem 1.25rem;
            border-left: 4px solid #c62828;
            box-shadow: 0 1px 3px rgba(0,0,0,0.08);
        ">
            <div style="color: #b71c1c; font-size: 0.85rem; margin-bottom: 0.25rem;">未實現</div>
            <div style="font-size: 1.5rem; font-weight: 700; color: {_pnl_color(unrealized_sum)};">{_fmt_big(unrealized_sum)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
with row1_4:
    st.markdown(
        f"""
        <div style="
            background: linear-gradient(135deg, #e3f2fd 0%, #bbdefb 100%);
            border-radius: 12px;
            padding: 1rem 1.25rem;
            border-left: 4px solid #1565c0;
            box-shadow: 0 1px 3px rgba(0,0,0,0.08);
        ">
            <div style="color: #0d47a1; font-size: 0.85rem; margin-bottom: 0.25rem;">勝率</div>
            <div style="font-size: 1.5rem; font-weight: 700; color: #212529;">{win_rate_pct:.1f}%</div>
            <div style="font-size: 0.8rem; color: #546e7a; margin-top: 0.2rem;">獲利 {win_stocks} 支 · 虧損 {loss_stocks} 支</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

# 第二列：最佳個股、最差個股（較寬，數字完整）
row2_1, row2_2 = st.columns(2)
with row2_1:
    best_label = str(best_row["label"]) if best_row is not None else "—"
    best_val = best_row[pnl_col] if best_row is not None else 0
    st.markdown(
        f"""
        <div style="
            background: linear-gradient(135deg, #fff8e1 0%, #ffecb3 100%);
            border-radius: 12px;
            padding: 1rem 1.25rem;
            border-left: 4px solid #f9a825;
            box-shadow: 0 1px 3px rgba(0,0,0,0.08);
        ">
            <div style="color: #f57f17; font-size: 0.85rem; margin-bottom: 0.35rem;">最佳個股</div>
            <div style="font-size: 1.1rem; font-weight: 600; color: #212529; margin-bottom: 0.25rem;">{best_label}</div>
            <div style="font-size: 1.35rem; font-weight: 700; color: #c00;">{_fmt_big(best_val)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
with row2_2:
    worst_label = str(worst_row["label"]) if worst_row is not None else "—"
    worst_val = worst_row[pnl_col] if worst_row is not None else 0
    st.markdown(
        f"""
        <div style="
            background: linear-gradient(135deg, #fce4ec 0%, #f8bbd9 100%);
            border-radius: 12px;
            padding: 1rem 1.25rem;
            border-left: 4px solid #ad1457;
            box-shadow: 0 1px 3px rgba(0,0,0,0.08);
        ">
            <div style="color: #880e4f; font-size: 0.85rem; margin-bottom: 0.35rem;">最差個股</div>
            <div style="font-size: 1.1rem; font-weight: 600; color: #212529; margin-bottom: 0.25rem;">{worst_label}</div>
            <div style="font-size: 1.35rem; font-weight: 700; color: #0d7a0d;">{_fmt_big(worst_val)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

st.markdown("---")
st.markdown("<div style='margin-bottom: 1rem;'></div>", unsafe_allow_html=True)

# ---------- 計算邏輯說明面板（置於 KPI 下方，留間距） ----------
n_buys_range = sum(len(b) for b in buys_by_stock.values())
n_sells_range = sum(len(s) for s in sells_by_stock.values())
n_stocks_realized = len(realized_by_stock)
n_stocks_position = len(position)
policy_label = {"FIFO": "FIFO", "LIFO": "LIFO", "AVERAGE": "均價", "MINCOST": "MINCOST", "MAXCOST": "MAXCOST", "CLOSEST": "CLOSEST", "CUSTOM": "自定沖銷"}.get(policy, policy)
n_quote_api = sum(1 for v in quote_source_by_sid.values() if v == "API現價")
n_quote_fallback = sum(1 for v in quote_source_by_sid.values() if v == "持倉均價(無報價)")

with st.expander("📐 計算邏輯說明", expanded=False):
    st.markdown("### 本頁 KPI 計算方式")
    st.markdown("""
    | 指標 | 計算邏輯 |
    |------|----------|
    | **總損益** | 已實現 ＋ 未實現（或依「顯示模式」只顯示其一）。 |
    | **已實現** | 區間內所有「賣出」依所選沖銷方式與買進配對，每筆配對的 **淨損益** 加總。淨損益 ＝ 價差損益 － 買進手續費（按沖銷股數比例）－ 賣出手續費 － 證交稅。 |
    | **未實現** | 用 **全部交易** 計算目前持倉，持倉成本含買進手續費；未實現 ＝ (現價 － 持倉均價) × 持倉股數。現價來自報價 API，無報價時以持倉均價代替。 |
    | **勝率** | 損益為正的股票檔數 ÷ (損益為正 ＋ 損益為負的股票檔數) × 100%。不含損益為 0 的檔數。 |
    | **最佳 / 最差個股** | 依「顯示模式」選定之損益欄位，取該欄最大值與最小值的股票。 |
    """)
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

# ---------- 主圖：橫向條圖（各股貢獻，從大到小） ----------
st.subheader("各股損益（由大至小）")
df_chart = df.sort_values(pnl_col, ascending=False).copy()
df_chart["label_short"] = df_chart["label"].str[:14]
# Altair 橫向條：y=股票, x=損益, color 正紅負綠；paddingInner 讓長條之間有間距
y_scale = alt.Scale(paddingInner=0.25)
bar = alt.Chart(df_chart).mark_bar().encode(
    y=alt.Y("label_short:N", sort="-x", title="", scale=y_scale),
    x=alt.X(f"{pnl_col}:Q", title="損益"),
    color=alt.condition(
        alt.datum[pnl_col] >= 0,
        alt.value("#c00"),
        alt.value("#0d7a0d"),
    ),
)
text = alt.Chart(df_chart).mark_text(dx=8, align="left").encode(
    y=alt.Y("label_short:N", sort="-x", title="", scale=y_scale),
    x=alt.X(f"{pnl_col}:Q", title=""),
    text=alt.Text(f"{pnl_col}:Q", format=",.0f"),
)
st.altair_chart(bar + text, use_container_width=True)

# ---------- 獲利 Top5 | 虧損 Top5 ----------
st.subheader("獲利 Top5 與 虧損 Top5")
st.caption(
    "**占比% 說明**：左表「獲利 Top5」只顯示損益為正的股票，占比 = 該檔獲利 ÷ 全部獲利總和 × 100%（貢獻度，合計 100%）。"
    " 右表「虧損 Top5」只顯示損益為負的股票，占比 = 該檔虧損 ÷ 全部虧損總和 × 100%（占總虧損比重，合計 100%）。"
)
col_top5_win, col_top5_loss = st.columns(2)
with col_top5_win:
    # 只取「真正獲利」的股票，取前 5 名；占比% 即為占總獲利的比例（0～100%）
    top5_win = df[df[pnl_col] > 0].nlargest(5, pnl_col)
    if not top5_win.empty:
        top5_win = top5_win.copy()
        wins_sum = df[df[pnl_col] > 0][pnl_col].sum()
        top5_win["占比%"] = (top5_win[pnl_col] / wins_sum * 100) if wins_sum > 0 else 0
        st.dataframe(
            top5_win[["label", pnl_col, "占比%"]].rename(columns={"label": "股票", pnl_col: "損益"}).style.format({"損益": "{:,.0f}", "占比%": "{:.1f}%"}),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption("無獲利個股")
with col_top5_loss:
    top5_loss = df.nsmallest(5, pnl_col)
    top5_loss = top5_loss[top5_loss[pnl_col] < 0]
    if not top5_loss.empty:
        top5_loss = top5_loss.copy()
        loss_total = df[df[pnl_col] < 0][pnl_col].sum()
        top5_loss["占比%"] = (top5_loss[pnl_col] / loss_total * 100) if loss_total != 0 else 0
        st.dataframe(
            top5_loss[["label", pnl_col, "占比%"]].rename(columns={"label": "股票", pnl_col: "損益"}).style.format({"損益": "{:,.0f}", "占比%": "{:.1f}%"}),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption("無虧損個股")

# ---------- 產業損益柱狀（漸層色：紅→灰→綠） ----------
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
            color=alt.condition(
                alt.datum.顯示損益 > 0,
                alt.value("#c00"),
                alt.value("#0d7a0d"),
            ),
        )
    )
    st.altair_chart(chart_ind, use_container_width=True)
else:
    st.caption("無產業資料")

# ---------- 完整明細表（預設收起，可排序、匯出 CSV） ----------
with st.expander("完整明細表（可排序、匯出 CSV）", expanded=False):
    display_df = df[["stock_id", "name", "industry", "已實現", "未實現", "合計"]].copy()
    display_df = display_df.rename(columns={"stock_id": "代號", "name": "名稱", "industry": "產業"})
    st.dataframe(
        display_df.sort_values("合計", ascending=False).style.format({"已實現": "{:,.2f}", "未實現": "{:,.2f}", "合計": "{:,.2f}"}),
        use_container_width=True,
        hide_index=True,
    )
    csv = display_df.to_csv(index=False).encode("utf-8-sig")
    st.download_button("匯出 CSV", data=csv, file_name="pnl_overview.csv", mime="text/csv")
