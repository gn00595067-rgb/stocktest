# -*- coding: utf-8 -*-
"""自定沖銷設定：指定某筆賣出與某筆買進的沖銷股數，供分析頁選擇「自定沖銷」時使用。"""
import sys
import os
from collections import defaultdict

import streamlit as st
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.stock_list_loader import ensure_google_sheet_loaded
ensure_google_sheet_loaded()
from db.database import get_session
from db.models import Trade, StockMaster, CustomMatchRule
from sqlalchemy.exc import OperationalError
from services.price_service import get_quote_cached
from services.position_cost import compute_position_and_cost_by_stock

st.set_page_config(page_title="自定沖銷設定", layout="wide")
st.title("自定沖銷設定")
st.caption("可指定「某筆賣出」與「某筆買進」的沖銷股數；在 庫存損益、投資績效、個股明細等頁面選擇「自定沖銷」時會依此規則計算損益。")

sess = None
try:
    sess = get_session()
    trades = sess.query(Trade).order_by(Trade.trade_date, Trade.id).all()
    masters = {m.stock_id: m for m in sess.query(StockMaster).all()}
    rules = sess.query(CustomMatchRule).all()
    sess.close()
except OperationalError:
    if sess is not None:
        try:
            sess.close()
        except Exception:
            pass
    st.warning("資料庫無法使用（雲端部署請在 Secrets 設定 USE_GOOGLE_SHEET、GOOGLE_SHEET_ID、GOOGLE_SHEET_CREDENTIALS_B64）。")
    st.stop()
except Exception:
    if sess is not None:
        try:
            sess.close()
        except Exception:
            pass
    trades = []
    masters = {}
    rules = []

# 依交易 ID 查詢
trade_by_id = {t.id: t for t in trades}
custom_users = sorted(set(t.user for t in trades if getattr(t, "user", None)))
sells = [t for t in trades if (t.side or "").upper() == "SELL"]
buys = [t for t in trades if (t.side or "").upper() == "BUY"]

# 買賣人篩選（不影響規則儲存，僅篩選顯示的賣出/買進列表）
custom_user_opts = ["全部"] + custom_users
custom_user_idx = st.selectbox("買賣人", range(len(custom_user_opts)), format_func=lambda i: custom_user_opts[i], key="custom_filter_user")
if custom_user_idx != 0:
    filter_user = custom_user_opts[custom_user_idx]
    sells = [t for t in sells if getattr(t, "user", None) == filter_user]
    buys = [t for t in buys if getattr(t, "user", None) == filter_user]

# 每個賣出/買進已被規則占用的股數
sell_used = defaultdict(int)
buy_used = defaultdict(int)
for r in rules:
    sell_used[r.sell_trade_id] += r.matched_qty
    buy_used[r.buy_trade_id] += r.matched_qty

# ---------- 新增一筆規則 ----------
st.subheader("新增自定沖銷規則")
if not sells:
    st.warning("尚無賣出交易，無法設定沖銷。請先於「交易輸入」或「交易匯入」建立買賣資料。")
else:
    # 賣出：先選股票（可選「全部」或指定股票），再展開可排序表格 + 單選
    st.markdown("**1. 選擇「賣出」交易**")
    # 選單：篩選要針對哪隻股票（僅顯示該股票的賣出）
    stock_options = [("全部", None)]
    seen_stock = set()
    for t in sells:
        if t.stock_id not in seen_stock:
            seen_stock.add(t.stock_id)
            name = (masters.get(t.stock_id).name if masters.get(t.stock_id) else "") or ""
            stock_options.append((f"{t.stock_id} {name}".strip(), t.stock_id))
    # 有剩餘可配的股票：至少一筆賣出 剩餘可配 > 0
    stocks_with_sell_remain = {t.stock_id for t in sells if (t.quantity - sell_used[t.id]) > 0}
    filter_has_remain = st.checkbox(
        "僅顯示有剩餘配額的股票",
        value=False,
        key="add_filter_has_remain",
        help="勾選後，「選擇股票」僅列出該股票至少有一筆賣出仍可再配對股數（剩餘可配＞0）。",
    )
    if filter_has_remain:
        stock_options = [("全部", None)] + [
            (label, sid) for (label, sid) in stock_options[1:]
            if sid in stocks_with_sell_remain
        ]
    col_filter, col_sort = st.columns(2)
    with col_filter:
        filter_stock_idx = st.selectbox(
            "選擇股票",
            range(len(stock_options)),
            format_func=lambda i: stock_options[i][0],
            key="filter_stock",
            help="可先選定股票，僅顯示該股票的賣出以利操作。",
        )
    filter_stock_idx = min(filter_stock_idx, len(stock_options) - 1) if stock_options else 0
    filter_stock_id = stock_options[filter_stock_idx][1]
    sells_to_show = [t for t in sells if t.stock_id == filter_stock_id] if filter_stock_id else sells
    rows_sell = []
    for t in sells_to_show:
        used = sell_used[t.id]
        remain = max(0, t.quantity - used)
        if filter_has_remain and remain <= 0:
            continue
        name = (masters.get(t.stock_id).name if masters.get(t.stock_id) else "") or ""
        sell_price = round(float(t.price), 2) if t.price is not None else None
        rows_sell.append({
            "買賣人": getattr(t, "user", None) or "",
            "交易ID": t.id,
            "股票": f"{t.stock_id} {name}".strip(),
            "日期": str(t.trade_date),
            "賣出價格": sell_price,
            "當沖": bool(getattr(t, "is_daytrade", False)),
            "賣出股數": t.quantity,
            "已配": used,
            "剩餘可配": remain,
        })
    df_sells = pd.DataFrame(rows_sell)
    with col_sort:
        sort_sell = st.selectbox(
            "賣出列表排序",
            ["依日期（新→舊）", "依日期（舊→新）", "依股票", "依剩餘可配（多→少）"],
            key="sort_sell",
        )
    if not df_sells.empty:
        if "日期（新→舊）" in sort_sell:
            df_sells = df_sells.sort_values("日期", ascending=False)
        elif "日期（舊→新）" in sort_sell:
            df_sells = df_sells.sort_values("日期", ascending=True)
        elif "股票" in sort_sell:
            df_sells = df_sells.sort_values("股票")
        else:
            df_sells = df_sells.sort_values("剩餘可配", ascending=False)
        df_sells = df_sells.reset_index(drop=True)
    sell_indices = list(range(len(df_sells)))
    # 多選：用 session_state 記住勾選的賣出交易ID（同時保留一筆 active 賣出供下方預覽）
    if "add_sell_ids" not in st.session_state:
        st.session_state["add_sell_ids"] = []
    if "active_sell_id" not in st.session_state:
        st.session_state["active_sell_id"] = None
    sell_id = None
    sell_trade = None
    if df_sells.empty:
        st.caption("目前沒有可顯示的賣出交易（請取消「僅顯示有剩餘配額的股票」或選擇其他股票）。")
    else:
        df_sells_display = df_sells.copy()
        # 快速操作：全選 / 取消選擇（僅針對目前列表）
        a1, a2, a3 = st.columns([1, 1, 6])
        with a1:
            if st.button("全選", key="sell_select_all", help="全選目前列表中的賣出交易"):
                st.session_state["add_sell_ids"] = [int(x) for x in df_sells["交易ID"].tolist()]
                st.session_state["active_sell_id"] = st.session_state["add_sell_ids"][-1] if st.session_state["add_sell_ids"] else None
                st.rerun()
        with a2:
            if st.button("取消選擇", key="sell_select_none", help="取消所有已勾選的賣出交易"):
                # 只清除「賣出勾選」，不影響上方「選擇股票」篩選狀態
                st.session_state["add_sell_ids"] = []
                st.session_state["active_sell_id"] = None
                st.rerun()

        selected_ids = set(int(x) for x in (st.session_state.get("add_sell_ids") or []) if str(x).isdigit())
        df_sells_display.insert(0, "勾選", [int(df_sells.iloc[i]["交易ID"]) in selected_ids for i in sell_indices])
        # 股數相關欄位以千分位字串顯示（>1000 顯示 1,000）
        for col in ("賣出股數", "已配", "剩餘可配"):
            if col in df_sells_display.columns:
                df_sells_display[col] = df_sells_display[col].apply(lambda x: f"{int(x):,}" if x is not None and str(x).replace(".", "").replace("-", "").isdigit() else str(x))
        # 賣出價格：數值兩位小數，空值顯示 —
        if "賣出價格" in df_sells_display.columns:
            def _fmt_price(x):
                if x is None or (isinstance(x, float) and pd.isna(x)):
                    return "—"
                try:
                    return f"{float(x):,.2f}"
                except (ValueError, TypeError):
                    return str(x)
            df_sells_display["賣出價格"] = df_sells_display["賣出價格"].apply(_fmt_price)
        edited_sell = st.data_editor(
            df_sells_display,
            use_container_width=True,
            hide_index=True,
            key="add_sell_editor",
            column_config={
                "勾選": st.column_config.CheckboxColumn("勾選", width="small", required=True),
            },
            disabled=["買賣人", "交易ID", "股票", "日期", "賣出價格", "當沖", "賣出股數", "已配", "剩餘可配"],
        )
        # 從編輯結果取回勾選的賣出交易ID（允許多選）
        checked_rows = edited_sell.index[edited_sell["勾選"]].tolist()
        sell_ids = [int(df_sells.iloc[i]["交易ID"]) for i in checked_rows] if checked_rows else []
        st.session_state["add_sell_ids"] = sell_ids
        # active 賣出：用最後一次勾到的那筆（若全取消則為 None）
        st.session_state["active_sell_id"] = sell_ids[-1] if sell_ids else None
        sell_id = st.session_state["active_sell_id"]
        sell_trade = trade_by_id.get(sell_id) if sell_id else None
    if sell_trade:
        # 同股票、且交易日在賣出日當天或之前的買進
        same_stock_buys = [
            t for t in buys
            if t.stock_id == sell_trade.stock_id and t.trade_date <= sell_trade.trade_date and t.id != sell_trade.id
        ]
        sell_remain = max(0, sell_trade.quantity - sell_used[sell_id])
        if not same_stock_buys:
            st.caption("此賣出所屬股票沒有可配對的買進（需同股票且買進日 ≤ 賣出日）。")
        else:
            # 先建立買進列表與排序，供下方「選擇買進」表格與輔助面板連動使用
            rows_buy = []
            for t in same_stock_buys:
                used = buy_used[t.id]
                remain = max(0, t.quantity - used)
                if filter_has_remain and remain <= 0:
                    continue
                name = (masters.get(t.stock_id).name if masters.get(t.stock_id) else "") or ""
                rows_buy.append({
                    "買賣人": getattr(t, "user", None) or "",
                    "交易ID": t.id,
                    "股票": f"{t.stock_id} {name}".strip(),
                    "日期": str(t.trade_date),
                    "買入價格": round(float(t.price), 2) if t.price is not None else None,
                    "當沖": bool(getattr(t, "is_daytrade", False)),
                    "買進股數": t.quantity,
                    "已配": used,
                    "剩餘可配": remain,
                })
            df_buys = pd.DataFrame(rows_buy)
            if not df_buys.empty:
                sort_buy_val = st.session_state.get("sort_buy", "依日期（新→舊）")
                if "日期（新→舊）" in str(sort_buy_val):
                    df_buys = df_buys.sort_values("日期", ascending=False)
                elif "日期（舊→新）" in str(sort_buy_val):
                    df_buys = df_buys.sort_values("日期", ascending=True)
                elif "買入價格" in str(sort_buy_val):
                    df_buys = df_buys.sort_values("買入價格", ascending=False)
                else:
                    df_buys = df_buys.sort_values("剩餘可配", ascending=False)
            df_buys = df_buys.reset_index(drop=True)
            buy_id_to_idx = {int(df_buys.iloc[i]["交易ID"]): i for i in range(len(df_buys))} if not df_buys.empty else {}

            # ---------- 輔助篩選配對面板 ----------
            sid = sell_trade.stock_id
            stock_name = (masters.get(sid).name if masters.get(sid) else "") or ""
            custom_rules_tuples = [(r.sell_trade_id, r.buy_trade_id, r.matched_qty) for r in rules]
            quote = get_quote_cached(sid)
            current_price = float(quote["price"]) if quote and quote.get("price") is not None else None
            pos_by_stock = compute_position_and_cost_by_stock(trades, custom_rules=custom_rules_tuples)
            pos = pos_by_stock.get(sid) if pos_by_stock else None
            with st.expander("輔助篩選配對：現價與推薦買進（依賺賠分類）", expanded=True):
                if current_price is not None:
                    st.markdown("**%s %s** · 現價 **%s**" % (sid, stock_name, f"{current_price:,.2f}"))
                    # 要賣股票的賣出價格：單選顯示單一價格；多選顯示區間（min~max）
                    selected_sell_ids = list(st.session_state.get("add_sell_ids") or [])
                    sell_prices = []
                    for tid in selected_sell_ids:
                        t = trade_by_id.get(tid)
                        if t and t.price is not None:
                            try:
                                sell_prices.append(float(t.price))
                            except Exception:
                                pass
                    if sell_prices:
                        lo = min(sell_prices)
                        hi = max(sell_prices)
                        if abs(hi - lo) < 1e-9:
                            s_txt = f"{lo:,.2f}"
                        else:
                            s_txt = f"{lo:,.2f}～{hi:,.2f}"
                        st.markdown("要賣股票的**賣出價格**：**%s**" % s_txt)
                    # 多筆賣出：顯示總剩餘配額（未被規則占用的可配股數總和）
                    if selected_sell_ids:
                        total_remain = 0
                        for tid in selected_sell_ids:
                            t = trade_by_id.get(tid)
                            if not t:
                                continue
                            used = sell_used.get(t.id, 0)
                            total_remain += max(0, int(t.quantity or 0) - int(used))
                        if len(selected_sell_ids) > 1:
                            st.caption(f"總剩餘配額：**{total_remain:,}**")
                    # 多賣出：將分配策略移到推薦買進面板上方
                    if len(selected_sell_ids) > 1:
                        s1, s2 = st.columns([4, 1], vertical_alignment="center")
                        with s1:
                            st.selectbox(
                                "集體沖銷策略（多賣出）",
                                [
                                    "FIFO（買進 舊→新）",
                                    "LIFO（買進 新→舊）",
                                    "買價 低→高",
                                    "買價 高→低",
                                    "依剩餘股數比例分攤",
                                ],
                                key="multi_sell_alloc_mode",
                                help="先選策略並按右側「確定策略」，推薦買進表會自動把「配到的」打勾並把勾選列移到最上方；最後按「確定沖銷」才會真正寫入規則。",
                            )
                        with s2:
                            if st.button("確定策略", type="primary", key="multi_sell_apply_strategy"):
                                mode = st.session_state.get("multi_sell_alloc_mode", "FIFO（買進 舊→新）")
                                # 以「所有勾選賣出」為需求端、以同股票可用買進為供給端，做一次性分配並寫回 rec_panel_state（不寫入 DB）
                                sell_trades_multi = [trade_by_id[i] for i in selected_sell_ids if i in trade_by_id]
                                sell_trades_multi = sorted(sell_trades_multi, key=lambda t: (t.trade_date, t.id))
                                buy_trades_multi = [t for t in same_stock_buys if (t.quantity - buy_used[t.id]) > 0]
                                if "LIFO" in str(mode):
                                    buy_trades_multi = sorted(buy_trades_multi, key=lambda t: (t.trade_date, t.id), reverse=True)
                                elif "買價 低→高" in str(mode):
                                    buy_trades_multi = sorted(buy_trades_multi, key=lambda t: (float(t.price) if t.price is not None else float("inf"), t.trade_date, t.id))
                                elif "買價 高→低" in str(mode):
                                    buy_trades_multi = sorted(buy_trades_multi, key=lambda t: (float(t.price) if t.price is not None else float("-inf"), t.trade_date, t.id), reverse=True)
                                else:
                                    buy_trades_multi = sorted(buy_trades_multi, key=lambda t: (t.trade_date, t.id))

                                buy_remaining = {t.id: max(0, t.quantity - buy_used[t.id]) for t in buy_trades_multi}
                                plan = []  # (sell_id, buy_id, qty)
                                for s in sell_trades_multi:
                                    s_rem = max(0, s.quantity - sell_used[s.id])
                                    if s_rem <= 0:
                                        continue
                                    eligible = [b for b in buy_trades_multi if b.trade_date <= s.trade_date and buy_remaining.get(b.id, 0) > 0]
                                    if not eligible:
                                        continue
                                    if "比例分攤" in str(mode):
                                        total_rem = sum(buy_remaining.get(b.id, 0) for b in eligible)
                                        if total_rem <= 0:
                                            continue
                                        allocs = []
                                        given = 0
                                        for b in eligible:
                                            b_rem = int(buy_remaining.get(b.id, 0) or 0)
                                            if b_rem <= 0:
                                                continue
                                            qty = int((s_rem * b_rem) // total_rem)
                                            qty = min(qty, b_rem)
                                            if qty > 0:
                                                allocs.append((b, qty))
                                                given += qty
                                        left = s_rem - given
                                        if left > 0:
                                            for b in eligible:
                                                if left <= 0:
                                                    break
                                                b_rem = int(buy_remaining.get(b.id, 0) or 0)
                                                already = next((q for bb, q in allocs if bb.id == b.id), 0)
                                                cap = b_rem - already
                                                if cap <= 0:
                                                    continue
                                                add = min(left, cap)
                                                if add <= 0:
                                                    continue
                                                found = False
                                                for ii in range(len(allocs)):
                                                    if allocs[ii][0].id == b.id:
                                                        allocs[ii] = (allocs[ii][0], allocs[ii][1] + add)
                                                        found = True
                                                        break
                                                if not found:
                                                    allocs.append((b, add))
                                                left -= add
                                        for b, qty in allocs:
                                            if qty <= 0:
                                                continue
                                            plan.append((s.id, b.id, int(qty)))
                                            buy_remaining[b.id] = int(buy_remaining.get(b.id, 0) or 0) - int(qty)
                                    else:
                                        for b in eligible:
                                            if s_rem <= 0:
                                                break
                                            b_rem = int(buy_remaining.get(b.id, 0) or 0)
                                            if b_rem <= 0:
                                                continue
                                            qty = min(int(s_rem), b_rem)
                                            if qty <= 0:
                                                continue
                                            plan.append((s.id, b.id, int(qty)))
                                            s_rem -= qty
                                            buy_remaining[b.id] = b_rem - qty

                                if "rec_panel_state" not in st.session_state:
                                    st.session_state["rec_panel_state"] = {}
                                # 先清掉本次多選賣出的既有勾選（避免殘留）
                                for sid_ in selected_sell_ids:
                                    st.session_state["rec_panel_state"][sid_] = {}
                                for sid_, bid_, qty_ in plan:
                                    st.session_state["rec_panel_state"][sid_][int(bid_)] = {"勾選": True, "沖銷股數": int(qty_)}
                                st.rerun()
                    # 勾選的賣出（已配/剩餘配額在表格與確定沖銷區下方動態顯示）
                    if pos and pos["qty"] and pos["qty"] > 0:
                        avg_cost = pos["cost"] / pos["qty"]
                        pnl_amt = (current_price - avg_cost) * pos["qty"]
                        pnl_pct = ((current_price - avg_cost) / avg_cost * 100) if avg_cost else 0
                        if pnl_amt >= 0:
                            st.markdown("持倉損益：**%s** 元（**+%.2f%%**）" % (f"{pnl_amt:+,.0f}", pnl_pct))
                        else:
                            st.markdown("持倉損益：**%s** 元（**%.2f%%**）" % (f"{pnl_amt:,.0f}", pnl_pct))
                    else:
                        st.caption("目前無持倉（或已全部沖銷）。")
                else:
                    st.caption("無法取得現價（請確認 API 或網路）。")
                buys_with_remain = [(t, max(0, t.quantity - buy_used[t.id])) for t in same_stock_buys if (t.quantity - buy_used[t.id]) > 0]
                if not buys_with_remain or current_price is None:
                    st.caption("無剩餘可配的買進，或無現價可試算。")
                else:
                    def _cat(pct):
                        if pct > 20: return "大賺"
                        if pct > 5: return "中賺"
                        if pct >= 0: return "小賺"
                        if pct >= -5: return "小賠"
                        if pct >= -20: return "中賠"
                        return "大賠"
                    # 動態沖銷股數：被勾選的列維持不變；剩餘配額 = 賣出剩餘 - 勾選列沖銷總和；未勾選列若 > 剩餘配額則改為剩餘配額
                    rec_state = st.session_state.get("rec_panel_state") or {}
                    rec_state_sell = rec_state.get(sell_id) or {}
                    # 先算勾選列沖銷總和，若超過賣出剩餘則依表格順序從勾選列壓縮
                    total_checked = 0
                    for t, rem in buys_with_remain:
                        prev = rec_state_sell.get(t.id) or {}
                        if prev.get("勾選"):
                            total_checked += int(prev.get("沖銷股數", 0) or 0)
                    if total_checked > sell_remain:
                        remaining = sell_remain
                        capped_checked = {}
                        for t, rem in buys_with_remain:
                            prev = rec_state_sell.get(t.id) or {}
                            if prev.get("勾選"):
                                want = int(prev.get("沖銷股數", 0) or 0)
                                qty = min(want, rem, remaining)
                                capped_checked[t.id] = qty
                                remaining -= qty
                        remaining_sell = 0
                    else:
                        capped_checked = None
                        remaining_sell = sell_remain - total_checked
                    recs = []
                    for t, rem in buys_with_remain:
                        pnl_amt = (current_price - t.price) * rem
                        pnl_pct = ((current_price - t.price) / t.price * 100) if t.price else 0
                        prev = rec_state_sell.get(t.id) or {}
                        checked = prev.get("勾選", False)
                        if checked:
                            if capped_checked is not None:
                                qty = capped_checked.get(t.id, 0)
                            else:
                                qty = min(int(prev.get("沖銷股數", 0) or 0), rem)
                        else:
                            # 未勾選：<= 剩餘配額不變，> 的改為剩餘配額
                            want = int(prev.get("沖銷股數", rem) or rem)
                            qty = min(want, remaining_sell, rem)
                        recs.append({
                            "勾選": checked,
                            "沖銷股數": qty,
                            "分類": _cat(pnl_pct),
                            "買進ID": t.id,
                            "買價": t.price,
                            "現價": current_price,
                            "剩餘可配": rem,
                            "賺賠金額": pnl_amt,
                            "賺賠%": pnl_pct,
                        })
                    df_rec = pd.DataFrame(recs)
                    st.markdown("**依賺賠篩選推薦買進**")
                    cx1, cx2, cx3, cx4, cx5, cx6 = st.columns(6)
                    with cx1: show_大賺 = st.checkbox("大賺(>20%%)", value=True, key="rec_大賺")
                    with cx2: show_中賺 = st.checkbox("中賺(5~20%%)", value=True, key="rec_中賺")
                    with cx3: show_小賺 = st.checkbox("小賺(0~5%%)", value=True, key="rec_小賺")
                    with cx4: show_大賠 = st.checkbox("大賠(<-20%%)", value=True, key="rec_大賠")
                    with cx5: show_中賠 = st.checkbox("中賠(-20~-5%%)", value=True, key="rec_中賠")
                    with cx6: show_小賠 = st.checkbox("小賠(-5~0%%)", value=True, key="rec_小賠")
                    show_cats = set()
                    if show_大賺: show_cats.add("大賺")
                    if show_中賺: show_cats.add("中賺")
                    if show_小賺: show_cats.add("小賺")
                    if show_大賠: show_cats.add("大賠")
                    if show_中賠: show_cats.add("中賠")
                    if show_小賠: show_cats.add("小賠")
                    df_rec = df_rec[df_rec["分類"].isin(show_cats)]
                    # 讓已勾選（配到的）優先顯示在上面
                    if not df_rec.empty and "勾選" in df_rec.columns:
                        try:
                            df_rec["_rank_checked"] = df_rec["勾選"].apply(lambda x: 0 if bool(x) else 1)
                            df_rec = df_rec.sort_values(by=["_rank_checked", "分類", "買進ID"], ascending=[True, True, True]).drop(columns=["_rank_checked"])
                        except Exception:
                            pass
                    if show_中賺 and "中賺" in df_rec["分類"].values:
                        mid = df_rec[df_rec["分類"] == "中賺"]
                        n = len(mid)
                        if n > 0:
                            sum_amt = mid["賺賠金額"].sum()
                            sum_cost = (mid["買價"] * mid["剩餘可配"]).sum()
                            total_rem = mid["剩餘可配"].sum()
                            avg_price = sum_cost / total_rem if total_rem else 0
                            avg_pct = (sum_amt / sum_cost * 100) if sum_cost else 0
                            avg_row = pd.DataFrame([{
                                "勾選": False,
                                "沖銷股數": 0,
                                "分類": "中賺(平均)",
                                "買進ID": "共%d筆" % n,
                                "買價": round(avg_price, 2),
                                "現價": current_price,
                                "剩餘可配": int(total_rem),
                                "賺賠金額": sum_amt,
                                "賺賠%": round(avg_pct, 2),
                            }])
                            # 保留所有中賺個別筆數，僅在最後追加一列「中賺(平均)」供參考（該列不可用於確定沖銷）
                            df_rec = pd.concat([df_rec, avg_row], ignore_index=True)
                    if df_rec.empty:
                        st.caption("目前篩選下無推薦筆數。")
                    else:
                        df_rec = df_rec.round({"買價": 2, "現價": 2, "賺賠金額": 0, "賺賠%": 2})
                        # 價錢、金額欄位以千分位字串顯示（買價、現價、賺賠金額皆為唯讀）
                        df_rec["買價"] = df_rec["買價"].apply(lambda x: f"{float(x):,.2f}" if x is not None and isinstance(x, (int, float)) else str(x) if x is not None else "")
                        df_rec["現價"] = df_rec["現價"].apply(lambda x: f"{float(x):,.2f}" if x is not None and isinstance(x, (int, float)) else str(x) if x is not None else "")
                        df_rec["賺賠金額"] = df_rec["賺賠金額"].apply(lambda x: f"{int(x):,}" if x is not None and isinstance(x, (int, float)) else (f"{int(float(x)):,}" if x is not None and str(x).replace(".", "").replace("-", "").isdigit() else str(x) if x is not None else ""))
                        df_rec["賺賠%"] = df_rec["賺賠%"].apply(lambda x: f"{float(x):.2f}%" if x is not None and isinstance(x, (int, float)) else str(x) if x is not None else "")
                        if "中賺(平均)" in df_rec["分類"].values:
                            st.caption("※ 「中賺(平均)」為彙總列，僅供參考；請勾選上方個別買進列並設定沖銷股數後按「確定沖銷」。")
                        edited_rec = st.data_editor(
                            df_rec,
                            use_container_width=True,
                            hide_index=True,
                            key="rec_editor",
                            column_config={
                                "勾選": st.column_config.CheckboxColumn("勾選", width="small", required=True),
                                "沖銷股數": st.column_config.NumberColumn("沖銷股數", min_value=0, max_value=sell_remain, step=1, format="%d"),
                            },
                            disabled=["分類", "買進ID", "買價", "現價", "剩餘可配", "賺賠金額", "賺賠%"],
                        )
                        # 儲存勾選與沖銷股數，下次 run 時依序分配以確保總和 <= 賣出剩餘配額
                        if "rec_panel_state" not in st.session_state:
                            st.session_state["rec_panel_state"] = {}
                        if sell_id not in st.session_state["rec_panel_state"]:
                            st.session_state["rec_panel_state"][sell_id] = {}
                        rec_changed = False
                        for _, row in edited_rec.iterrows():
                            try:
                                bid = int(row["買進ID"])
                            except (TypeError, ValueError):
                                continue
                            new_勾選 = bool(row.get("勾選", False))
                            new_沖銷股數 = int(row.get("沖銷股數", 0)) if row.get("沖銷股數") is not None else 0
                            prev = st.session_state["rec_panel_state"][sell_id].get(bid) or {}
                            if prev.get("勾選") != new_勾選 or prev.get("沖銷股數") != new_沖銷股數:
                                rec_changed = True
                            st.session_state["rec_panel_state"][sell_id][bid] = {
                                "勾選": new_勾選,
                                "沖銷股數": new_沖銷股數,
                            }
                        # 若有勾選/沖銷股數變更，立即 rerun 讓下一輪用新狀態重畫表格，勾選才會正確顯示
                        if rec_changed:
                            st.rerun()
                        # 依勾選與沖銷股數計算預覽已配／剩餘配額（僅計買進ID 為整數的列）
                        def _is_int_buy_id(x):
                            try:
                                int(x)
                                return True
                            except (TypeError, ValueError):
                                return False
                        checked = edited_rec[edited_rec["勾選"] == True] if "勾選" in edited_rec.columns else pd.DataFrame()
                        temp_alloc = 0
                        if not checked.empty:
                            for _, row in checked.iterrows():
                                if _is_int_buy_id(row.get("買進ID")):
                                    q = int(row.get("沖銷股數", 0)) or 0
                                    temp_alloc += min(max(0, q), sell_remain - temp_alloc, int(row.get("剩餘可配", 0)))
                        preview_已配 = sell_used[sell_id] + temp_alloc
                        preview_剩餘 = sell_trade.quantity - preview_已配
                        st.caption("勾選的賣出：交易日期 **%s** · 賣出股數 **%s** · 已配 **%s** · 剩餘配額 **%s**" % (sell_trade.trade_date, f"{sell_trade.quantity:,}", f"{preview_已配:,}", f"{max(0, preview_剩餘):,}"))
                        selected_sell_ids = list(st.session_state.get("add_sell_ids") or [])
                        # 勾選單一筆時連動下方「選擇買進」表格
                        if not checked.empty and buy_id_to_idx:
                            one_checked = [r for _, r in checked.iterrows() if _is_int_buy_id(r.get("買進ID"))]
                            if len(one_checked) == 1:
                                bid = int(one_checked[0]["買進ID"])
                                if bid in buy_id_to_idx:
                                    st.session_state["add_buy_idx"] = buy_id_to_idx[bid]
                                    st.session_state["panel_selected_buy_id"] = bid
                        if st.button("確定沖銷", type="primary", key="confirm_offset_btn"):
                            selected_sell_ids = list(st.session_state.get("add_sell_ids") or [])
                            # 取「被勾選的買進ID」作為候選池（多賣出模式用自動分配；單賣出模式沿用手動 qty）
                            selected_buy_ids = []
                            if not checked.empty:
                                for _, row in checked.iterrows():
                                    if _is_int_buy_id(row.get("買進ID")):
                                        selected_buy_ids.append(int(row.get("買進ID")))

                            if len(selected_sell_ids) > 1:
                                if not selected_buy_ids:
                                    st.warning("請至少勾選 1 筆買進，才能對多筆賣出自動分配。")
                                else:
                                    sell_trades_multi = [trade_by_id[i] for i in selected_sell_ids if i in trade_by_id]
                                    sell_trades_multi = sorted(sell_trades_multi, key=lambda t: (t.trade_date, t.id))
                                    buy_trades_multi = [trade_by_id[i] for i in selected_buy_ids if i in trade_by_id]
                                    mode = st.session_state.get("multi_sell_alloc_mode", "FIFO（買進 舊→新）")
                                    if "LIFO" in str(mode):
                                        buy_trades_multi = sorted(buy_trades_multi, key=lambda t: (t.trade_date, t.id), reverse=True)
                                    elif "買價 低→高" in str(mode):
                                        buy_trades_multi = sorted(buy_trades_multi, key=lambda t: (float(t.price) if t.price is not None else float("inf"), t.trade_date, t.id))
                                    elif "買價 高→低" in str(mode):
                                        buy_trades_multi = sorted(buy_trades_multi, key=lambda t: (float(t.price) if t.price is not None else float("-inf"), t.trade_date, t.id), reverse=True)
                                    else:
                                        buy_trades_multi = sorted(buy_trades_multi, key=lambda t: (t.trade_date, t.id))

                                    buy_remaining = {t.id: max(0, t.quantity - buy_used[t.id]) for t in buy_trades_multi}
                                    plan = []
                                    for s in sell_trades_multi:
                                        s_rem = max(0, s.quantity - sell_used[s.id])
                                        if s_rem <= 0:
                                            continue
                                        eligible = [b for b in buy_trades_multi if b.trade_date <= s.trade_date and buy_remaining.get(b.id, 0) > 0]
                                        if not eligible:
                                            continue
                                        if "比例分攤" in str(mode):
                                            total_rem = sum(buy_remaining.get(b.id, 0) for b in eligible)
                                            if total_rem <= 0:
                                                continue
                                            allocs = []
                                            given = 0
                                            for b in eligible:
                                                b_rem = int(buy_remaining.get(b.id, 0) or 0)
                                                if b_rem <= 0:
                                                    continue
                                                qty = int((s_rem * b_rem) // total_rem)
                                                qty = min(qty, b_rem)
                                                if qty > 0:
                                                    allocs.append((b, qty))
                                                    given += qty
                                            left = s_rem - given
                                            if left > 0:
                                                for b in eligible:
                                                    if left <= 0:
                                                        break
                                                    b_rem = int(buy_remaining.get(b.id, 0) or 0)
                                                    already = next((q for bb, q in allocs if bb.id == b.id), 0)
                                                    cap = b_rem - already
                                                    if cap <= 0:
                                                        continue
                                                    add = min(left, cap)
                                                    if add <= 0:
                                                        continue
                                                    found = False
                                                    for ii in range(len(allocs)):
                                                        if allocs[ii][0].id == b.id:
                                                            allocs[ii] = (allocs[ii][0], allocs[ii][1] + add)
                                                            found = True
                                                            break
                                                    if not found:
                                                        allocs.append((b, add))
                                                    left -= add
                                            for b, qty in allocs:
                                                if qty <= 0:
                                                    continue
                                                plan.append((s.id, b.id, int(qty)))
                                                buy_remaining[b.id] = int(buy_remaining.get(b.id, 0) or 0) - int(qty)
                                        else:
                                            for b in eligible:
                                                if s_rem <= 0:
                                                    break
                                                b_rem = int(buy_remaining.get(b.id, 0) or 0)
                                                if b_rem <= 0:
                                                    continue
                                                qty = min(int(s_rem), b_rem)
                                                if qty <= 0:
                                                    continue
                                                plan.append((s.id, b.id, int(qty)))
                                                s_rem -= qty
                                                buy_remaining[b.id] = b_rem - qty

                                    if not plan:
                                        st.warning("目前無可分配的股數（可能賣出/買進剩餘配額已用完，或買進日期晚於賣出日）。")
                                    else:
                                        sessw = get_session()
                                        try:
                                            for sid, bid, qty in plan:
                                                existing = sessw.query(CustomMatchRule).filter(
                                                    CustomMatchRule.sell_trade_id == sid,
                                                    CustomMatchRule.buy_trade_id == bid,
                                                ).first()
                                                if existing:
                                                    existing.matched_qty = int(existing.matched_qty) + int(qty)
                                                else:
                                                    sessw.add(CustomMatchRule(sell_trade_id=sid, buy_trade_id=bid, matched_qty=int(qty)))
                                            sessw.commit()
                                            st.success(f"已新增/更新 {len(plan)} 筆沖銷規則（多賣出自動分配）")
                                            st.rerun()
                                        except OperationalError:
                                            sessw.rollback()
                                            st.error("無法寫入資料庫（目前環境可能唯讀）")
                                        except Exception as e:
                                            sessw.rollback()
                                            st.error(f"新增失敗：{e}")
                                        finally:
                                            sessw.close()
                            else:
                                to_add = []
                                for _, row in checked.iterrows():
                                    if not _is_int_buy_id(row.get("買進ID")):
                                        continue
                                    bid = int(row["買進ID"])
                                    qty = int(row.get("沖銷股數", 0)) or 0
                                    rem_buy = int(row.get("剩餘可配", 0))
                                    if qty <= 0 or qty > rem_buy or qty > sell_remain:
                                        continue
                                    existing = next((r for r in rules if r.sell_trade_id == sell_id and r.buy_trade_id == bid), None)
                                    if existing:
                                        continue
                                    to_add.append((bid, qty))
                                if not to_add:
                                    st.warning("請至少勾選一筆有效買進並設定沖銷股數（且該買進尚無規則）。")
                                else:
                                    total_qty = sum(q for _, q in to_add)
                                    if total_qty > sell_remain:
                                        st.warning("勾選的沖銷股數總和不得超過賣出剩餘配額 **%s**。" % f"{sell_remain:,}")
                                    else:
                                        try:
                                            for bid, qty in to_add:
                                                sess.add(CustomMatchRule(sell_trade_id=sell_id, buy_trade_id=bid, matched_qty=qty))
                                            sess.commit()
                                            if "rec_panel_state" in st.session_state and sell_id in st.session_state["rec_panel_state"]:
                                                del st.session_state["rec_panel_state"][sell_id]
                                            st.success("已新增 %d 筆沖銷規則。" % len(to_add))
                                            st.rerun()
                                        except OperationalError:
                                            sess.rollback()
                                            st.error("無法寫入資料庫（目前環境可能唯讀）")
                                        except Exception as e:
                                            sess.rollback()
                                            st.error("新增失敗：%s" % e)
                                        finally:
                                            sess.close()
            st.markdown("**2. 選擇「買進」交易（與上列賣出沖銷）**")
            sort_buy = st.selectbox(
                "買進列表排序",
                ["依日期（新→舊）", "依日期（舊→新）", "依買入價格", "依剩餘可配（多→少）"],
                key="sort_buy",
            )
            if not df_buys.empty:
                if "日期（新→舊）" in sort_buy:
                    df_buys = df_buys.sort_values("日期", ascending=False)
                elif "日期（舊→新）" in sort_buy:
                    df_buys = df_buys.sort_values("日期", ascending=True)
                elif "買入價格" in sort_buy:
                    df_buys = df_buys.sort_values("買入價格", ascending=False)
                else:
                    df_buys = df_buys.sort_values("剩餘可配", ascending=False)
                df_buys = df_buys.reset_index(drop=True)
                # 輔助面板勾選連動：依重排後的表格更新選中列索引
                pid = st.session_state.get("panel_selected_buy_id")
                if pid is not None:
                    new_idx = next((i for i in range(len(df_buys)) if int(df_buys.iloc[i]["交易ID"]) == pid), None)
                    if new_idx is not None:
                        st.session_state["add_buy_idx"] = new_idx
            buy_indices = list(range(len(df_buys)))
            if "add_buy_idx" not in st.session_state:
                st.session_state["add_buy_idx"] = 0
            buy_idx = st.session_state["add_buy_idx"]
            if buy_idx >= len(buy_indices):
                buy_idx = 0
                st.session_state["add_buy_idx"] = 0
            df_buys_display = df_buys.copy()
            df_buys_display.insert(0, "勾選", [bool(i == buy_idx) for i in buy_indices])
            if not df_buys_display.empty:
                df_buys_display["勾選"] = df_buys_display["勾選"].astype(bool)
                df_buys_display["交易ID"] = df_buys_display["交易ID"].astype("int64")
                df_buys_display["買進股數"] = df_buys_display["買進股數"].astype("int64")
                df_buys_display["已配"] = df_buys_display["已配"].astype("int64")
                df_buys_display["剩餘可配"] = df_buys_display["剩餘可配"].astype("int64")
                df_buys_display["買入價格"] = df_buys_display["買入價格"].astype("float64")
                df_buys_display["當沖"] = df_buys_display["當沖"].astype(bool)
                df_buys_display["股票"] = df_buys_display["股票"].astype(str)
                df_buys_display["日期"] = df_buys_display["日期"].astype(str)
            else:
                df_buys_display["勾選"] = df_buys_display["勾選"].astype(bool)
            # 股數相關欄位以千分位字串顯示（>1000 顯示 1,000）
            for col in ("買進股數", "已配", "剩餘可配"):
                if col in df_buys_display.columns:
                    df_buys_display[col] = df_buys_display[col].apply(lambda x: f"{int(x):,}" if x is not None and str(x).replace(".", "").replace("-", "").isdigit() else str(x))
            # 買入價格欄位以千分位顯示（例：1,234.56）
            if "買入價格" in df_buys_display.columns:
                def _fmt_price(x):
                    if x is None or (isinstance(x, float) and pd.isna(x)):
                        return "—"
                    try:
                        return f"{float(x):,.2f}"
                    except (ValueError, TypeError):
                        return str(x) if x is not None else "—"
                df_buys_display["買入價格"] = df_buys_display["買入價格"].apply(_fmt_price)
            edited_buy = st.data_editor(
                df_buys_display,
                use_container_width=True,
                hide_index=True,
                key="add_buy_editor",
                column_config={
                    "勾選": st.column_config.CheckboxColumn("勾選", width="small", required=True),
                },
                disabled=["買賣人", "交易ID", "股票", "日期", "當沖", "買進股數", "買入價格", "已配", "剩餘可配"],
            )
            checked_buy = edited_buy.index[edited_buy["勾選"]].tolist()
            if len(checked_buy) == 1:
                st.session_state["add_buy_idx"] = int(checked_buy[0])
                buy_idx = int(checked_buy[0])
            elif len(checked_buy) > 1:
                st.session_state["add_buy_idx"] = int(checked_buy[-1])
                buy_idx = int(checked_buy[-1])
            buy_id = int(df_buys.iloc[buy_idx]["交易ID"]) if buy_indices else None
            buy_trade = trade_by_id.get(buy_id) if buy_id else None
            st.markdown("---")
            if buy_trade:
                buy_remain = max(0, buy_trade.quantity - buy_used[buy_id])
                max_qty = min(sell_remain, buy_remain)
                if max_qty <= 0:
                    st.caption("此賣出或此買進的剩餘可配對股數已用完，請選其他交易或刪除既有規則後再配。")
                else:
                    # 檢查是否已有 (sell_id, buy_id) 規則（同一對只能一筆，用 upsert 概念）
                    existing = next((r for r in rules if r.sell_trade_id == sell_id and r.buy_trade_id == buy_id), None)
                    if existing:
                        st.caption(f"此賣出與此買進已有規則（沖銷 **{existing.matched_qty:,}** 股）。若要修改請先刪除該筆規則再新增。")
                    else:
                        # 在沖銷股數上方列出被勾選的賣出／買進，方便判斷
                        st.markdown("**本次配對**")
                        col_sell_summary, col_buy_summary = st.columns(2)
                        with col_sell_summary:
                            st.caption("勾選的賣出")
                            nm = (masters.get(sell_trade.stock_id).name if masters.get(sell_trade.stock_id) else "") or ""
                            st.markdown(f"**#{sell_id}** {sell_trade.stock_id} {nm} · {sell_trade.trade_date} · 賣出 **{sell_trade.quantity:,}** 股（已配 {sell_used[sell_id]:,}，剩餘可配 **{sell_remain:,}**）")
                        with col_buy_summary:
                            st.caption("勾選的買進")
                            nm = (masters.get(buy_trade.stock_id).name if masters.get(buy_trade.stock_id) else "") or ""
                            st.markdown(f"**#{buy_id}** {buy_trade.stock_id} {nm} · {buy_trade.trade_date} · 買進 **{buy_trade.quantity:,}** 股 @ {buy_trade.price:,.2f}（已配 {buy_used[buy_id]:,}，剩餘可配 **{buy_remain:,}**）")
                        st.markdown("")  # 空一行
                        qty = st.number_input("沖銷股數", min_value=1, max_value=max_qty, value=min(1, max_qty), key="add_qty")
                        if st.button("新增此筆規則", key="add_rule_btn"):
                            try:
                                sess.add(CustomMatchRule(sell_trade_id=sell_id, buy_trade_id=buy_id, matched_qty=qty))
                                sess.commit()
                                st.success(f"已新增規則：賣出 #{sell_id} 與 買進 #{buy_id} 沖銷 **{qty:,}** 股")
                                st.rerun()
                            except OperationalError as e:
                                sess.rollback()
                                st.error("無法寫入資料庫（目前環境可能唯讀）")
                            except Exception as e:
                                sess.rollback()
                                st.error(f"新增失敗：{e}")
                            finally:
                                sess.close()
            # 下方改為新的已配對一覽（含修改／刪除），見本頁最下方

sess.close()

# ---------- 已配對一覽（表格內直接修改／刪除，取代舊的本股票一覽） ----------
st.subheader("已配對一覽")
if not rules:
    st.caption("尚無自定規則。請於上方新增。")
else:
    st.caption("以下為已設定的配對；可直接在「沖銷股數」欄修改數字後按「確認」，或按「刪除」移除該筆規則。")
    sess2 = get_session()
    try:
        rules_list = sess2.query(CustomMatchRule).all()
        # 先以全部規則計算已配股數（修改時上限才正確）
        sell_used2 = defaultdict(int)
        buy_used2 = defaultdict(int)
        for r in rules_list:
            sell_used2[r.sell_trade_id] += r.matched_qty
            buy_used2[r.buy_trade_id] += r.matched_qty
        # 股票選單：篩選已配對一覽要顯示的股票
        paired_stock_options = [("全部", None)]
        seen_paired = set()
        for r in rules_list:
            st_t = trade_by_id.get(r.sell_trade_id)
            buy_t = trade_by_id.get(r.buy_trade_id)
            sid = (st_t.stock_id if st_t else (buy_t.stock_id if buy_t else None))
            if sid and sid not in seen_paired:
                seen_paired.add(sid)
                name = (masters.get(sid).name if masters.get(sid) else "") or ""
                paired_stock_options.append((f"{sid} {name}".strip(), sid))
        filter_paired_idx = st.selectbox(
            "選擇股票",
            range(len(paired_stock_options)),
            format_func=lambda i: paired_stock_options[i][0],
            key="paired_filter_stock",
            help="僅顯示所選股票的已配對規則。",
        )
        filter_paired_id = paired_stock_options[filter_paired_idx][1]
        all_rules_for_summary = list(rules_list)
        if filter_paired_id:
            rules_list = [r for r in rules_list if (trade_by_id.get(r.sell_trade_id) and trade_by_id.get(r.sell_trade_id).stock_id == filter_paired_id)]
        # 依配對時間由新到舊排序（無 created_at 的排最後）
        from datetime import datetime as dt_min
        rules_list = sorted(rules_list, key=lambda r: (getattr(r, "created_at") or dt_min.min), reverse=True)
        # 表頭（含買賣人、賣出價格、買入價格）
        h1, h2, h3, h4, h5, h6, h7, h8, h9, h10, h11, h12, h13 = st.columns([1.2, 0.8, 0.7, 0.8, 0.9, 0.7, 0.8, 0.9, 1.0, 0.7, 0.6, 1.0, 0.5])
        with h1: st.markdown("**股票**")
        with h2: st.markdown("**買賣人**")
        with h3: st.markdown("**賣出ID**")
        with h4: st.markdown("**賣出日**")
        with h5: st.markdown("**賣出價格**")
        with h6: st.markdown("**買進ID**")
        with h7: st.markdown("**買進日**")
        with h8: st.markdown("**買入價格**")
        with h9: st.markdown("**配對時間**")
        with h10: st.markdown("**沖銷股數**")
        with h11: st.markdown("**當沖**")
        with h12: st.markdown("**操作**")
        with h13: st.markdown("")
        st.markdown("---")
        for r in rules_list:
            st_t = trade_by_id.get(r.sell_trade_id)
            buy_t = trade_by_id.get(r.buy_trade_id)
            st_date = str(st_t.trade_date) if st_t else "—"
            buy_date = str(buy_t.trade_date) if buy_t else "—"
            sell_price_str = f"{float(st_t.price):,.2f}" if st_t and st_t.price is not None else "—"
            buy_price_str = f"{float(buy_t.price):,.2f}" if buy_t and buy_t.price is not None else "—"
            created = getattr(r, "created_at", None)
            paired_time_str = created.strftime("%Y-%m-%d %H:%M") if created else "—"
            is_dt = "是" if (st_t and getattr(st_t, "is_daytrade", False)) or (buy_t and getattr(buy_t, "is_daytrade", False)) or (st_t and buy_t and st_t.trade_date == buy_t.trade_date) else "否"
            sid = st_t.stock_id if st_t else (buy_t.stock_id if buy_t else "—")
            name = (masters.get(sid).name if masters.get(sid) else "") or ""
            stock_label = f"{sid} {name}".strip()
            sell_user = getattr(st_t, "user", None) or "" if st_t else "—"
            cur = r.matched_qty
            if st_t and buy_t:
                sell_remain_after = max(0, st_t.quantity - (sell_used2[r.sell_trade_id] - cur))
                buy_remain_after = max(0, buy_t.quantity - (buy_used2[r.buy_trade_id] - cur))
                max_new_qty = max(1, min(sell_remain_after, buy_remain_after))
            else:
                max_new_qty = cur
            key_qty = f"rule_qty_{r.sell_trade_id}_{r.buy_trade_id}"
            key_mod = f"rule_mod_{r.sell_trade_id}_{r.buy_trade_id}"
            key_del = f"rule_del_{r.sell_trade_id}_{r.buy_trade_id}"
            c1, c2, c3, c4, c5, c6, c7, c8, c9, c10, c11, c12, c13 = st.columns([1.2, 0.8, 0.7, 0.8, 0.9, 0.7, 0.8, 0.9, 1.0, 0.7, 0.6, 1.0, 0.5])
            with c1: st.caption(stock_label)
            with c2: st.caption(str(sell_user))
            with c3: st.caption(str(r.sell_trade_id))
            with c4: st.caption(st_date)
            with c5: st.caption(sell_price_str)
            with c6: st.caption(str(r.buy_trade_id))
            with c7: st.caption(buy_date)
            with c8: st.caption(buy_price_str)
            with c9: st.caption(paired_time_str)
            with c10:
                new_qty = st.number_input("股數", min_value=1, max_value=max_new_qty, value=min(cur, max_new_qty), key=key_qty, label_visibility="collapsed")
            with c11: st.caption(is_dt)
            with c12:
                if st.button("確認", key=key_mod, type="primary"):
                    if new_qty != cur:
                        try:
                            r.matched_qty = new_qty
                            sess2.commit()
                            st.success(f"已修改 賣出 #{r.sell_trade_id} ↔ 買進 #{r.buy_trade_id} 為 **{new_qty:,}** 股")
                            st.rerun()
                        except OperationalError:
                            sess2.rollback()
                            st.error("無法寫入資料庫（目前環境可能唯讀）")
                        except Exception as e:
                            sess2.rollback()
                            st.error(f"修改失敗：{e}")
            with c13:
                if st.button("刪除", key=key_del):
                    try:
                        sess2.delete(r)
                        sess2.commit()
                        st.success("已刪除該筆規則")
                        st.rerun()
                    except OperationalError:
                        sess2.rollback()
                        st.error("無法寫入資料庫（目前環境可能唯讀）")
        # 依買賣人加總（以賣方買賣人統計）
        if custom_users and all_rules_for_summary:
            with st.expander("📊 依買賣人加總（以賣方統計）", expanded=False):
                summary_rows = []
                for u in custom_users:
                    user_rules = [r for r in all_rules_for_summary if trade_by_id.get(r.sell_trade_id) and getattr(trade_by_id.get(r.sell_trade_id), "user", None) == u]
                    summary_rows.append({
                        "買賣人": u,
                        "規則筆數": len(user_rules),
                        "總沖銷股數": sum(r.matched_qty for r in user_rules),
                    })
                df_custom_summary = pd.DataFrame(summary_rows)
                st.dataframe(df_custom_summary.style.format({"總沖銷股數": "{:,.0f}"}), use_container_width=True, hide_index=True)
    finally:
        sess2.close()
    st.markdown("---")

# ---------- 使用說明 ----------
st.subheader("使用說明")
st.markdown("""
- **自定沖銷**：在此頁設定「賣出 A 的 X 股」與「買進 B 的 X 股」配對；**庫存損益**、**投資績效（含損益總覽）**、**個股明細** 等頁面皆依此規則計算已實現損益。
- 同一筆賣出可分成多筆規則（配對不同買進）；同一筆買進也可配對多筆賣出，只要各筆「沖銷股數」總和不超過該筆交易的股數即可。
- 若未設定規則或僅部分設定，選「自定沖銷」時只有被規則覆蓋到的配對會計入已實現損益，其餘不計入。
""")
