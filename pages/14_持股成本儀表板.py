# -*- coding: utf-8 -*-
"""持股成本儀表板：三個帳戶的成本、市值，扣掉美美後的淨投入成本（Peggy姐 原本請 AI 每天更新的那份）。
只讀不寫。規格：docs/specs/持股成本儀表板.md
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.fresh import reload_stale_modules
reload_stale_modules()   # 部署後自動重新載入過期的 services／reports（免 Reboot）
from services.stock_list_loader import ensure_google_sheet_loaded
ensure_google_sheet_loaded()
from db.database import get_session
from services.auth_service import (ensure_bootstrap_admin, login_guard, render_auth_sidebar,
                                   filter_trades_by_permission, can_access_trader)
from services.data_cache import load_trades_rules_masters
from services.mobile_ui import inject_mobile_css
from services.price_service import get_quotes_cached, _tw_market_open
import services.group_portfolio as gp
from reports.cost_dashboard import ACCOUNTS, DEDUCT_GROUP, DEDUCT_SCOPE, OWNER, build_cost_dashboard, needed_groups

st.set_page_config(page_title="持股成本儀表板", layout="wide")
inject_mobile_css()
ensure_bootstrap_admin()
login_guard()
render_auth_sidebar()

POLICY = "CUSTOM_PLUS_FIFO"   # 同仿Yahoo


def _wan(v: float) -> str:
    """金額縮寫：1.64 億、1,879 萬"""
    return f"{v / 1e8:.2f} 億" if abs(v) >= 1e8 else f"{v / 1e4:,.0f} 萬"


def _pct(v) -> str:
    return "—" if v is None else f"{v:+.1f}%".replace("+-", "−").replace("-", "−")


def _signed(v: float) -> str:
    return f"{v:,.0f}".replace("-", "−")


all_trades, rules, masters = load_trades_rules_masters()
trades = filter_trades_by_permission(all_trades)

sess = get_session()
try:
    groups = {}
    for g in gp.list_groups(sess, OWNER, can_access=can_access_trader):
        groups.setdefault(g.name, g)   # 同名取排序最前面的
finally:
    sess.close()

pick = {n: groups.get(n) for n in needed_groups()}
g_trades = {n: gp.filter_trades(trades, g) for n, g in pick.items() if g}
sids = sorted({str(t.stock_id).strip() for ts in g_trades.values() for t in ts}
              | {s for g in pick.values() if g for s in g.stock_ids})
quotes = get_quotes_cached(sids, exchanges={s: getattr(masters.get(s), "exchange", None) for s in sids}) if sids else {}
summaries = {n: (gp.summarize_group(trades, g, rules, POLICY, quotes, masters, extra_stock_ids=list(g.stock_ids))
                 if g else None) for n, g in pick.items()}
d = build_cost_dashboard(summaries)
net, total, ded = d["net"], d["total"], d["deduct_total"]
_tw = datetime.now(timezone(timedelta(hours=8)))
when = f"{_tw:%Y/%m/%d %H:%M} 盤中報價" if _tw_market_open() else f"{_tw:%Y/%m/%d} 收盤"

if d["missing"]:
    st.warning(f"找不到這些仿Yahoo 分頁（{OWNER} 帳號）：{'、'.join(d['missing'])}，先當 0 算。分頁改名的話請告訴 Jonathan。")

# ─── 總覽卡 ───
ratio = max(0.0, min(100.0, net["ratio"] or 0.0))
R = 70
circ = 2 * 3.14159 * R
ring = (f'<svg viewBox="0 0 180 180" class="cd-ring"><circle cx="90" cy="90" r="{R}" fill="none" stroke="#24476e" stroke-width="16"/>'
        f'<circle cx="90" cy="90" r="{R}" fill="none" stroke="#4fb7d3" stroke-width="16" stroke-linecap="round" '
        f'stroke-dasharray="{circ * ratio / 100:.1f} {circ:.1f}" transform="rotate(-90 90 90)"/>'
        f'<text x="90" y="86" text-anchor="middle" class="cd-ring-big">{ratio:.0f}%</text>'
        f'<text x="90" y="108" text-anchor="middle" class="cd-ring-sub">市值 ÷ 成本</text>'
        f'<text x="90" y="128" text-anchor="middle" class="cd-ring-neg">{_pct(net["pct"])}</text></svg>')


def _kpi(lbl, val, neg=False):
    return f'<div class="cd-kpi"><div class="l">{lbl}</div><div class="v{" neg" if neg else ""}">{val}</div></div>'


def _bar(a):
    w = max(0.0, min(100.0, a["market_value"] / a["cost"] * 100)) if a["cost"] else 0
    tabs = "・".join(t["label"] for t in a["tabs"])
    return (f'<div class="cd-acc"><div class="t"><b>{a["account"]}</b><span>成本 {_wan(a["cost"])}</span></div>'
            f'<div class="bar"><i style="width:{w:.1f}%"></i></div>'
            f'<div class="t sm"><span>{tabs}</span><span class="neg">市值 {_wan(a["market_value"])}　{_pct(a["pct"])}</span></div></div>')


st.markdown(f"""
<style>
.cd-card{{background:linear-gradient(135deg,#0b2545 0%,#081a33 60%,#06142a 100%);color:#e8f1fb;border-radius:18px;padding:1.6rem 1.8rem;margin-bottom:1.2rem}}
.cd-top{{display:flex;justify-content:space-between;gap:1rem;flex-wrap:wrap;font-size:.78rem;letter-spacing:.08em;color:#9fb4cc}}
.cd-card h2{{color:#fff;margin:.2rem 0 1rem;font-size:1.8rem}}
.cd-main{{display:flex;flex-wrap:wrap;gap:1.5rem;align-items:center;justify-content:space-between}}
.cd-big{{font-family:Georgia,serif;font-size:clamp(2.4rem,7vw,5.2rem);line-height:1.05;color:#f2f7fd;word-break:break-all}}
.cd-big small{{font-size:.35em;color:#9fb4cc;margin-left:.4rem}}
.cd-kpis{{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,8.5rem),1fr));gap:.6rem;margin-top:.8rem;max-width:42rem}}
.cd-kpi{{border:1px solid #2a4a70;border-radius:10px;padding:.5rem .8rem}}
.cd-kpi .l{{font-size:.75rem;color:#9fb4cc}} .cd-kpi .v{{font-size:1.25rem;font-weight:700;color:#fff}}
.cd-kpi .v.neg,.cd-ring-neg,.cd-acc .neg{{color:#ff7b7b;fill:#ff7b7b}}
.cd-ring{{width:min(190px,45vw)}} .cd-ring-big{{fill:#fff;font-size:30px;font-family:Georgia,serif}}
.cd-ring-sub{{fill:#9fb4cc;font-size:11px}} .cd-ring-neg{{font-size:14px;font-weight:700}}
.cd-accs{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,15rem),1fr));gap:1.2rem;border-top:1px solid #23415f;margin-top:1.2rem;padding-top:1rem}}
.cd-acc .t{{display:flex;justify-content:space-between;gap:.5rem;flex-wrap:wrap}} .cd-acc .t b{{font-size:1.05rem;color:#fff}}
.cd-acc .t span{{font-size:.8rem;color:#9fb4cc}} .cd-acc .sm span{{font-size:.78rem}}
.cd-acc .bar{{height:8px;background:#1d3555;border-radius:4px;margin:.45rem 0}} .cd-acc .bar i{{display:block;height:100%;border-radius:4px;background:linear-gradient(90deg,#2c6db5,#4fb7d3)}}
table.cd-tbl{{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums;font-size:.92rem}}
table.cd-tbl th{{color:#6b7280;font-weight:500;text-align:right;padding:.45rem .6rem;border-bottom:1px solid #e5e7eb;white-space:nowrap}}
table.cd-tbl th:nth-child(-n+2),table.cd-tbl td:nth-child(-n+2){{text-align:left}}
table.cd-tbl td{{padding:.45rem .6rem;border-bottom:1px solid #eef0f3;text-align:right;white-space:nowrap}}
table.cd-tbl tr.sub td{{background:#fff;font-weight:600}} table.cd-tbl tr.ded td{{color:#6b7280}}
table.cd-tbl tr.net td{{font-weight:700;border-top:2px solid #111827}} table.cd-tbl td.neg{{color:#b42318}}
.cd-tblwrap{{overflow-x:auto}}
</style>
<div class="cd-card">
  <div class="cd-top"><span>PORTFOLIO COST · 三帳戶</span><span>● {when}</span></div>
  <h2>持股成本儀表板</h2>
  <div class="cd-main">
    <div>
      <div style="font-size:.8rem;color:#9fb4cc">扣除{DEDUCT_GROUP}後・原始投入成本</div>
      <div class="cd-big">{net["cost"]:,.0f}<small>TWD</small></div>
      <div class="cd-kpis">{_kpi("目前市值", _wan(net["market_value"]))}{_kpi("未實現損益", _wan(net["unrealized"]).replace("-", "−"), net["unrealized"] < 0)}{_kpi("三帳戶合計成本", _wan(total["cost"]))}{_kpi(f"已扣除的{DEDUCT_GROUP}成本", _wan(ded["cost"]))}</div>
    </div>
    {ring}
  </div>
  <div class="cd-accs">{"".join(_bar(a) for a in d["accounts"])}</div>
</div>
""", unsafe_allow_html=True)

# ─── 成本從哪裡來（瀑布圖）───
st.markdown("#### 成本從哪裡來")
st.caption(f"各分頁的成本一段段疊成合計，再扣掉{DEDUCT_GROUP}的持股，就是右邊的淨投入成本。實心是現在的市值，淡色是帳面虧損。")
xs, base, solid, light, labels = [], [], [], [], []
acc = 0.0
for a in d["accounts"]:
    for t in a["tabs"]:
        xs.append(t["label"]); base.append(acc)
        solid.append(min(t["market_value"], t["cost"])); light.append(abs(t["cost"] - t["market_value"]))
        labels.append(_wan(t["cost"])); acc += t["cost"]
xs += ["合計", f"扣{DEDUCT_GROUP}", "淨投入"]
base += [0, net["cost"], 0]
solid += [min(total["market_value"], total["cost"]), 0, min(net["market_value"], net["cost"])]
light += [abs(total["cost"] - total["market_value"]), 0, abs(net["cost"] - net["market_value"])]
labels += [_wan(total["cost"]), f"扣 {_wan(ded['cost'])}", _wan(net["cost"])]
colors = ["#2b7bd6"] * (len(xs) - 3) + ["#1f4e89", "#2b7bd6", "#1c9aa8"]
fig = go.Figure()
fig.add_bar(x=xs, y=solid, base=base, marker_color=colors, name="目前市值", hovertemplate="%{x}：市值 %{y:,.0f}<extra></extra>")
# 數字標在每根最上面；放不下（手機）就自動藏起來，滑過去仍看得到
fig.add_bar(x=xs, y=light, base=[b + s for b, s in zip(base, solid)], marker_color="#c9d1db", name="帳面虧損",
            text=[lab if lab[0] != "扣" else "" for lab in labels], textposition="outside", cliponaxis=False,
            textfont=dict(color="#374151"), customdata=labels, hovertemplate="%{x}：成本 %{customdata}<extra></extra>")
fig.add_bar(x=[f"扣{DEDUCT_GROUP}"], y=[ded["cost"]], base=[net["cost"]], name="扣除",
            marker=dict(color="rgba(0,0,0,0)", line=dict(color="#b42318", width=2)), showlegend=False,
            text=[f"扣 {_wan(ded['cost'])}"], textposition="outside", cliponaxis=False, textfont=dict(color="#b42318"),
            hovertemplate=f"扣{DEDUCT_GROUP}：%{{y:,.0f}}<extra></extra>")
top = max([b + s + l for b, s, l in zip(base, solid, light)] + [net["cost"] + ded["cost"]])
fig.update_layout(barmode="overlay", height=400, margin=dict(l=10, r=10, t=40, b=50), plot_bgcolor="#f3f6fa",
                  uniformtext=dict(minsize=10, mode="hide"),
                  legend=dict(orientation="h", y=1.14, x=0), bargap=0.25,
                  yaxis=dict(showticklabels=False, gridcolor="#e3e8ef", range=[0, top * 1.12]),
                  xaxis=dict(tickfont=dict(size=12)))
st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

# ─── 統計表 ───
st.markdown("#### 統計表")


def _row(cls, a, b, p):
    neg = ' class="neg"' if p["unrealized"] < 0 else ""
    return (f'<tr class="{cls}"><td>{a}</td><td>{b}</td><td>{p["market_value"]:,.0f}</td>'
            f'<td{neg}>{_signed(p["unrealized"])}</td><td>{p["cost"]:,.0f}</td><td>{_pct(p["pct"])}</td></tr>')


body = []
for a in d["accounts"]:
    body += [_row("", a["account"], t["label"], t) for t in a["tabs"]]
    if len(a["tabs"]) > 1:
        body.append(_row("sub", f'{a["account"]} 小計', "", a))
body.append(_row("sub", "三帳戶合計", "", total))
deducted = [x for x in d["deduct"] if x["deducted"]]
body += [_row("ded", f"減：{DEDUCT_GROUP}", x["name"], x) for x in deducted]
body.append(_row("sub", f"減：{DEDUCT_GROUP}小計", "", ded))
body.append(_row("net", f"扣除{DEDUCT_GROUP}後總計", "", net))
st.markdown('<div class="cd-tblwrap"><table class="cd-tbl"><tr><th>帳戶</th><th>分頁／股票</th><th>市值</th><th>未實現損益</th>'
            '<th>成本</th><th>報酬率</th></tr>' + "".join(body) + "</table></div>", unsafe_allow_html=True)

with st.expander(f"{DEDUCT_GROUP}的持股與計算方式"):
    st.markdown('<div class="cd-tblwrap"><table class="cd-tbl"><tr><th>股票</th><th style="text-align:right">股數</th><th>市值</th><th>未實現損益</th><th>是否扣除</th></tr>'
                + "".join(f'<tr><td>{x["stock_id"]} {x["name"]}</td><td style="text-align:right">{x["qty"]:,}</td>'
                          f'<td>{x["market_value"]:,.0f}</td><td class="neg">{_signed(x["unrealized"])}</td>'
                          f'<td>{"扣除" if x["deducted"] else "不在分頁中"}</td></tr>' for x in d["deduct"])
                + "</table></div>", unsafe_allow_html=True)
    acc_txt = "；".join(f"{a} 帳戶包含 {'、'.join(l for _g, l in tabs)}" for a, tabs in ACCOUNTS if len(tabs) > 1)
    st.caption(f"只有同時在「{DEDUCT_SCOPE}」分頁有持股的股票會被扣除，其他幾檔不在這些分頁中。"
               "市值與未實現損益取自仿Yahoo 各分頁的「持有股票市值」與「未實現損益（扣賣出費稅）」；"
               "成本＝市值－未實現損益，含買進手續費與預估賣出手續費、證交稅。"
               f"{acc_txt}。圓環＝市值÷成本，也就是目前還值原本投入的幾成。")
