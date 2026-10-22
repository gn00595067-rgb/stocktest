# -*- coding: utf-8 -*-
"""庫存模擬（代操扣除）：多人連動庫存合計，模擬把幾張以自訂價格劃給系統外的人後，剩下的張數與均價。
純模擬，不寫回任何資料。規格：docs/specs/庫存模擬.md
"""
import os
import sys

import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.fresh import reload_stale_modules
reload_stale_modules()   # 部署後自動重新載入過期的 services／reports（免 Reboot）
from services.stock_list_loader import ensure_google_sheet_loaded
ensure_google_sheet_loaded()
from services.auth_service import ensure_bootstrap_admin, login_guard, render_auth_sidebar, filter_trades_by_permission
from services.carveout_sim import positions_by_person, simulate
from services.data_cache import load_trades_rules_masters
from services.mobile_ui import inject_mobile_css
from services.report_ui import render_page_notes
from services.trade_fees import BASE_FEE_RATE, DEFAULT_DISCOUNT

st.set_page_config(page_title="庫存模擬", layout="wide")
inject_mobile_css()
ensure_bootstrap_admin()
login_guard()
render_auth_sidebar()
POLICY = "CUSTOM_PLUS_FIFO"   # 與庫存損益、仿Yahoo 同口徑

st.title("庫存模擬")
st.caption("純模擬，不會改到任何交易資料。")

all_trades, rules, masters = load_trades_rules_masters()
trades = filter_trades_by_permission(all_trades)
people_all = sorted({(t.user or "").strip() for t in trades if (t.user or "").strip()})
pos_all = positions_by_person(trades, people_all, rules, POLICY)
with_stock = sorted({p for per in pos_all.values() for p in per})   # 目前有庫存的人
if not with_stock:
    st.info("目前沒有任何人有庫存。")
    st.stop()

# ── 設定 ──
s1, s2, s3, s4 = st.columns([3, 1.2, 1, 1.6])
if "sim_people" not in st.session_state:
    st.session_state["sim_people"] = with_stock
st.session_state["sim_people"] = [p for p in st.session_state["sim_people"] if p in with_stock]
people = s1.multiselect("連動庫存的人", with_stock, key="sim_people",
                        help="勾選要合計庫存的人（預設＝所有目前有庫存的人）")
who = s2.text_input("模擬對象", value="王小姐", key="sim_who", help="只是畫面上的名稱")
disc = s3.number_input("手續費折數", min_value=0.0, max_value=1.0, value=DEFAULT_DISCOUNT, step=0.01,
                       format="%.2f", key="sim_disc", help="0.28＝2.8 折；模擬對象均價＝價格×(1＋0.1425%×折數)")
fee_rate = BASE_FEE_RATE * disc
deduct_mode = s4.radio("扣除口徑", ["不含手續費", "含手續費"], key="sim_mode", horizontal=True,
                       help="扣除後總成本＝連動總成本 − 模擬張數×價格（不含手續費）或 ×價格×(1＋費率)（含手續費）")
with_fee = deduct_mode == "含手續費"
if not people:
    st.warning("請至少選一個人。")
    st.stop()

pos = positions_by_person(trades, people, rules, POLICY)
sids = sorted(pos, key=lambda s: -sum(c for _, c in pos[s].values()))   # 成本大的排前面


def _name(sid):
    return f"{sid} {getattr(masters.get(sid), 'name', '') or ''}".strip()


# ── 單一表格：一邊填一邊算（Peggy 2026-10-22：輸入表、結果表合成一張）──
# 填的值存在 sim_inputs（換人、換口徑都保留本次登入期間填的值）。表格內容含計算欄，
# 每次改值後內容不同、表格會重建，所以先讀表格上一輪的修改（edited_rows）更新 sim_inputs，再算、再畫。
inputs = st.session_state.setdefault("sim_inputs", {})
ed_key = f"sim_ed_{'|'.join(people)}"
COL_Q, COL_P = f"{who}張數", f"{who}價格"
for i, ch in (st.session_state.get(ed_key) or {}).get("edited_rows", {}).items():
    if int(i) < len(sids):
        s = sids[int(i)]
        q, p = inputs.get(s, (None, None))
        q = ch.get(COL_Q, q) if COL_Q in ch else q
        p = ch.get(COL_P, p) if COL_P in ch else p
        inputs[s] = (None if q is None or pd.isna(q) else float(q), None if p is None or pd.isna(p) else float(p))

rows, warns = [], []
for s in sids:
    per = pos[s]
    tq, tc = sum(q for q, _ in per.values()), sum(c for _, c in per.values())
    sq, sp = inputs.get(s, (None, None))
    sim_shares = int(round((sq or 0) * 1000))
    r = simulate(tq, tc, sim_shares, sp, fee_rate, with_fee)
    if r["over"]:
        warns.append(f"{_name(s)}：{who} {sim_shares / 1000:g} 張超過連動庫存 {tq / 1000:g} 張。")
    if r["missing_price"]:
        warns.append(f"{_name(s)}：有填張數但沒填價格。")
    row = {"股票": _name(s), "連動張數": tq / 1000, "連動均價": r["total_avg"]}
    for p in people:
        row[p] = per.get(p, (0, 0.0))[0] / 1000
    row.update({
        COL_Q: sq, COL_P: sp,
        f"扣除{who}張數": None if r["over"] else r["left_qty"] / 1000,
        f"扣除{who}均價": r["left_avg"],
        f"{who}均價（含手續費）": r["sim_cost_avg"] if sim_shares else None,
    })
    rows.append(row)
res = pd.DataFrame(rows)

st.caption(f"✏️ 在「{COL_Q}」「{COL_P}」兩欄直接填，按 Enter 或點別格，黃色的扣除後張數／均價就會更新。")
_num = lambda label=None, fmt="localized": st.column_config.NumberColumn(label, disabled=True, format=fmt)
_hi = [f"扣除{who}張數", f"扣除{who}均價"]


# 表格的文字格式只吃 column_config（Styler 只用來上底色）；空值會顯示「None」，所以補 0（0＝不扣）
_disp = res.copy()
_numcols = [c for c in res.columns if c != "股票"]
_disp[_numcols] = _disp[_numcols].astype(float).fillna(0)
_sty = _disp.style
st.data_editor(
    _sty.set_properties(subset=_hi, **{"background-color": "#ffc000", "font-weight": "700"}),
    key=ed_key, hide_index=True, use_container_width=True, num_rows="fixed",
    height=36 * (len(res) + 1) + 8,
    column_config={
        "股票": st.column_config.TextColumn(disabled=True),
        "連動張數": _num(), "連動均價": _num("連動均價", "accounting"),
        **{p: _num() for p in people},
        COL_Q: st.column_config.NumberColumn(COL_Q, min_value=0.0, step=1.0, format="localized",
                                           help="可填零股，例如 0.5＝500 股；0＝不扣"),
        COL_P: st.column_config.NumberColumn(COL_P, min_value=0.0, step=0.05, format="accounting",
                                           help="不含手續費"),
        f"扣除{who}張數": _num(), f"扣除{who}均價": _num(None, "accounting"),
        f"{who}均價（含手續費）": _num(None, "accounting"),
    },
)
for w in warns:
    st.warning(w)

render_page_notes(
    "**連動張數／均價**：所選的人各自的庫存（自定沖銷＋其餘先進先出）加總；均價含買進手續費，與庫存損益頁同口徑。",
    f"**{who}均價（含手續費）**：價格 ×（1＋0.1425% × 折數），不計最低 1 元。",
    "**扣除口徑**：不含手續費＝連動總成本 − 張數×價格（同原本 Excel）；含手續費＝連動總成本 − 張數×價格×(1＋費率)。",
    "這頁只是試算，不會新增交易或改沖銷；重新登入後填的張數、價格會清空。",
)
