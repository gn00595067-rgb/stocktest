# -*- coding: utf-8 -*-
"""AI 問答助理 — 整合持倉、即時報價、財報、月營收、三大法人"""
import os
import re
import sys
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
ensure_bootstrap_admin()
login_guard()
render_auth_sidebar()

st.title("🤖 AI 問答助理")
st.caption("自動抓取即時報價、財報、月營收、三大法人資料，由 Claude AI 回答投資問題。")

_api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
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
_FM_URL   = "https://api.finmindtrade.com/api/v4/data"
_TIMEOUT  = 10


# ═══════════════════════════════════════════════════════════════
# 資料抓取函式
# ═══════════════════════════════════════════════════════════════

def _twse_price(code: str) -> dict | None:
    """TWSE MIS 即時報價（上市 → 上櫃 fallback）"""
    for ex in ("tse", "otc"):
        try:
            url = (
                f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
                f"?ex_ch={ex}_{code}.tw&json=1&delay=0"
            )
            r = requests.get(url, timeout=6, headers=_HEADERS)
            if r.status_code != 200:
                continue
            items = r.json().get("msgArray", [])
            if not items:
                continue
            d     = items[0]
            z     = d.get("z", "-")
            prev  = float(d.get("y") or 0)
            price = float(z) if z not in ("-", "") else prev
            if price <= 0:
                continue
            chg_pct = round((price - prev) / prev * 100, 2) if prev else 0
            return {
                "name":       d.get("n", code),
                "price":      price,
                "prev_close": prev,
                "change_pct": chg_pct,
                "high":       d.get("h", "-"),
                "low":        d.get("l", "-"),
                "volume_k":   d.get("v", "-"),   # 千股
                "exchange":   ex.upper(),
            }
        except Exception:
            continue
    return None


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


def _financial_statements(code: str) -> dict | None:
    """最新季度財報：EPS / 毛利率 / 營益率 / TTM EPS"""
    end   = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=730)).strftime("%Y-%m-%d")
    rows  = _fm_fetch("TaiwanStockFinancialStatements", code, start, end)
    if not rows:
        return None

    from collections import defaultdict
    by_date: dict = defaultdict(dict)
    for r in rows:
        by_date[r["date"]][r["type"]] = r["value"]

    quarters = []
    for date in sorted(by_date.keys(), reverse=True):
        d   = by_date[date]
        rev = float(d.get("Revenue") or 0)
        gp  = float(d.get("GrossProfit") or 0)
        oi  = float(d.get("OperatingIncome") or 0)
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
    """最新月營收 YoY / MoM"""
    end   = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
    rows  = _fm_fetch("TaiwanStockMonthRevenue", code, start, end)
    if not rows:
        return None
    rows.sort(key=lambda r: (r.get("date", ""), r.get("revenue_month", "")), reverse=True)
    r = rows[0]
    return {
        "year_month": f"{r.get('revenue_year', '')}/{r.get('revenue_month', ''):0>2}",
        "revenue_m":  round(float(r.get("revenue", 0)) / 1e6, 1),   # 百萬
        "yoy":        r.get("revenue_year_month_sum_year_over_year"),
        "mom":        r.get("revenue_month_over_month"),
    }


def _institutional(code: str) -> dict | None:
    """近5日三大法人買賣超合計（張）"""
    end   = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
    rows  = _fm_fetch("TaiwanStockInstitutionalInvestorsBuySell", code, start, end)
    if not rows:
        return None
    rows.sort(key=lambda r: r.get("date", ""), reverse=True)
    rows = rows[:15]   # 最近5日 × 3法人

    foreign = trust = dealer = 0
    for r in rows:
        name = r.get("name", "")
        net  = (int(r.get("buy", 0) or 0) - int(r.get("sell", 0) or 0)) // 1000
        if "Foreign_Investor" in name or "外資" in name:
            foreign += net
        elif "Investment_Trust" in name or "投信" in name:
            trust += net
        elif "Dealer" in name or "自營" in name:
            dealer += net

    latest_date = rows[0].get("date", "") if rows else ""
    return {"date": latest_date, "foreign": foreign, "trust": trust, "dealer": dealer}


@st.cache_data(ttl=120, show_spinner=False)
def _fetch_stock_context(code: str) -> str:
    """抓取單一股票的即時資料，組成文字區塊供 AI 使用"""
    lines = [f"【{code} 即時資料】"]

    price = _twse_price(code)
    if price:
        sign  = "▲" if price["change_pct"] >= 0 else "▼"
        lines.append(
            f"股價：${price['price']}（{sign}{abs(price['change_pct']):.2f}%）"
            f"  高{price['high']} / 低{price['low']}  交易所：{price['exchange']}"
        )
        lines.append(f"股票名稱（TWSE）：{price['name']}")
    else:
        lines.append("股價：TWSE 查無資料（可能代號有誤）")

    fin = _financial_statements(code)
    if fin:
        ttm_str = f"  TTM EPS={fin['ttm_eps']}" if fin["ttm_eps"] else ""
        gm_str  = f"  毛利率={fin['gm']}%" if fin["gm"] else ""
        opm_str = f"  營益率={fin['opm']}%" if fin["opm"] else ""
        lines.append(
            f"最新季報（{fin['date']}）：EPS={fin['eps']}{ttm_str}{gm_str}{opm_str}"
        )
    else:
        lines.append("季報：無法取得（FinMind Token 未設定或資料不足）")

    rev = _monthly_revenue(code)
    if rev:
        yoy_str = f"  YoY={rev['yoy']}%" if rev["yoy"] is not None else ""
        mom_str = f"  MoM={rev['mom']}%" if rev["mom"] is not None else ""
        lines.append(f"月營收（{rev['year_month']}）：{rev['revenue_m']}百萬{yoy_str}{mom_str}")
    else:
        lines.append("月營收：無法取得")

    chip = _institutional(code)
    if chip:
        lines.append(
            f"近5日三大法人（至{chip['date']}）："
            f"外資 {chip['foreign']:+,}張  投信 {chip['trust']:+,}張  自營 {chip['dealer']:+,}張"
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
        rules   = [
            (r.sell_trade_id, r.buy_trade_id, r.matched_qty)
            for r in session.query(CustomMatchRule).all()
        ]
    finally:
        session.close()

    filtered  = filter_trades_by_permission(trades)
    positions = compute_position_and_cost_by_stock(filtered, rules)
    name_to_code = {v: k for k, v in masters.items()}   # 反查：名稱 → 代號

    rows = []
    for code, pos in positions.items():
        qty = pos["qty"]
        if qty <= 0:
            continue
        avg = pos["cost"] / qty
        quote   = get_quote_cached(code)
        cur_p   = quote["price"] if quote else None
        upnl    = round((cur_p - avg) / avg * 100, 1) if cur_p and avg else None
        mv      = round(cur_p * qty) if cur_p else None
        rows.append({
            "stock_id": code, "name": masters.get(code, code),
            "qty": qty, "avg_cost": round(avg, 2),
            "current_price": cur_p, "unrealized_pct": upnl, "market_value": mv,
        })

    rows.sort(key=lambda x: -(x["market_value"] or 0))
    return rows, name_to_code


def _build_system_prompt(holdings: list[dict]) -> str:
    lines = [
        "你是一位專業的台股投資助理。",
        "請用繁體中文回答，語氣專業但親切，數字要精確，回答要有具體依據。",
        "如果使用者詢問某支股票，請根據下方注入的【即時資料】區塊給出具體分析，",
        "包含現價、財報重點、月營收趨勢、法人動向，最後給出明確的看法或建議方向。",
        "涉及未來走勢時，請說明這是分析觀點，非投資建議。",
        "",
    ]
    lines.append("【使用者持倉】")
    if holdings:
        for h in holdings:
            p   = f"現價 ${h['current_price']}" if h["current_price"] else "現價未知"
            pct = f"（損益 {h['unrealized_pct']:+.1f}%）" if h["unrealized_pct"] is not None else ""
            lines.append(f"- {h['name']}（{h['stock_id']}）{h['qty']}股 均成本${h['avg_cost']} {p}{pct}")
    else:
        lines.append("- 目前無持倉")
    return "\n".join(lines)


def _detect_codes(text: str, name_to_code: dict) -> list[str]:
    """從問題中提取股票代號（4位數字）或股票名稱"""
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

if prompt := st.chat_input("問任何台股投資問題，例如：京元電子目前值得買嗎？"):
    st.session_state.ai_chat_history.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # 偵測股票代號／名稱，抓即時資料
    detected = _detect_codes(prompt, name_to_code)
    extra_blocks = []
    if detected:
        with st.spinner(f"抓取 {', '.join(detected)} 即時資料..."):
            for code in detected[:3]:   # 最多同時查 3 檔
                block = _fetch_stock_context(code)
                extra_blocks.append(block)

    # 組合 system prompt（持倉 + 即時股票資料）
    system_prompt = _build_system_prompt(holdings)
    if extra_blocks:
        system_prompt += "\n\n" + "\n\n".join(extra_blocks)

    with st.chat_message("assistant"):
        try:
            with _client.messages.stream(
                model="claude-sonnet-4-6",
                max_tokens=1500,
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
        st.rerun()
