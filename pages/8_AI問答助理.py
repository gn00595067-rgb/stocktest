# -*- coding: utf-8 -*-
"""AI 問答助理 — 持倉、即時報價、財報、月營收、三大法人、盤中分K分析"""
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta

import requests
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    if hasattr(st, "secrets"):
        for _k in ("FINMIND_TOKEN", "ANTHROPIC_API_KEY"):
            if st.secrets.get(_k):
                os.environ.setdefault(_k, str(st.secrets[_k]).strip())
except Exception:
    pass

from services.stock_list_loader import ensure_google_sheet_loaded
from services.auth_service import (
    ensure_bootstrap_admin,
    login_guard,
    render_auth_sidebar,
    filter_trades_by_permission,
)
from services.position_cost import compute_position_and_cost_by_stock
from services.price_service import get_quote_cached
from db.database import get_session
from db.models import Trade, StockMaster, CustomMatchRule

ensure_google_sheet_loaded()
st.set_page_config(page_title="AI 問答助理", layout="wide")
from services.mobile_ui import inject_mobile_css
inject_mobile_css()
ensure_bootstrap_admin()
login_guard()
render_auth_sidebar()

st.title("🤖 AI 問答助理")
st.caption("整合即時報價、分K走勢、上沖下洗偵測、財報、月營收、三大法人，由 Claude AI 回答投資問題。")

_api_key  = os.environ.get("ANTHROPIC_API_KEY", "").strip()
_fm_token = os.environ.get("FINMIND_TOKEN", "").strip()

if not _api_key:
    st.error(
        "❌ 尚未設定 **ANTHROPIC_API_KEY**。\n\n"
        "請至 Streamlit Cloud → Settings → Secrets 新增：\n```\nANTHROPIC_API_KEY = \"sk-ant-...\"\n```"
    )
    st.stop()

try:
    from anthropic import Anthropic
    _client = Anthropic(api_key=_api_key)
except ImportError:
    st.error("❌ 找不到 `anthropic` 套件，請確認 requirements.txt 已加入 `anthropic`。")
    st.stop()

_HEADERS = {"User-Agent": "Mozilla/5.0"}
_FM_URL  = "https://api.finmindtrade.com/api/v4/data"
_TIMEOUT = 12


# ═══════════════════════════════════════════════════════════════
# 工具函式
# ═══════════════════════════════════════════════════════════════

def _f(v, default=0.0):
    try:
        return float(v) if v not in (None, "-", "") else default
    except (TypeError, ValueError):
        return default


def _fm_fetch(dataset: str, code: str, start: str, end: str) -> list:
    if not _fm_token:
        return []
    try:
        r = requests.get(
            _FM_URL,
            params={"dataset": dataset, "data_id": code,
                    "start_date": start, "end_date": end, "token": _fm_token},
            headers=_HEADERS, timeout=_TIMEOUT,
        )
        if r.status_code == 200:
            return r.json().get("data", [])
    except Exception:
        pass
    return []


# ─── 即時報價（TWSE MIS）────────────────────────────────────────

def _twse_price(code: str) -> dict | None:
    for ex in ("tse", "otc"):
        try:
            url = (f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
                   f"?ex_ch={ex}_{code}.tw&json=1&delay=0")
            r = requests.get(url, timeout=6, headers=_HEADERS)
            if r.status_code != 200:
                continue
            items = r.json().get("msgArray", [])
            if not items:
                continue
            d    = items[0]
            name = d.get("n", "").strip()
            if not name:
                continue

            z_raw  = d.get("z", "-")    # 即時成交價（盤中）
            pz_raw = d.get("pz", "")    # 最後成交價（盤後最準確）
            prev_f = _f(d.get("y"))     # 昨日收盤
            pz_f   = _f(pz_raw)

            is_rt = z_raw not in ("-", "", None)
            if is_rt:
                price_f    = _f(z_raw)
                price_type = "realtime"
            elif pz_f > 0:
                price_f    = pz_f
                price_type = "close"
            else:
                price_f    = prev_f
                price_type = "prev_close"

            if price_f <= 0:
                continue

            chg_pct = round((price_f - prev_f) / prev_f * 100, 2) if prev_f else 0
            return {
                "name":        name,
                "price":       price_f,
                "prev_close":  prev_f,
                "change_pct":  chg_pct,
                "open":        _f(d.get("o")),
                "high":        _f(d.get("h")),
                "low":         _f(d.get("l")),
                "volume_k":    d.get("v", "-"),
                "exchange":    ex.upper(),
                "is_realtime": is_rt,
                "price_type":  price_type,
            }
        except Exception:
            continue
    return None


# ─── FinMind 分K（盤中 / 最近交易日）────────────────────────────

def _last_trading_day() -> str:
    """取得最近交易日（週一-五），跳過週末"""
    d = datetime.now().date()
    for _ in range(7):
        if d.weekday() < 5:
            return d.strftime("%Y-%m-%d")
        d -= timedelta(days=1)
    return datetime.now().strftime("%Y-%m-%d")


@st.cache_data(ttl=60, show_spinner=False)
def _fetch_kbar(code: str) -> list[dict]:
    """抓取今天（或最近交易日）分K，回傳 list of OHLCV dict"""
    today     = datetime.now().strftime("%Y-%m-%d")
    last_day  = _last_trading_day()
    rows = _fm_fetch("TaiwanStockKBar", code, today, today)
    if not rows:
        rows = _fm_fetch("TaiwanStockKBar", code, last_day, last_day)
    return rows


def _analyze_kbar(rows: list[dict]) -> dict:
    """
    從分K分析盤中行為：
    - VWAP、振幅、上下影線、量能分佈
    - 上沖下洗 / 洗盤 / 高開低走 / 低開高走 偵測
    """
    if not rows:
        return {}

    import statistics

    rows = sorted(rows, key=lambda x: x.get("minute", ""))

    opens   = [_f(r["open"])   for r in rows]
    highs   = [_f(r["high"])   for r in rows]
    lows    = [_f(r["low"])    for r in rows]
    closes  = [_f(r["close"])  for r in rows]
    vols    = [_f(r["volume"]) for r in rows]

    day_open  = opens[0]
    day_high  = max(highs)
    day_low   = min(lows)
    day_close = closes[-1]
    total_vol = sum(vols)

    # VWAP
    vwap = (sum(closes[i] * vols[i] for i in range(len(rows))) / total_vol
            if total_vol > 0 else day_close)

    # 振幅
    amplitude_pct = round((day_high - day_low) / day_open * 100, 2) if day_open else 0

    # 上影線、下影線比例（今日完整K棒）
    body_top    = max(day_open, day_close)
    body_bottom = min(day_open, day_close)
    full_range  = day_high - day_low or 1
    upper_shadow_pct = round((day_high - body_top)    / full_range * 100, 1)
    lower_shadow_pct = round((body_bottom - day_low)  / full_range * 100, 1)

    # 上午盤 vs 下午盤量能分佈（以 11:30 為分界）
    am_vol = sum(vols[i] for i, r in enumerate(rows) if r.get("minute","") < "11:30:00")
    pm_vol = total_vol - am_vol
    am_pct = round(am_vol / total_vol * 100, 1) if total_vol else 0

    # 最大量能時段
    max_vol_row = max(rows, key=lambda r: _f(r.get("volume")))

    # 高點與低點出現時間
    hi_row = max(rows, key=lambda r: _f(r.get("high")))
    lo_row = min(rows, key=lambda r: _f(r.get("low")))

    # ── 盤中模式偵測 ──────────────────────────────────────────────
    signals = []

    # 高開低走：今日開盤高於昨收 1% 以上，收盤低於開盤
    # （此處以 day_open vs day_close 判斷，實際需搭配前一日收盤）
    if day_close < day_open * 0.99:
        signals.append("高開低走（開高收低）：開盤拉高後賣壓出現，多頭力道不足")

    # 低開高走（墊底洗盤）
    if day_close > day_open * 1.01:
        signals.append("低開高走：開盤壓低後快速拉升，可能為洗盤後買盤進場")

    # 上影線長：高點出現後明顯回落
    if upper_shadow_pct > 35:
        signals.append(f"上影線長（占全幅 {upper_shadow_pct}%）：高點 {day_high} 有明顯賣壓，主力可能在高點出貨")

    # 下影線長：低點出現後快速反彈（洗盤訊號）
    if lower_shadow_pct > 35:
        signals.append(f"下影線長（占全幅 {lower_shadow_pct}%）：低點 {day_low} 有買盤承接，可能為主力洗盤")

    # 振幅大但量縮：典型上沖下洗
    if amplitude_pct > 3 and total_vol < 1000:
        signals.append(f"振幅大（{amplitude_pct}%）但量縮：量價背離，可能為操控性洗盤")

    # 尾盤量大：尾盤一波拉升或殺盤
    last_30min = [r for r in rows if r.get("minute","") >= "13:00:00"]
    last_vol   = sum(_f(r.get("volume")) for r in last_30min)
    if total_vol > 0 and last_vol / total_vol > 0.3:
        direction = "拉升" if (_f(last_30min[-1]["close"]) > _f(last_30min[0]["open"])) else "殺盤"
        signals.append(f"尾盤量大（佔全日 {round(last_vol/total_vol*100,1)}%）：尾盤{direction}，需留意隔日走向")

    return {
        "date":             rows[0].get("date", ""),
        "day_open":         day_open,
        "day_high":         day_high,
        "day_low":          day_low,
        "day_close":        day_close,
        "total_vol":        int(total_vol),
        "vwap":             round(vwap, 2),
        "amplitude_pct":    amplitude_pct,
        "upper_shadow_pct": upper_shadow_pct,
        "lower_shadow_pct": lower_shadow_pct,
        "am_vol_pct":       am_pct,
        "max_vol_time":     max_vol_row.get("minute", "")[:5],
        "max_vol_price":    _f(max_vol_row.get("close")),
        "high_time":        hi_row.get("minute", "")[:5],
        "low_time":         lo_row.get("minute", "")[:5],
        "signals":          signals,
        "bar_count":        len(rows),
    }


def _kbar_context_text(code: str, analysis: dict) -> str:
    """將分K分析結果轉成 AI 可閱讀的文字"""
    if not analysis:
        return f"【{code} 盤中分K】：今日尚無分K資料（可能盤前或假日）"

    lines = [f"【{code} 盤中分K分析（{analysis['date']}，共{analysis['bar_count']}根分K）】"]
    lines.append(
        f"開{analysis['day_open']} 高{analysis['day_high']} 低{analysis['day_low']} 收{analysis['day_close']}  "
        f"VWAP={analysis['vwap']}  振幅={analysis['amplitude_pct']}%  總量={analysis['total_vol']:,}張"
    )
    lines.append(
        f"上影線={analysis['upper_shadow_pct']}%  下影線={analysis['lower_shadow_pct']}%  "
        f"上午量占比={analysis['am_vol_pct']}%  最大量時段={analysis['max_vol_time']}（${analysis['max_vol_price']}）"
    )
    lines.append(f"高點時間={analysis['high_time']}  低點時間={analysis['low_time']}")
    if analysis["signals"]:
        lines.append("⚡ 盤中訊號：")
        for s in analysis["signals"]:
            lines.append(f"  - {s}")
    else:
        lines.append("盤中走勢無明顯異常訊號")
    return "\n".join(lines)


# ─── 分K圖（Plotly 蠟燭圖）─────────────────────────────────────

def _render_kbar_chart(code: str, rows: list[dict], name: str = ""):
    if not rows:
        st.info("今日尚無分K資料（盤前或非交易日）")
        return
    try:
        import plotly.graph_objects as go
        import pandas as pd

        df = pd.DataFrame(rows)
        df["time"] = pd.to_datetime(df["date"] + " " + df["minute"])
        df = df.sort_values("time")

        fig = go.Figure()
        fig.add_trace(go.Candlestick(
            x=df["time"], open=df["open"], high=df["high"],
            low=df["low"], close=df["close"],
            name=f"{name or code} 分K",
            increasing_line_color="#e84040",
            decreasing_line_color="#00b050",
        ))
        fig.add_trace(go.Bar(
            x=df["time"], y=df["volume"],
            name="成交量", yaxis="y2",
            marker_color=["#e84040" if c >= o else "#00b050"
                          for c, o in zip(df["close"], df["open"])],
            opacity=0.4,
        ))

        # VWAP
        total_vol = df["volume"].sum()
        if total_vol > 0:
            df["cum_vol"]  = df["volume"].cumsum()
            df["cum_tvol"] = (df["close"] * df["volume"]).cumsum()
            df["vwap"]     = df["cum_tvol"] / df["cum_vol"]
            fig.add_trace(go.Scatter(
                x=df["time"], y=df["vwap"], name="VWAP",
                line=dict(color="orange", width=1.5, dash="dot"),
            ))

        fig.update_layout(
            title=f"{name or code} 盤中分K（{rows[0].get('date','')}）",
            yaxis=dict(title="價格", side="right"),
            yaxis2=dict(title="量", overlaying="y", side="left",
                        showgrid=False, range=[0, df["volume"].max() * 5]),
            xaxis=dict(
                rangeslider=dict(visible=False),
                rangebreaks=[
                    dict(bounds=["sat","mon"]),
                    dict(bounds=[13.5, 9], pattern="hour"),
                ],
            ),
            height=420,
            margin=dict(l=10, r=10, t=40, b=10),
            legend=dict(orientation="h", y=1.05),
            template="plotly_white",
        )
        st.plotly_chart(fig, use_container_width=True)
    except ImportError:
        st.warning("需安裝 plotly 才能顯示分K圖")


# ─── 財報 / 月營收 / 三大法人 ────────────────────────────────────

def _financial_statements(code: str) -> dict | None:
    end   = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=730)).strftime("%Y-%m-%d")
    rows  = _fm_fetch("TaiwanStockFinancialStatements", code, start, end)
    if not rows:
        return None
    by_date: dict = defaultdict(dict)
    for r in rows:
        by_date[r["date"]][r["type"]] = r["value"]
    quarters = []
    for date in sorted(by_date.keys(), reverse=True):
        d   = by_date[date]
        rev = _f(d.get("Revenue"))
        gp  = _f(d.get("GrossProfit"))
        oi  = _f(d.get("OperatingIncome"))
        eps = d.get("EPS")
        if eps is None or rev == 0:
            continue
        try:
            eps_f = float(eps)
        except (TypeError, ValueError):
            continue
        gm  = round(gp / rev * 100, 1) if rev > 0 else None
        opm = round(oi / rev * 100, 1) if rev > 0 else None
        if gm and opm and opm > gm + 2:
            opm = None
        quarters.append({"date": date, "eps": eps_f, "gm": gm, "opm": opm})
        if len(quarters) >= 5:
            break
    if not quarters:
        return None
    q   = quarters[0]
    ttm = round(sum(x["eps"] for x in quarters[:4]), 2) if len(quarters) >= 4 else None
    return {"date": q["date"], "eps": q["eps"], "gm": q["gm"], "opm": q["opm"], "ttm_eps": ttm}


def _monthly_revenue(code: str) -> dict | None:
    end   = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
    rows  = _fm_fetch("TaiwanStockMonthRevenue", code, start, end)
    if not rows:
        return None
    rows.sort(key=lambda r: (r.get("date", ""), r.get("revenue_month", "")), reverse=True)
    r = rows[0]
    return {
        "year_month": f"{r.get('revenue_year','')}/{str(r.get('revenue_month','')).zfill(2)}",
        "revenue_m":  round(_f(r.get("revenue")) / 1e6, 1),
        "yoy":        r.get("revenue_year_month_sum_year_over_year"),
        "mom":        r.get("revenue_month_over_month"),
    }


def _institutional(code: str) -> dict | None:
    end   = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
    rows  = _fm_fetch("TaiwanStockInstitutionalInvestorsBuySell", code, start, end)
    if not rows:
        return None
    rows.sort(key=lambda r: r.get("date", ""), reverse=True)
    rows   = rows[:15]
    foreign = trust = dealer = 0
    for r in rows:
        name = r.get("name", "")
        net  = (int(r.get("buy", 0) or 0) - int(r.get("sell", 0) or 0)) // 1000
        if "Foreign_Investor" in name:
            foreign += net
        elif "Investment_Trust" in name:
            trust += net
        elif "Dealer" in name:
            dealer += net
    latest_date = rows[0].get("date", "") if rows else ""
    return {"date": latest_date, "foreign": foreign, "trust": trust, "dealer": dealer}


# ─── 組合股票資料區塊 ────────────────────────────────────────────

@st.cache_data(ttl=60, show_spinner=False)
def _fetch_stock_context(code: str) -> str:
    lines = [f"【{code} 即時資料】"]

    price = _twse_price(code)
    if price:
        _lbl = {"realtime": "即時", "close": "收盤（盤後）", "prev_close": "昨收"}
        rt   = _lbl.get(price.get("price_type", ""), "盤後")
        sign = "▲" if price["change_pct"] >= 0 else "▼"
        lines.append(
            f"股票名稱：{price['name']}  股價（{rt}）：${price['price']}"
            f"（{sign}{abs(price['change_pct']):.2f}%）"
            f"  今高{price['high']} / 今低{price['low']}  {price['exchange']}"
        )
    else:
        lines.append("股價：查無資料（請確認代號）")

    fin = _financial_statements(code)
    if fin:
        lines.append(
            f"最新季報（{fin['date']}）：EPS={fin['eps']}"
            f"{'  TTM='+str(fin['ttm_eps']) if fin['ttm_eps'] else ''}"
            f"{'  毛利率='+str(fin['gm'])+'%' if fin['gm'] else ''}"
            f"{'  營益率='+str(fin['opm'])+'%' if fin['opm'] else ''}"
        )
    else:
        lines.append("季報：無法取得")

    rev = _monthly_revenue(code)
    if rev:
        lines.append(
            f"月營收（{rev['year_month']}）：{rev['revenue_m']}百萬"
            f"{'  YoY='+str(rev['yoy'])+'%' if rev['yoy'] is not None else ''}"
            f"{'  MoM='+str(rev['mom'])+'%' if rev['mom'] is not None else ''}"
        )
    else:
        lines.append("月營收：無法取得")

    chip = _institutional(code)
    if chip:
        lines.append(
            f"近5日三大法人（至{chip['date']}）："
            f"外資{chip['foreign']:+,}張  投信{chip['trust']:+,}張  自營{chip['dealer']:+,}張"
        )
    else:
        lines.append("三大法人：無法取得")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# 持倉讀取
# ═══════════════════════════════════════════════════════════════

@st.cache_data(ttl=180, show_spinner=False)
def _load_holdings() -> tuple[list[dict], dict[str, str]]:
    session = get_session()
    try:
        trades  = session.query(Trade).all()
        masters = {m.stock_id: m.name for m in session.query(StockMaster).all()}
        rules   = [(r.sell_trade_id, r.buy_trade_id, r.matched_qty)
                   for r in session.query(CustomMatchRule).all()]
    finally:
        session.close()

    filtered  = filter_trades_by_permission(trades)
    positions = compute_position_and_cost_by_stock(filtered, rules)
    name_to_code = {v: k for k, v in masters.items()}

    rows = []
    for code, pos in positions.items():
        qty = pos["qty"]
        if qty <= 0:
            continue
        avg   = pos["cost"] / qty
        quote = get_quote_cached(code)
        cur_p = quote["price"] if quote else None
        upnl  = round((cur_p - avg) / avg * 100, 1) if cur_p and avg else None
        mv    = round(cur_p * qty) if cur_p else None
        rows.append({
            "stock_id": code, "name": masters.get(code, code),
            "qty": qty, "avg_cost": round(avg, 2),
            "current_price": cur_p, "unrealized_pct": upnl, "market_value": mv,
        })
    rows.sort(key=lambda x: -(x["market_value"] or 0))
    return rows, name_to_code


def _build_system_prompt(holdings: list[dict]) -> str:
    lines = [
        "你是一位專業的台股投資助理，擁有以下即時資料可直接引用。",
        "請用繁體中文回答，數字精確，給出具體看法。",
        "若有盤中分K分析資料，請重點解讀盤中訊號（上沖下洗/洗盤/主力行為），",
        "並結合法人籌碼、財報給出當下操作建議方向。",
        "涉及未來走勢請說明為分析觀點，非投資建議。",
        "",
        "【使用者持倉】",
    ]
    if holdings:
        for h in holdings:
            p   = f"現價${h['current_price']}" if h["current_price"] else "現價未知"
            pct = f"（{h['unrealized_pct']:+.1f}%）" if h["unrealized_pct"] is not None else ""
            lines.append(f"- {h['name']}（{h['stock_id']}）{h['qty']}股 成本${h['avg_cost']} {p}{pct}")
    else:
        lines.append("- 目前無持倉")
    return "\n".join(lines)


def _detect_codes(text: str, name_to_code: dict) -> list[str]:
    codes = set(re.findall(r'\b\d{4}\b', text))
    for name, code in name_to_code.items():
        if len(name) >= 2 and name in text:
            codes.add(code)
    return list(codes)


# ═══════════════════════════════════════════════════════════════
# 主介面
# ═══════════════════════════════════════════════════════════════

with st.spinner("載入持倉..."):
    try:
        holdings, name_to_code = _load_holdings()
    except Exception as e:
        holdings, name_to_code = [], {}
        st.warning(f"持倉載入失敗：{e}")

# 側欄
with st.sidebar:
    st.markdown("---")
    st.markdown("**📋 目前持倉**")
    if holdings:
        for h in holdings:
            pct   = f"{h['unrealized_pct']:+.1f}%" if h["unrealized_pct"] is not None else "—"
            emoji = "🟢" if (h["unrealized_pct"] or 0) >= 0 else "🔴"
            st.markdown(f"{emoji} **{h['name']}** `{pct}`")
    else:
        st.caption("無持倉資料")
    if st.button("🔄 重新整理", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

# ─── 盤中個股分析區（獨立於對話）────────────────────────────────
with st.expander("📈 盤中分K走勢圖（FinMind Sponsor）", expanded=False):
    col1, col2 = st.columns([2, 1])
    with col1:
        chart_code = st.text_input("股票代號", placeholder="例如 2449", key="chart_code")
    with col2:
        st.markdown("<br>", unsafe_allow_html=True)
        show_chart = st.button("載入分K圖", use_container_width=True)

    if show_chart and chart_code.strip():
        code = chart_code.strip()
        with st.spinner(f"抓取 {code} 分K資料..."):
            kbars    = _fetch_kbar(code)
            analysis = _analyze_kbar(kbars)
            price    = _twse_price(code)
            name     = price["name"] if price else code

        _render_kbar_chart(code, kbars, name)

        if analysis:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("振幅", f"{analysis['amplitude_pct']}%")
            c2.metric("VWAP", f"${analysis['vwap']}")
            c3.metric("上影線", f"{analysis['upper_shadow_pct']}%")
            c4.metric("下影線", f"{analysis['lower_shadow_pct']}%")

            c5, c6 = st.columns(2)
            c5.metric("上午量占比", f"{analysis['am_vol_pct']}%")
            c6.metric("最大量時段", f"{analysis['max_vol_time']} (${analysis['max_vol_price']})")

            if analysis["signals"]:
                st.warning("**⚡ 盤中訊號偵測**\n\n" + "\n\n".join(f"- {s}" for s in analysis["signals"]))
            else:
                st.success("盤中走勢無明顯異常訊號")

            # 把分K分析加入 session_state，下次對話時自動附帶
            st.session_state["last_kbar_context"] = _kbar_context_text(code, analysis)
            st.caption("✅ 分K分析已加入 AI 問答上下文，直接輸入問題即可詢問。")

st.markdown("---")

# ─── 對話區 ──────────────────────────────────────────────────────
if "ai_chat_history" not in st.session_state:
    st.session_state.ai_chat_history = []

if not st.session_state.ai_chat_history:
    st.markdown("##### 💡 快速提問")
    quick_qs = [
        "目前持倉整體損益狀況如何？",
        "哪一檔虧損最多？該怎麼處理？",
        "目前持倉有哪些風險需要注意？",
        "哪幾檔表現最好？原因可能是什麼？",
    ]
    cols = st.columns(2)
    for i, q in enumerate(quick_qs):
        with cols[i % 2]:
            if st.button(q, use_container_width=True, key=f"qq_{i}"):
                st.session_state.ai_chat_history.append({"role": "user", "content": q})
                st.rerun()
    st.markdown("---")

for msg in st.session_state.ai_chat_history:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

if prompt := st.chat_input("問任何台股問題，例如：京元電子今天盤中上沖下洗是真洗盤嗎？"):
    st.session_state.ai_chat_history.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # 偵測股票代號，抓基本資料
    detected = _detect_codes(prompt, name_to_code)
    extra_blocks = []

    if detected:
        with st.spinner(f"抓取 {', '.join(detected[:3])} 資料..."):
            for code in detected[:3]:
                extra_blocks.append(_fetch_stock_context(code))
                # 也抓分K並分析
                kbars    = _fetch_kbar(code)
                analysis = _analyze_kbar(kbars)
                if analysis:
                    extra_blocks.append(_kbar_context_text(code, analysis))

    # 附帶上一次載入的分K分析（從 expander）
    last_kbar = st.session_state.get("last_kbar_context", "")
    if last_kbar and last_kbar not in extra_blocks:
        extra_blocks.insert(0, last_kbar)

    system_prompt = _build_system_prompt(holdings)
    if extra_blocks:
        system_prompt += "\n\n" + "\n\n".join(extra_blocks)

    with st.chat_message("assistant"):
        try:
            with _client.messages.stream(
                model="claude-sonnet-4-6",
                max_tokens=1800,
                system=system_prompt,
                messages=st.session_state.ai_chat_history,
            ) as stream:
                response_text = st.write_stream(stream.text_stream)
        except Exception as e:
            response_text = f"⚠️ 呼叫 AI 時發生錯誤：{e}"
            st.error(response_text)

    st.session_state.ai_chat_history.append({"role": "assistant", "content": response_text})

if st.session_state.ai_chat_history:
    st.markdown("")
    if st.button("🗑️ 清除對話", use_container_width=False):
        st.session_state.ai_chat_history = []
        st.session_state.pop("last_kbar_context", None)
        st.rerun()
