# -*- coding: utf-8 -*-
"""庫存損益 — 專業投資分析儀表板"""
import streamlit as st
import pandas as pd
import altair as alt
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
from sqlalchemy.exc import OperationalError
from db.database import get_session
from db.models import Trade, StockMaster, CustomMatchRule
from reports.portfolio_report import build_portfolio_df
from reports.stock_detail_report import build_stock_detail
from services.price_service import get_quote_cached, fetch_daily_prices

# ---------------------------------------------------------------------------
# 圖表視覺常數（統一 theme）
# ---------------------------------------------------------------------------
CHART_CONFIG = {
    "font": "sans-serif",
    "fontSize": 11,
    "titleFontSize": 13,
    "labelFontSize": 10,
    "axisTitleFontSize": 11,
    "background": "#f5f7fa",
    "lineColor": "#37474f",
    "lineWidth": 2.5,
    "buyColor": "#c62828",
    "sellColor": "#2e7d32",
    "refLineColor": "#78909c",
    "gridColor": "#e0e4e8",
    "heightMain": 440,
    "heightVolume": 140,
}

# ---------------------------------------------------------------------------
# 自訂 CSS：卡片、留白、圓角
# ---------------------------------------------------------------------------
def _inject_page_style():
    st.markdown("""
    <style>
    .portfolio-kpi-card {
        background: linear-gradient(145deg, #fff 0%, #f8f9fa 100%);
        border: 1px solid #e9ecef;
        border-radius: 12px;
        padding: 1rem 1.25rem;
        margin-bottom: 0.5rem;
        box-shadow: 0 1px 3px rgba(0,0,0,0.06);
    }
    .portfolio-kpi-label { font-size: 0.8rem; color: #6c757d; margin-bottom: 0.25rem; }
    .portfolio-kpi-value { font-size: 1.35rem; font-weight: 700; }
    .portfolio-kpi-value--positive { color: #c62828; }
    .portfolio-kpi-value--negative { color: #2e7d32; }
    div[data-testid="stDataFrame"] { border-radius: 8px; overflow: hidden; }
    /* 持倉表列選取：未勾選時看起來像空白（仍可點） */
    div[data-testid="stDataFrame"] input[type="checkbox"]:not(:checked) {
        opacity: 0;
    }
    </style>
    """, unsafe_allow_html=True)


def _range_active_index(start_date, end_date, today):
    """若目前區間與某快捷按鈕一致，回傳該按鈕索引 0..5（近3天、近1週、近1個月、近半年、近1年、全部），否則 -1。"""
    if end_date != today:
        return -1
    d = (today - start_date).days
    if start_date == date(2000, 1, 1):
        return 5   # 全部
    if d <= 4 and start_date == today - timedelta(days=3):
        return 0  # 近3天
    if 6 <= d <= 8 and start_date == today - timedelta(weeks=1):
        return 1  # 近1週
    if 28 <= d <= 32 and start_date == today - timedelta(days=30):
        return 2  # 近1個月
    if 178 <= d <= 182 and start_date == today - timedelta(days=180):
        return 3  # 近半年
    if 363 <= d <= 367 and start_date == today - timedelta(days=365):
        return 4  # 近1年
    return -1


def _inject_range_button_highlight(active_index):
    """當區間與快捷按鈕重合時，將該按鈕以深色高亮（第一排 6 欄為快捷按鈕）。"""
    if active_index < 0:
        return
    n = active_index + 1
    st.markdown(f"""
    <style>
    [data-testid="stHorizontalBlock"] > div:nth-child({n}) button {{
        background-color: #262730 !important;
        color: white !important;
        border-color: #262730;
    }}
    </style>
    """, unsafe_allow_html=True)


def _pnl_color(val):
    """依正負回傳 CSS class（台股：正紅、負綠）"""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return ""
    try:
        v = float(val)
        return "portfolio-kpi-value--positive" if v >= 0 else "portfolio-kpi-value--negative"
    except Exception:
        return ""


def _fmt_num(x):
    """千分位格式化"""
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "—"
    try:
        return f"{float(x):,.0f}"
    except Exception:
        return str(x)


def _fmt_pct(x):
    """百分比格式化（小數 2 位）"""
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "—"
    try:
        return f"{float(x):.2f}%"
    except Exception:
        return str(x)


def _fmt_big(val):
    """大數字改為 萬/億 顯示，避免被截斷"""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return "—"
    try:
        v = float(val)
        if abs(v) >= 1e8:
            return f"{v / 1e8:.2f}億"
        if abs(v) >= 1e4:
            return f"{v / 1e4:.2f}萬"
        return f"{v:,.0f}"
    except Exception:
        return str(val)


# ---------------------------------------------------------------------------
# KPI 摘要區
# ---------------------------------------------------------------------------
def build_portfolio_kpi_cards(df: pd.DataFrame) -> None:
    """持倉市值、未實現損益、已實現損益、總報酬率。正數紅/負數綠。"""
    if df.empty:
        st.caption("尚無持倉，無法計算 KPI。")
        return
    total_mv = df["市值"].sum()
    total_unrealized = df["未實現損益"].sum()
    total_realized = df["已實現損益"].sum()
    total_pnl = df["總損益"].sum()
    cost_basis = total_mv - total_unrealized
    total_return_pct = (total_pnl / cost_basis * 100) if cost_basis and cost_basis != 0 else 0.0

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(f"""
        <div class="portfolio-kpi-card">
            <div class="portfolio-kpi-label">持倉市值</div>
            <div class="portfolio-kpi-value">{_fmt_num(total_mv)}</div>
        </div>""", unsafe_allow_html=True)
    with c2:
        cls = _pnl_color(total_unrealized)
        st.markdown(f"""
        <div class="portfolio-kpi-card">
            <div class="portfolio-kpi-label">未實現損益</div>
            <div class="portfolio-kpi-value {cls}">{_fmt_num(total_unrealized)}</div>
        </div>""", unsafe_allow_html=True)
    with c3:
        cls = _pnl_color(total_realized)
        st.markdown(f"""
        <div class="portfolio-kpi-card">
            <div class="portfolio-kpi-label">已實現損益</div>
            <div class="portfolio-kpi-value {cls}">{_fmt_num(total_realized)}</div>
        </div>""", unsafe_allow_html=True)
    with c4:
        cls = _pnl_color(total_return_pct)
        st.markdown(f"""
        <div class="portfolio-kpi-card">
            <div class="portfolio-kpi-label">總報酬率</div>
            <div class="portfolio-kpi-value {cls}">{_fmt_pct(total_return_pct)}</div>
        </div>""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# 表格美化：千分位、損益著色、市值漸層
# ---------------------------------------------------------------------------
def style_portfolio_dataframe(df: pd.DataFrame, pnl_columns: list = None):
    """持倉表：金額千分位、損益欄正紅負綠、市值漸層。"""
    if df.empty:
        return df.style
    pnl_columns = pnl_columns or ["未實現損益", "已實現損益", "總損益"]
    format_map = {}
    for col in ["市值", "股數", "均價", "現價"] + list(pnl_columns):
        if col not in df.columns:
            continue
        if col in ["均價", "現價"]:
            format_map[col] = "{:,.2f}"
        else:
            format_map[col] = "{:,.0f}"

    sty = df.style.format(format_map, na_rep="—")

    def _color_pnl(s):
        return [
            "color: #c62828;" if v is not None and float(v) >= 0 else "color: #2e7d32;"
            for v in s
        ]

    for col in pnl_columns:
        if col in df.columns:
            sty = sty.apply(lambda s: _color_pnl(s), subset=[col])

    # 市值欄位用淺色 data bar 效果（不依賴 matplotlib）
    if "市值" in df.columns:
        try:
            s = df["市值"]
            if s.max() > s.min():
                sty = sty.bar(subset=["市值"], color="#90caf9", width=0.6, vmin=s.min(), vmax=s.max())
        except Exception:
            pass
    return sty


# ---------------------------------------------------------------------------
# 股價主圖：深藍灰線、買賣量柱+標記、最新價虛線、區間報酬
# ---------------------------------------------------------------------------
def build_stock_price_chart(
    price_df: pd.DataFrame,
    trades_df: pd.DataFrame,
    latest_price: float,
    config: dict,
    stock_return_pct: float = None,
) -> alt.Chart:
    """
    price_df: 日期, 股價
    trades_df: 日期, 買賣, 股數, 價格 (每筆交易一列)
    latest_price: 目前價，畫虛線
    stock_return_pct: 若提供，圖表標題顯示「本檔報酬（依沖銷）」此數值（僅自定沖銷）；否則顯示區間股價漲跌幅。
    """
    if price_df.empty:
        return None
    cfg = config
    price_min = price_df["股價"].min()
    price_max = price_df["股價"].max()
    price_range = max(price_max - price_min, 0.01)
    first_price = price_df["股價"].iloc[0]
    last_price = price_df["股價"].iloc[-1]
    return_pct = (last_price - first_price) / first_price * 100 if first_price else 0

    # 主線（無點標記）
    line = (
        alt.Chart(price_df)
        .mark_line(strokeWidth=cfg["lineWidth"], color=cfg["lineColor"])
        .encode(
            x=alt.X("日期:T", title="", axis=alt.Axis(format="%Y/%m/%d", labelOverlap="parity", tickCount=8)),
            y=alt.Y("股價:Q", title="股價", scale=alt.Scale(nice=True, padding=0.1)),
        )
    )

    # 最新價參考線（虛線）
    ref_df = pd.DataFrame([{"最新價": latest_price}])
    ref_line = (
        alt.Chart(ref_df)
        .mark_rule(strokeDash=[4, 2], stroke=cfg["refLineColor"], strokeWidth=1.5, opacity=0.8)
        .encode(y=alt.Y("最新價:Q"))
    )

    # 買賣：細垂直量柱 + 圓點標記
    buy_trades = trades_df[trades_df["買賣"] == "買"] if not trades_df.empty and "買賣" in trades_df.columns else pd.DataFrame()
    sell_trades = trades_df[trades_df["買賣"] == "賣"] if not trades_df.empty and "買賣" in trades_df.columns else pd.DataFrame()
    max_qty = trades_df["股數"].max() if not trades_df.empty and "股數" in trades_df.columns else 1
    scale = price_range * 0.12 / max(max_qty, 1)

    bars_buy = None
    if not buy_trades.empty:
        buy_trades = buy_trades.copy()
        buy_trades["y_base"] = buy_trades["價格"]
        buy_trades["y_top"] = buy_trades["價格"] + buy_trades["股數"] * scale
        bars_buy = (
            alt.Chart(buy_trades)
            .mark_rule(strokeWidth=3, opacity=0.75)
            .encode(
                x=alt.X("日期:T"),
                y=alt.Y("y_base:Q"),
                y2=alt.Y2("y_top:Q"),
                color=alt.value(cfg["buyColor"]),
                tooltip=[
                    alt.Tooltip("日期:T", title="日期"),
                    alt.Tooltip("買賣:N", title="買/賣"),
                    alt.Tooltip("股數:Q", title="股數", format=",.0f"),
                    alt.Tooltip("價格:Q", title="價格", format=",.2f"),
                ],
            )
        )
    bars_sell = None
    if not sell_trades.empty:
        sell_trades = sell_trades.copy()
        sell_trades["y_top"] = sell_trades["價格"]
        sell_trades["y_base"] = sell_trades["價格"] - sell_trades["股數"] * scale
        bars_sell = (
            alt.Chart(sell_trades)
            .mark_rule(strokeWidth=3, opacity=0.75)
            .encode(
                x=alt.X("日期:T"),
                y=alt.Y("y_base:Q"),
                y2=alt.Y2("y_top:Q"),
                color=alt.value(cfg["sellColor"]),
                tooltip=[
                    alt.Tooltip("日期:T", title="日期"),
                    alt.Tooltip("買賣:N", title="買/賣"),
                    alt.Tooltip("股數:Q", title="股數", format=",.0f"),
                    alt.Tooltip("價格:Q", title="價格", format=",.2f"),
                ],
            )
        )

    # 買賣點標記（三角）
    points_buy = (
        alt.Chart(buy_trades)
        .mark_point(filled=True, size=50, shape="triangle-up", color=cfg["buyColor"])
        .encode(x=alt.X("日期:T"), y=alt.Y("價格:Q"), tooltip=[alt.Tooltip("日期:T"), alt.Tooltip("價格:Q", format=",.2f"), alt.Tooltip("股數:Q", format=",.0f")])
    ) if not buy_trades.empty else None
    points_sell = (
        alt.Chart(sell_trades)
        .mark_point(filled=True, size=50, shape="triangle-down", color=cfg["sellColor"])
        .encode(x=alt.X("日期:T"), y=alt.Y("價格:Q"), tooltip=[alt.Tooltip("日期:T"), alt.Tooltip("價格:Q", format=",.2f"), alt.Tooltip("股數:Q", format=",.0f")])
    ) if not sell_trades.empty else None

    layers = [line, ref_line]
    if bars_buy is not None:
        layers.append(bars_buy)
    if bars_sell is not None:
        layers.append(bars_sell)
    if points_buy is not None:
        layers.append(points_buy)
    if points_sell is not None:
        layers.append(points_sell)

    chart = alt.layer(*layers).resolve_scale(y="shared")
    # 標題：顯示依自定沖銷計算之本檔報酬
    if stock_return_pct is not None:
        title_suffix = f" 本檔報酬（依沖銷） {stock_return_pct:+.2f}%"
        pct_for_color = stock_return_pct
    else:
        title_suffix = f" 區間股價報酬 {return_pct:+.2f}%"
        pct_for_color = return_pct
    return (
        chart
        .properties(height=cfg["heightMain"], title=alt.TitleParams(text=title_suffix, fontSize=cfg["titleFontSize"], color="#c62828" if pct_for_color >= 0 else "#2e7d32"))
        .configure_view(strokeWidth=0, fill=cfg["background"])
        .configure_axis(gridColor=cfg["gridColor"], gridOpacity=0.6, domain=False)
        .configure_axisX(labelAngle=-30)
    )


# ---------------------------------------------------------------------------
# 副圖：成交股數（買紅、賣綠）
# ---------------------------------------------------------------------------
def build_trade_volume_chart(trades_df: pd.DataFrame, config: dict) -> alt.Chart:
    """依日期彙總買進/賣出股數，買紅賣綠。"""
    if trades_df.empty or "日期" not in trades_df.columns:
        return None
    vol = trades_df.groupby(["日期", "買賣"], as_index=False).agg({"股數": "sum"})
    cfg = config
    chart = (
        alt.Chart(vol)
        .mark_bar(opacity=0.85)
        .encode(
            x=alt.X("日期:T", title="", axis=alt.Axis(format="%Y/%m/%d", labelOverlap="parity", tickCount=8)),
            y=alt.Y("股數:Q", title="股數"),
            color=alt.Color("買賣:N", scale=alt.Scale(domain=["買", "賣"], range=[cfg["buyColor"], cfg["sellColor"]]), legend=alt.Legend(title="買/賣")),
            tooltip=[alt.Tooltip("日期:T", title="日期"), alt.Tooltip("買賣:N", title="買/賣"), alt.Tooltip("股數:Q", title="股數", format=",.0f")],
        )
    )
    return (
        chart
        .properties(height=cfg["heightVolume"])
        .configure_view(strokeWidth=0, fill=cfg["background"])
        .configure_axis(gridColor=cfg["gridColor"], gridOpacity=0.5, domain=False)
        .configure_axisX(labelAngle=-30)
    )


# ---------------------------------------------------------------------------
# 圓餅圖：依市值佔比，滑鼠顯示百分佔比
# ---------------------------------------------------------------------------
def build_distribution_pie(df: pd.DataFrame, name_col: str, value_col: str = "市值", height: int = 280) -> alt.Chart:
    """df 需含 name_col 與 value_col，會自動算佔比%；tooltip 顯示佔比。"""
    if df.empty or name_col not in df.columns or value_col not in df.columns:
        return None
    total = df[value_col].sum()
    if total == 0:
        return None
    plot_df = df.copy()
    plot_df["佔比%"] = (plot_df[value_col] / total * 100).round(1)
    return (
        alt.Chart(plot_df)
        .mark_arc(innerRadius=0, stroke="white", strokeWidth=1.5)
        .encode(
            theta=alt.Theta(f"{value_col}:Q"),
            color=alt.Color(
                f"{name_col}:N",
                legend=alt.Legend(title=name_col, orient="right"),
                scale=alt.Scale(range=["#4a90d9", "#e85d75", "#50c878", "#9b59b6", "#f39c12", "#1abc9c"]),
            ),
            tooltip=[
                alt.Tooltip(f"{name_col}:N", title=name_col),
                alt.Tooltip(f"{value_col}:Q", title=value_col, format=",.0f"),
                alt.Tooltip("佔比%:Q", title="佔比 (%)", format=".1f"),
            ],
        )
        .properties(height=height)
        .configure_view(strokeWidth=0)
    )


# ===========================================================================
# 頁面主流程
# ===========================================================================
st.set_page_config(page_title="庫存損益", layout="wide")
_inject_page_style()

st.title("庫存損益")

# ----- 1. 篩選條件 -----
with st.container():
    st.markdown("#### 篩選條件")
    # 先初始化日期 key，避免 widget 同時有 default value 與 session state 的警告
    # 「全部」= 2000-01-01 至今，與「損益總覽與投資績效」頁的「全部」一致，兩頁已實現才會對齊
    # 預設區間：近半年
    today = date.today()
    if "portfolio_start" not in st.session_state:
        st.session_state["portfolio_start"] = today - timedelta(days=180)
    if "portfolio_end" not in st.session_state:
        st.session_state["portfolio_end"] = today

    # 快捷區間按鈕（一排橫向，點選後會更新下方日期；下方日期也可手動輸入）
    b1, b2, b3, b4, b5, b6 = st.columns(6)
    with b1:
        btn_3d = st.button("近3天", key="btn_3d")
    with b2:
        btn_1w = st.button("近1週", key="btn_1w")
    with b3:
        btn_1m = st.button("近1個月", key="btn_1m")
    with b4:
        btn_6m = st.button("近半年", key="btn_6m")
    with b5:
        btn_1y = st.button("近1年", key="btn_1y")
    with b6:
        btn_all = st.button("全部", key="btn_all")
    if btn_3d:
        st.session_state["portfolio_start"] = today - timedelta(days=3)
        st.session_state["portfolio_end"] = today
        st.rerun()
    if btn_1w:
        st.session_state["portfolio_start"] = today - timedelta(weeks=1)
        st.session_state["portfolio_end"] = today
        st.rerun()
    if btn_1m:
        st.session_state["portfolio_start"] = today - timedelta(days=30)
        st.session_state["portfolio_end"] = today
        st.rerun()
    if btn_6m:
        st.session_state["portfolio_start"] = today - timedelta(days=180)
        st.session_state["portfolio_end"] = today
        st.rerun()
    if btn_1y:
        st.session_state["portfolio_start"] = today - timedelta(days=365)
        st.session_state["portfolio_end"] = today
        st.rerun()
    if btn_all:
        st.session_state["portfolio_start"] = date(2000, 1, 1)
        st.session_state["portfolio_end"] = today
        st.rerun()

    # 先載入資料供篩選與報表使用
    try:
        sess = get_session()
        all_trades = sess.query(Trade).all()
        portfolio_users = sorted(set(t.user for t in all_trades))
        masters = {m.stock_id: m for m in sess.query(StockMaster).all()}
        custom_rules = [(r.sell_trade_id, r.buy_trade_id, r.matched_qty) for r in sess.query(CustomMatchRule).all()]
        sess.close()
    except OperationalError:
        st.warning("資料庫無法使用（雲端部署請在 Secrets 設定 USE_GOOGLE_SHEET、GOOGLE_SHEET_ID、GOOGLE_SHEET_CREDENTIALS_B64）。")
        st.stop()
    except Exception:
        st.stop()

    col_f1, col_f2, col_f3, col_f4 = st.columns(4)
    with col_f1:
        start_date = st.date_input("開始日期", key="portfolio_start")
    with col_f2:
        end_date = st.date_input("結束日期", key="portfolio_end")
    with col_f3:
        policy = st.selectbox(
            "損益沖銷方式",
            ["CUSTOM"],
            format_func=lambda x: "自定沖銷",
            key="portfolio_policy",
        )
    with col_f4:
        filter_user_options = ["全部"] + portfolio_users
        filter_user_idx = st.selectbox(
            "買賣人",
            range(len(filter_user_options)),
            format_func=lambda i: filter_user_options[i],
            key="portfolio_filter_user",
        )
        portfolio_filter_users = None if filter_user_idx == 0 else [filter_user_options[filter_user_idx]]
    _inject_range_button_highlight(_range_active_index(start_date, end_date, today))
    st.caption("持倉與損益皆依 **自定沖銷** 規則計算。請至「自定沖銷設定」頁設定賣出與買進的配對。")
    st.caption("**已實現損益**依上列日期區間計算；**持倉與未實現**依全部交易。點「全部」= 2000-01-01 至今，與「損益總覽與投資績效」頁一致。")

trades = [t for t in all_trades if start_date <= t.trade_date <= end_date]

df, df_industry, df_user, debug_cost = build_portfolio_df(
    all_trades, masters, start_date, end_date, policy, get_quote_cached, custom_rules=custom_rules, filter_users=portfolio_filter_users
)

if df.empty:
    st.info("目前無持倉資料，或所選區間內無交易。請調整日期或先至「交易輸入」新增交易。")
    st.stop()

# ----- 2. KPI 摘要 -----
st.markdown("---")
build_portfolio_kpi_cards(df)

# ----- 3. 持倉明細表 -----
st.markdown("---")
st.markdown("#### 📋 持倉明細")
st.caption("**持倉股數** = 該股票、該買賣人的「買進總股數 − 賣出總股數」。**自定沖銷**只影響已實現損益與成本分攤，不會減少持倉；持倉要歸零，必須在「交易輸入」中該股票的**賣出總股數 ≥ 買進總股數**。若認為已全部賣出卻仍出現持倉，請至「交易輸入」或「個股明細」確認是否漏輸賣出紀錄。")
df_display = df.drop(columns=["買進總股數", "賣出總股數"], errors="ignore") if "買進總股數" in df.columns else df
# 預設依「市值」由高到低排序（若欄位存在）
if "市值" in df_display.columns:
    try:
        df_display = df_display.sort_values(by="市值", ascending=False, kind="mergesort")
    except Exception:
        # 若因型別混合導致排序失敗，嘗試以浮點轉換後再排
        try:
            df_display = df_display.assign(_mv=df_display["市值"].astype(float)).sort_values(by="_mv", ascending=False, kind="mergesort").drop(columns=["_mv"])
        except Exception:
            pass
# 用原生 st.dataframe 呈現持倉表：可排序、保持原樣式，點選列展開明細
if "portfolio_detail_row" not in st.session_state:
    st.session_state["portfolio_detail_row"] = None
if "portfolio_table_key_v" not in st.session_state:
    st.session_state["portfolio_table_key_v"] = 0

# 先建立每列對應 (sid, name, user)，供選取列對應與下方明細使用
detail_rows = []
for _idx, row in df_display.iterrows():
    sid = row.get("股票代號") or row.get("stock_id")
    name = row.get("名稱", "")
    user = row.get("買賣人", "")
    detail_rows.append((sid, name, user))

# 簡化操作：不再佔左側空間；僅在有選取時於表格上方顯示一條工具列 + 小收合按鈕
st.caption("提示：點表格任一列即可展開下方明細")

event = st.dataframe(
    style_portfolio_dataframe(df_display),
    use_container_width=True,
    hide_index=True,
    on_select="rerun",
    selection_mode="single-row",
    key=f"portfolio_table_{st.session_state['portfolio_table_key_v']}",
)
try:
    rows = list(getattr(getattr(event, "selection", None), "rows", []) or [])
    if rows:
        st.session_state["portfolio_detail_row"] = int(rows[0])
    else:
        # 使用者取消勾選/清除選取時，下方明細也要跟著消失
        st.session_state["portfolio_detail_row"] = None
except Exception:
    pass

choice = st.session_state["portfolio_detail_row"]

# 僅在「有選擇一檔」時，下方顯示該檔的已出售＋庫存
def _detail_style_signed(val):
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return ""
    if isinstance(val, (int, float)):
        if val > 0:
            return "color: #c00; font-weight: 500;"
        if val < 0:
            return "color: #0d7a0d; font-weight: 500;"
    return ""

def _detail_fmt_num(val):
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return ""
    try:
        v = float(val)
        return f"{int(v):,}" if v == int(v) else f"{v:,.2f}"
    except (ValueError, TypeError):
        return str(val)

if choice is not None and 0 <= choice < len(detail_rows):
    sid, name, user = detail_rows[choice]
    trades_for_row = [t for t in all_trades if str(t.stock_id).strip() == str(sid).strip() and (getattr(t, "user", None) or "") == (user or "")]
    sold_df, sold_revenue, inv_df, inv_summary = build_stock_detail(sid, trades_for_row, masters, policy, custom_rules=custom_rules)
    st.markdown("---")
    h_l, h_r = st.columns([9, 1], vertical_alignment="center")
    with h_l:
        st.markdown(f"#### 📋 明細 · {sid} {str(name).strip()} · {user or '—'}")
    with h_r:
        if st.button("×", key="portfolio_detail_close", help="關閉明細並清除勾選"):
            st.session_state["portfolio_detail_row"] = None
            st.session_state["portfolio_table_key_v"] = int(st.session_state.get("portfolio_table_key_v", 0)) + 1
            st.rerun()
    st.markdown("**已出售**")
    if sold_df.empty:
        st.caption("此股票尚無已出售紀錄")
    else:
        cols_num = [c for c in sold_df.columns if sold_df[c].dtype in ("int64", "float64")]
        fmt_sold = {c: _detail_fmt_num for c in cols_num}
        style_cols = [c for c in ["單筆損益", "累計損益"] if c in sold_df.columns]
        if style_cols:
            st.dataframe(sold_df.style.format(fmt_sold).applymap(_detail_style_signed, subset=style_cols), use_container_width=True, hide_index=True)
        else:
            st.dataframe(sold_df.style.format(fmt_sold), use_container_width=True, hide_index=True)
        st.caption(f"總賣出金額：{sold_revenue:,.0f}" if sold_revenue else "0")
    st.markdown("**庫存**")
    if inv_df.empty:
        st.caption("此股票目前無庫存")
    else:
        cols_num = [c for c in inv_df.columns if inv_df[c].dtype in ("int64", "float64")]
        fmt_inv = {c: _detail_fmt_num for c in cols_num}
        style_cols = [c for c in ["單筆損益", "累計損益"] if c in inv_df.columns]
        if style_cols:
            st.dataframe(inv_df.style.format(fmt_inv).applymap(_detail_style_signed, subset=style_cols), use_container_width=True, hide_index=True)
        else:
            st.dataframe(inv_df.style.format(fmt_inv), use_container_width=True, hide_index=True)
        st.caption(f"庫存股數 {inv_summary.get('庫存股數', 0):,} · 原始成本 {inv_summary.get('原始成本', 0):,.0f} · 均價 {inv_summary.get('原始均價', 0):.2f}")

with st.expander("🔍 為何還有持倉？— 買進／賣出總股數對照", expanded=False):
    st.caption("下表為每筆持倉的 **買進總股數** 與 **賣出總股數**，持倉 = 買進 − 賣出。若賣出總股數小於買進總股數，就會有剩餘持倉。請至「交易輸入」補齊該股票、該買賣人的賣出紀錄後，持倉才會歸零。")
    if not df.empty and "買進總股數" in df.columns:
        diag = df[["買賣人", "股票代號", "名稱", "買進總股數", "賣出總股數", "股數"]].copy()
        diag = diag.rename(columns={"股數": "持倉股數"})
        st.dataframe(diag.style.format({"買進總股數": "{:,.0f}", "賣出總股數": "{:,.0f}", "持倉股數": "{:,.0f}"}, na_rep="—"), use_container_width=True, hide_index=True)
    else:
        st.caption("尚無持倉或資料未含買進／賣出總股數。")
with st.expander("🔍 持倉成本計算明細（程式內部如何算出均價）", expanded=False):
    st.caption("以下為 **程式內部** 依「全部買進成本 − 已沖銷成本 − 已沖銷之買進手續費」算出剩餘持倉成本，再 ÷ 股數 = 均價。可對照檢查是哪一項導致均價異常。")
    if debug_cost:
        if any(d.get("qty_mismatch") for d in debug_cost.values()):
            st.warning("部分股票之自定沖銷規則與實際買賣數量不一致（已沖銷總股數 ≠ 賣出總股數），持倉與均價已依實際股數推算。請至「自定沖銷設定」檢查並補齊或修正配對。")
        debug_rows = []
        for sid in sorted(debug_cost.keys()):
            d = debug_cost[sid]
            name = (masters.get(sid).name if masters.get(sid) else "") or sid
            debug_rows.append({
                "股票": f"{sid} {name}".strip(),
                "全部買進成本(含手續費)": round(d["total_buy_cost_raw"], 0),
                "已沖銷成本": round(d["matched_cost"], 0),
                "已沖銷買進手續費": round(d["matched_buy_fee"], 0),
                "剩餘持倉成本": round(d["remaining_cost"], 0),
                "持倉股數": d["position_qty"],
                "均價(=剩餘÷股數)": round(d["avg_cost"], 2),
            })
        _debug_df = pd.DataFrame(debug_rows)
        _fmt_debug = {
            "全部買進成本(含手續費)": "{:,.0f}", "已沖銷成本": "{:,.0f}", "已沖銷買進手續費": "{:,.0f}",
            "剩餘持倉成本": "{:,.0f}", "持倉股數": "{:,.0f}", "均價(=剩餘÷股數)": "{:,.2f}",
        }
        st.dataframe(_debug_df.style.format(_fmt_debug, na_rep="—"), use_container_width=True, hide_index=True)
    else:
        st.caption("尚無持倉。")
with st.expander("🔍 單一股票成本組成（每筆買進／沖銷／剩餘）", expanded=False):
    st.caption("可選一檔股票，查看 **每筆買進**、**沖銷配對**、**剩餘未沖銷的買進**。若均價異常，請看「剩餘持倉」表：是否有單筆買進單價異常高（例如 788）。")
    if debug_cost:
        opts = sorted(debug_cost.keys())
        labels = [f"{sid} {(getattr(masters.get(sid), 'name', None) or '')}".strip() for sid in opts]
        choice_idx = st.selectbox("選擇股票", range(len(opts)), format_func=lambda i: labels[i], key="debug_cost_stock")
        sid = opts[choice_idx]
        d = debug_cost[sid]
        max_buy = d.get("max_buy_price") or 0
        avg_c = d.get("avg_cost") or 0
        sum_rem = d.get("sum_remaining_from_lots")
        sum_qty_lots = d.get("sum_remaining_qty_from_lots")
        avg_from_lots = d.get("avg_cost_from_lots")
        pos_qty = d.get("position_qty") or 0
        rem_cost = d.get("remaining_cost") or 0
        remaining_fee = d.get("remaining_buy_fee") or 0
        if sum_rem is not None and sum_qty_lots is not None and avg_from_lots is not None:
            st.caption(f"**依③表加總**（未沖銷買進 股數×單價）：剩餘成本 = {sum_rem:,.0f}，股數 = {sum_qty_lots:,}，均價（不含手續費）= **{avg_from_lots:.2f}**。程式顯示剩餘成本 = {rem_cost:,.0f}（含剩餘手續費 {remaining_fee:,.0f}），股數 = {pos_qty:,}，均價 = {avg_c:.2f}。")
        st.markdown("**① 每筆買進（全部）**")
        if d.get("buys_detail"):
            _b = pd.DataFrame(d["buys_detail"]).rename(columns={"trade_id": "交易ID", "date": "日期", "qty": "股數", "price": "單價", "fee": "手續費", "cost": "成本(股數×單價+手續費)"})
            st.dataframe(_b.style.format({"股數": "{:,.0f}", "單價": "{:,.2f}", "手續費": "{:,.0f}", "成本(股數×單價+手續費)": "{:,.0f}"}, na_rep="—"), use_container_width=True, hide_index=True)
        else:
            st.caption("無買進紀錄")
        st.markdown("**② 沖銷配對（已沖銷掉的買進）**")
        if d.get("matches_detail"):
            _m = pd.DataFrame(d["matches_detail"]).rename(columns={"buy_id": "買進ID", "sell_id": "賣出ID", "matched_qty": "沖銷股數", "buy_price": "買進單價", "matched_cost": "沖銷成本"})
            st.dataframe(_m.style.format({"沖銷股數": "{:,.0f}", "買進單價": "{:,.2f}", "沖銷成本": "{:,.0f}"}, na_rep="—"), use_container_width=True, hide_index=True)
        else:
            st.caption("無沖銷")
        st.markdown("**③ 剩餘持倉（未沖銷的買進，這些構成目前均價）**")
        if d.get("remaining_lots_detail"):
            _r = pd.DataFrame(d["remaining_lots_detail"]).rename(columns={"buy_id": "買進ID", "date": "日期", "remaining_qty": "剩餘股數", "price": "單價", "remaining_cost": "剩餘成本"})
            st.dataframe(_r.style.format({"剩餘股數": "{:,.0f}", "單價": "{:,.2f}", "剩餘成本": "{:,.0f}"}, na_rep="—"), use_container_width=True, hide_index=True)
            st.caption("若上表出現單價異常（如 788），代表該筆買進資料有誤或沖銷配對未涵蓋該筆。")
        else:
            st.caption("無剩餘持倉（已全數沖銷）")
        # 一鍵複製／下載除錯文字
        st.markdown("**📋 除錯用一鍵複製**")
        lines = [f"股票：{sid} {(getattr(masters.get(sid), 'name', None) or '')}".strip()]
        if max_buy and avg_c > max_buy * 1.01:
            lines.append(f"⚠️ 異常：本檔買進最高單價為 {max_buy}，但剩餘持倉均價為 {avg_c:.2f}。均價不應高於任何一筆買進單價，可能是同一筆交易被重複計入成本（例如重複匯入或同一交易出現在多個買賣人）。已改為依交易 ID 去重計算；若仍異常請檢查資料。")
        lines.append("")
        lines.append("① 每筆買進（全部）")
        if d.get("buys_detail"):
            lines.append("交易ID\t日期\t股數\t單價\t手續費\t成本")
            for r in d["buys_detail"]:
                lines.append(f"{r['trade_id']}\t{r['date']}\t{r['qty']}\t{r['price']}\t{r['fee']}\t{round(r['cost'], 0)}")
        else:
            lines.append("無買進紀錄")
        lines.append("")
        lines.append("② 沖銷配對（已沖銷掉的買進）")
        if d.get("matches_detail"):
            lines.append("買進ID\t賣出ID\t沖銷股數\t買進單價\t沖銷成本")
            for r in d["matches_detail"]:
                lines.append(f"{r['buy_id']}\t{r['sell_id']}\t{r['matched_qty']}\t{r['buy_price']}\t{round(r['matched_cost'], 0)}")
        else:
            lines.append("無沖銷")
        lines.append("")
        lines.append("③ 剩餘持倉（未沖銷的買進，這些構成目前均價）")
        if d.get("remaining_lots_detail"):
            lines.append("買進ID\t日期\t剩餘股數\t單價\t剩餘成本")
            for r in d["remaining_lots_detail"]:
                lines.append(f"{r['buy_id']}\t{r['date']}\t{r['remaining_qty']}\t{r['price']}\t{round(r['remaining_cost'], 0)}")
        else:
            lines.append("無剩餘持倉（已全數沖銷）")
        debug_text = "\n".join(lines)
        st.code(debug_text, language=None)
        st.download_button("下載除錯文字 (.txt)", data=debug_text, file_name=f"cost_debug_{sid}.txt", mime="text/plain", key=f"download_debug_cost_{sid}")
    else:
        st.caption("尚無持倉。")
with st.expander("🔍 若某檔「均價」異常如何排查", expanded=False):
    st.markdown("""
    **均價怎麼來的**  
    均價 ＝ 剩餘持倉成本 ÷ 股數。剩餘持倉成本 ＝ 全部買進成本（含手續費）− 已沖銷掉的成本與對應手續費。

    **均價明顯偏高的可能原因**
    1. **某筆買進的「成交價」或「股數」輸入錯誤**（例如 788 誤鍵成 488 或 288，或小數點、位數錯誤）。
    2. **Excel 沖銷庫存匯入時**，該股票分頁的「股價」欄解析錯誤（例如抓到合計列或錯誤欄位）。
    3. **自定沖銷配對不當**，導致高價買單較少被沖銷，剩餘持倉多為高價單，拉高均價。

    **建議排查步驟**  
    請至 **個股明細** 選擇該股票（如 3037 欣興），在 **庫存** 表中查看每一筆買進的「股價」與「股數」；若有單筆股價明顯偏離該股歷史區間（例如超過 700），代表該筆資料有誤。可至 **交易輸入** 或 **交易匯入** 檢查並修正該筆交易，或刪除後重新輸入。
    """)

# ----- 4. 個股走勢與沖銷（依上方沖銷方式計算報酬與數據） -----
st.markdown("---")
st.markdown("#### 📈 個股走勢與沖銷")
st.caption("下方報酬與數據皆依 **自定沖銷** 計算。圖上紅/綠標記為買賣時點與股數。")

stock_options = df["股票代號"].tolist()
name_map = {m.stock_id: (m.name or m.stock_id) for m in masters.values()}
# 預設選持倉市值最大的一檔
default_idx = 0
if stock_options:
    max_mv_idx = df["市值"].idxmax()
    default_idx = df.index.get_loc(max_mv_idx) if max_mv_idx in df.index else 0
    default_idx = min(default_idx, len(stock_options) - 1)

selected_id = st.selectbox(
    "選擇股票",
    options=stock_options,
    index=default_idx,
    format_func=lambda x: f"{x}｜{name_map.get(x, '')}",
    key="portfolio_chart_stock",
)

if selected_id:
    # 本檔依目前沖銷方式計算之數據（來自持倉表）
    row = df[df["股票代號"] == selected_id]
    if not row.empty:
        r = row.iloc[0]
        cost = float(r["市值"]) - float(r["未實現損益"])
        ret_pct = (float(r["總損益"]) / cost * 100) if cost and cost != 0 else 0.0
        st.markdown(f"**本檔依「自定沖銷」之數據**")
        mv_val = float(r["市值"])
        real_val = float(r["已實現損益"])
        unreal_val = float(r["未實現損益"])
        total_val = float(r["總損益"])
        c1, c2, c3, c4, c5 = st.columns(5)
        with c1:
            st.markdown(f"""
            <div class="portfolio-kpi-card">
                <div class="portfolio-kpi-label">持倉市值</div>
                <div class="portfolio-kpi-value" style="word-break: break-all;">{_fmt_big(mv_val)}</div>
            </div>""", unsafe_allow_html=True)
        with c2:
            st.markdown(f"""
            <div class="portfolio-kpi-card">
                <div class="portfolio-kpi-label">已實現損益</div>
                <div class="portfolio-kpi-value {_pnl_color(real_val)}" style="word-break: break-all;">{_fmt_big(real_val)}</div>
            </div>""", unsafe_allow_html=True)
        with c3:
            st.markdown(f"""
            <div class="portfolio-kpi-card">
                <div class="portfolio-kpi-label">未實現損益</div>
                <div class="portfolio-kpi-value {_pnl_color(unreal_val)}" style="word-break: break-all;">{_fmt_big(unreal_val)}</div>
            </div>""", unsafe_allow_html=True)
        with c4:
            st.markdown(f"""
            <div class="portfolio-kpi-card">
                <div class="portfolio-kpi-label">總損益</div>
                <div class="portfolio-kpi-value {_pnl_color(total_val)}" style="word-break: break-all;">{_fmt_big(total_val)}</div>
            </div>""", unsafe_allow_html=True)
        with c5:
            st.markdown(f"""
            <div class="portfolio-kpi-card">
                <div class="portfolio-kpi-label">本檔報酬率</div>
                <div class="portfolio-kpi-value {_pnl_color(ret_pct)}" style="word-break: break-all;">{ret_pct:+.2f}%</div>
            </div>""", unsafe_allow_html=True)

    stock_trades = [t for t in trades if t.stock_id == selected_id]
    daily_prices = fetch_daily_prices(selected_id, start_date, end_date)
    if daily_prices:
        price_df = pd.DataFrame([{"日期": r["date"], "股價": r["close"]} for r in daily_prices])
    else:
        by_date = {}
        for t in stock_trades:
            d = str(t.trade_date)
            if d not in by_date:
                by_date[d] = []
            by_date[d].append(t.price)
        points = [(d, sum(p) / len(p)) for d, p in sorted(by_date.items())]
        if not points:
            st.warning(
                "目前無法取得此檔股票於所選區間之歷史股價。"
                "請確認已設定 FinMind API Token（主檔/設定），或改選區間內有成交的標的。"
            )
            price_df = pd.DataFrame()
        else:
            price_df = pd.DataFrame(points, columns=["日期", "股價"])

    if not price_df.empty:
        # 交易明細 df（日期、買賣、股數、價格）
        trades_for_chart = pd.DataFrame([
            {"日期": str(t.trade_date), "買賣": "買" if (t.side or "").upper() == "BUY" else "賣", "股數": t.quantity, "價格": float(t.price)}
            for t in stock_trades
        ])
        latest_price = price_df["股價"].iloc[-1]
        # 傳入本檔報酬率，圖表標題會顯示「本檔報酬（依沖銷）」並隨沖銷方式變動
        row = df[df["股票代號"] == selected_id]
        ret_pct_for_chart = None
        if not row.empty:
            r = row.iloc[0]
            cost = float(r["市值"]) - float(r["未實現損益"])
            ret_pct_for_chart = (float(r["總損益"]) / cost * 100) if cost and cost != 0 else 0.0
        main_chart = build_stock_price_chart(price_df, trades_for_chart, latest_price, CHART_CONFIG, stock_return_pct=ret_pct_for_chart)
        vol_chart = build_trade_volume_chart(trades_for_chart, CHART_CONFIG)
        if main_chart is not None:
            if vol_chart is not None:
                st.altair_chart(main_chart, use_container_width=True)
                st.altair_chart(vol_chart, use_container_width=True)
            else:
                st.altair_chart(main_chart, use_container_width=True)
        st.caption("主圖：股價走勢、紅三角為買進、綠三角為賣出；長條為該筆股數。灰虛線為區間最後收盤價。副圖：每日買進/賣出股數。")

# ----- 5. 按產業小計 -----
st.markdown("---")
st.markdown("#### 📊 按產業小計")
st.dataframe(style_portfolio_dataframe(df_industry, pnl_columns=["總損益"]), use_container_width=True, hide_index=True)
if not df_industry.empty:
    pie_industry = build_distribution_pie(df_industry, "產業", "市值")
    if pie_industry is not None:
        st.altair_chart(pie_industry, use_container_width=True)
    st.caption("滑鼠移至上圖可顯示各產業市值佔比（%）。")

# ----- 6. 按買賣人小計 -----
st.markdown("---")
st.markdown("#### 👤 按買賣人小計")
if not df_user.empty:
    st.dataframe(style_portfolio_dataframe(df_user, pnl_columns=["總損益"]), use_container_width=True, hide_index=True)
    pie_user = build_distribution_pie(df_user, "買賣人", "市值")
    if pie_user is not None:
        st.altair_chart(pie_user, use_container_width=True)
    st.caption("滑鼠移至上圖可顯示各買賣人持倉市值佔比（%）。")
else:
    st.caption("無依買賣人區分之持倉。")
