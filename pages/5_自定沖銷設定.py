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

st.set_page_config(page_title="自定沖銷設定", layout="wide")
st.title("自定沖銷設定")
st.caption("可指定「某筆賣出」與「某筆買進」的沖銷股數；在 庫存損益、投資績效、個股明細等頁面選擇「自定沖銷」時會依此規則計算損益。")

sess = get_session()
try:
    trades = sess.query(Trade).order_by(Trade.trade_date, Trade.id).all()
    masters = {m.stock_id: m for m in sess.query(StockMaster).all()}
    rules = sess.query(CustomMatchRule).all()
except Exception:
    trades = []
    masters = {}
    rules = []

# 依交易 ID 查詢
trade_by_id = {t.id: t for t in trades}
sells = [t for t in trades if (t.side or "").upper() == "SELL"]
buys = [t for t in trades if (t.side or "").upper() == "BUY"]

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
        rows_sell.append({
            "交易ID": t.id,
            "股票": f"{t.stock_id} {name}".strip(),
            "日期": str(t.trade_date),
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
    # 用 session_state 記住選中的列，預設第一筆
    if "add_sell_idx" not in st.session_state:
        st.session_state["add_sell_idx"] = 0
    sell_idx = st.session_state["add_sell_idx"]
    if sell_idx >= len(sell_indices):
        sell_idx = 0
        st.session_state["add_sell_idx"] = 0
    df_sells_display = df_sells.copy()
    df_sells_display.insert(0, "勾選", [i == sell_idx for i in sell_indices])
    edited_sell = st.data_editor(
        df_sells_display,
        use_container_width=True,
        hide_index=True,
        key="add_sell_editor",
        column_config={
            "勾選": st.column_config.CheckboxColumn("勾選", width="small", required=True),
        },
        disabled=["交易ID", "股票", "日期", "當沖", "賣出股數", "已配", "剩餘可配"],
    )
    # 從編輯結果取回選中的列（只保留一個勾選）
    checked = edited_sell.index[edited_sell["勾選"]].tolist()
    if len(checked) == 1:
        st.session_state["add_sell_idx"] = int(checked[0])
        sell_idx = int(checked[0])
    elif len(checked) > 1:
        st.session_state["add_sell_idx"] = int(checked[-1])
        sell_idx = int(checked[-1])
    sell_id = int(df_sells.iloc[sell_idx]["交易ID"]) if sell_indices else None
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
            st.markdown("**2. 選擇「買進」交易（與上列賣出沖銷）**")
            rows_buy = []
            for t in same_stock_buys:
                used = buy_used[t.id]
                remain = max(0, t.quantity - used)
                if filter_has_remain and remain <= 0:
                    continue
                name = (masters.get(t.stock_id).name if masters.get(t.stock_id) else "") or ""
                rows_buy.append({
                    "交易ID": t.id,
                    "股票": f"{t.stock_id} {name}".strip(),
                    "日期": str(t.trade_date),
                    "當沖": bool(getattr(t, "is_daytrade", False)),
                    "買進股數": t.quantity,
                    "單價": t.price,
                    "已配": used,
                    "剩餘可配": remain,
                })
            df_buys = pd.DataFrame(rows_buy)
            sort_buy = st.selectbox(
                "買進列表排序",
                ["依日期（新→舊）", "依日期（舊→新）", "依單價", "依剩餘可配（多→少）"],
                key="sort_buy",
            )
            if "日期（新→舊）" in sort_buy:
                df_buys = df_buys.sort_values("日期", ascending=False)
            elif "日期（舊→新）" in sort_buy:
                df_buys = df_buys.sort_values("日期", ascending=True)
            elif "單價" in sort_buy:
                df_buys = df_buys.sort_values("單價", ascending=False)
            else:
                df_buys = df_buys.sort_values("剩餘可配", ascending=False)
            df_buys = df_buys.reset_index(drop=True)
            buy_indices = list(range(len(df_buys)))
            if "add_buy_idx" not in st.session_state:
                st.session_state["add_buy_idx"] = 0
            buy_idx = st.session_state["add_buy_idx"]
            if buy_idx >= len(buy_indices):
                buy_idx = 0
                st.session_state["add_buy_idx"] = 0
            df_buys_display = df_buys.copy()
            df_buys_display.insert(0, "勾選", [i == buy_idx for i in buy_indices])
            edited_buy = st.data_editor(
                df_buys_display,
                use_container_width=True,
                hide_index=True,
                key="add_buy_editor",
                column_config={
                    "勾選": st.column_config.CheckboxColumn("勾選", width="small", required=True),
                },
                disabled=["交易ID", "股票", "日期", "當沖", "買進股數", "單價", "已配", "剩餘可配"],
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
                        st.caption(f"此賣出與此買進已有規則（沖銷 {existing.matched_qty} 股）。若要修改請先刪除該筆規則再新增。")
                    else:
                        # 在沖銷股數上方列出被勾選的賣出／買進，方便判斷
                        st.markdown("**本次配對**")
                        col_sell_summary, col_buy_summary = st.columns(2)
                        with col_sell_summary:
                            st.caption("勾選的賣出")
                            nm = (masters.get(sell_trade.stock_id).name if masters.get(sell_trade.stock_id) else "") or ""
                            st.markdown(f"**#{sell_id}** {sell_trade.stock_id} {nm} · {sell_trade.trade_date} · 賣出 **{sell_trade.quantity}** 股（已配 {sell_used[sell_id]}，剩餘可配 **{sell_remain}**）")
                        with col_buy_summary:
                            st.caption("勾選的買進")
                            nm = (masters.get(buy_trade.stock_id).name if masters.get(buy_trade.stock_id) else "") or ""
                            st.markdown(f"**#{buy_id}** {buy_trade.stock_id} {nm} · {buy_trade.trade_date} · 買進 **{buy_trade.quantity}** 股 @ {buy_trade.price}（已配 {buy_used[buy_id]}，剩餘可配 **{buy_remain}**）")
                        st.markdown("")  # 空一行
                        qty = st.number_input("沖銷股數", min_value=1, max_value=max_qty, value=min(1, max_qty), key="add_qty")
                        if st.button("新增此筆規則", key="add_rule_btn"):
                            try:
                                sess.add(CustomMatchRule(sell_trade_id=sell_id, buy_trade_id=buy_id, matched_qty=qty))
                                sess.commit()
                                st.success(f"已新增規則：賣出 #{sell_id} 與 買進 #{buy_id} 沖銷 {qty} 股")
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
        if filter_paired_id:
            rules_list = [r for r in rules_list if (trade_by_id.get(r.sell_trade_id) and trade_by_id.get(r.sell_trade_id).stock_id == filter_paired_id)]
        # 表頭
        h1, h2, h3, h4, h5, h6, h7, h8, h9 = st.columns([1.2, 0.8, 0.8, 0.8, 0.8, 0.8, 0.6, 1.2, 0.6])
        with h1: st.markdown("**股票**")
        with h2: st.markdown("**賣出ID**")
        with h3: st.markdown("**賣出日**")
        with h4: st.markdown("**買進ID**")
        with h5: st.markdown("**買進日**")
        with h6: st.markdown("**沖銷股數**")
        with h7: st.markdown("**當沖**")
        with h8: st.markdown("**操作**")
        with h9: st.markdown("")
        st.markdown("---")
        for r in rules_list:
            st_t = trade_by_id.get(r.sell_trade_id)
            buy_t = trade_by_id.get(r.buy_trade_id)
            st_date = str(st_t.trade_date) if st_t else "—"
            buy_date = str(buy_t.trade_date) if buy_t else "—"
            is_dt = "是" if (st_t and getattr(st_t, "is_daytrade", False)) or (buy_t and getattr(buy_t, "is_daytrade", False)) or (st_t and buy_t and st_t.trade_date == buy_t.trade_date) else "否"
            sid = st_t.stock_id if st_t else (buy_t.stock_id if buy_t else "—")
            name = (masters.get(sid).name if masters.get(sid) else "") or ""
            stock_label = f"{sid} {name}".strip()
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
            c1, c2, c3, c4, c5, c6, c7, c8, c9 = st.columns([1.2, 0.8, 0.8, 0.8, 0.8, 0.8, 0.6, 1.2, 0.6])
            with c1: st.caption(stock_label)
            with c2: st.caption(str(r.sell_trade_id))
            with c3: st.caption(st_date)
            with c4: st.caption(str(r.buy_trade_id))
            with c5: st.caption(buy_date)
            with c6:
                new_qty = st.number_input("股數", min_value=1, max_value=max_new_qty, value=min(cur, max_new_qty), key=key_qty, label_visibility="collapsed")
            with c7: st.caption(is_dt)
            with c8:
                if st.button("確認", key=key_mod, type="primary"):
                    if new_qty != cur:
                        try:
                            r.matched_qty = new_qty
                            sess2.commit()
                            st.success(f"已修改 賣出 #{r.sell_trade_id} ↔ 買進 #{r.buy_trade_id} 為 **{new_qty}** 股")
                            st.rerun()
                        except OperationalError:
                            sess2.rollback()
                            st.error("無法寫入資料庫（目前環境可能唯讀）")
                        except Exception as e:
                            sess2.rollback()
                            st.error(f"修改失敗：{e}")
            with c9:
                if st.button("刪除", key=key_del):
                    try:
                        sess2.delete(r)
                        sess2.commit()
                        st.success("已刪除該筆規則")
                        st.rerun()
                    except OperationalError:
                        sess2.rollback()
                        st.error("無法寫入資料庫（目前環境可能唯讀）")
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
