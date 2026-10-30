# -*- coding: utf-8 -*-
"""交易匯入頁「〇、券商對帳單（證券 App 匯出）」的畫面與寫入（docs/specs/對帳單匯入.md）。

判斷邏輯都在 services/broker_statement.py（純函式、有測試）；這裡只負責讀資料庫、顯示預覽、寫入。
平常整份給一個買賣人；勾「有幾筆要算在別人帳上」才逐筆改人（規格 §5.1），之後每個人各自判斷、各自配對。
"""
from datetime import datetime
from types import SimpleNamespace

import pandas as pd
import streamlit as st
from sqlalchemy.exc import OperationalError

from db.database import get_session
from db.models import CustomMatchRule, StockMaster, Trade
from services import trade_ref_service as trs
from services.broker_statement import (
    DUP_IMPORT, KEYED_EXACT, KEYED_MERGED, NEW, REVIEW, SELL_STRATEGIES, STATUS_LABEL,
    assign_tmp_ids, build_name_map, classify_rows, mark_cross_user_dups, parse_statement,
    plan_daytrade, plan_sell_matches,
)
from services.pnl_engine import net_pnl_for_match
from services.prefs import resolve_default_trader
from services.trader_service import all_trader_names


def _read_records(f):
    for enc in ("utf-8-sig", "big5"):
        try:
            f.seek(0)
            return pd.read_csv(f, dtype=str, keep_default_na=False, encoding=enc).to_dict("records")
        except Exception:
            continue
    return None


def _load_base():
    """主檔、所有人還有效的書號（跨人防重複用）。"""
    sess = get_session()
    try:
        masters = [(m.stock_id, m.name) for m in sess.query(StockMaster).all()]
        live_refs = trs.load_live_refs_all_users(sess)
        sess.commit()   # 第一次用到時建 trade_refs 表，先存起來
        return masters, live_refs
    finally:
        sess.close()


def _load_user(user):
    """這個人的交易、跟這些交易有關的沖銷規則、書號紀錄。"""
    sess = get_session()
    try:
        trades = sess.query(Trade).filter(Trade.user == user).order_by(Trade.id).all()
        tids = {int(t.id) for t in trades}
        rules = [
            (int(r.sell_trade_id), int(r.buy_trade_id), int(r.matched_qty))
            for r in sess.query(CustomMatchRule)
            .order_by(CustomMatchRule.sell_trade_id, CustomMatchRule.buy_trade_id).all()
            if int(r.sell_trade_id) in tids or int(r.buy_trade_id) in tids
        ]
        refs = trs.load_refs(sess, user)
        sess.expunge_all()   # 脫離 session：之後 session 關掉還能讀欄位
        return SimpleNamespace(trades=trades, rules=rules, refs=refs)
    finally:
        sess.close()


def _assign_users(rows, main_user, traders, name_by_id, editor_key):
    """勾了「有幾筆要算在別人帳上」才出現逐筆改人的表格；回傳 {seq: 買賣人}。"""
    assign = {r["seq"]: main_user for r in rows}
    active = [r for r in rows if r["status"] is None]
    if not active or not st.checkbox(
        "有幾筆要算在別人帳上（少數情況才勾；平常整份都給上面選的人）", key="bs_split",
    ):
        return assign, False
    st.caption("在第一欄「買賣人」把要給別人的那幾筆改掉，其他維持不動。已匯入過的不會列在這裡。")
    df = pd.DataFrame([{
        "seq": r["seq"], "日期": f"{r['trade_date']:%m/%d}", "股票": f"{r['stock_id']} {name_by_id.get(r['stock_id'], '')}",
        "買/賣": "買" if r["side"] == "BUY" else "賣", "股數": r["quantity"], "價格": r["price"],
        "委託書號": r["order_no"], "買賣人": main_user,
    } for r in active])
    ed = st.data_editor(
        df, key=editor_key, hide_index=True, use_container_width=True,
        # 買賣人放第一欄：手機寬度時右邊會被截掉，要改的欄位一定要看得到
        column_order=["買賣人", "日期", "股票", "買/賣", "股數", "價格", "委託書號"],
        disabled=["日期", "股票", "買/賣", "股數", "價格", "委託書號"],
        column_config={
            "買賣人": st.column_config.SelectboxColumn("買賣人", options=traders, required=True),
            "股數": st.column_config.NumberColumn(format="%d"),
        },
    )
    for _, x in ed.iterrows():
        if x["買賣人"]:
            assign[int(x["seq"])] = str(x["買賣人"])
    return assign, True


def render_broker_statement_section():
    st.subheader("〇、券商對帳單（證券 App 匯出）")
    st.caption(
        "上傳證券 App 盤後匯出的成交明細 CSV（股名、日期、成交股數、淨收付、成交單價、手續費、交易稅、委託書號…）。"
        "會自動略過**已匯入過**（同一個委託書號）和**已經手 key** 的交易，只補沒 key 的；當沖會自動配對。"
    )
    msg = st.session_state.pop("bs_done_msg", None)
    if msg:
        st.success(msg)

    traders = all_trader_names()
    default = resolve_default_trader(traders)
    c1, c2 = st.columns([1, 2])
    main_user = c1.selectbox(
        "匯入到哪個買賣人", traders, key="bs_user",
        index=traders.index(default) if default in traders else 0,
        help="Peggy姐、雅雲會互相幫忙匯，記得選對人。",
    )
    c2.markdown(f"#### 目前要匯入：:blue[{main_user or '（未選）'}]")
    f = st.file_uploader("上傳對帳單 CSV", type=["csv"], key="bs_file")
    if f is None or not main_user:
        return
    recs = _read_records(f)
    if recs is None:
        st.error("讀不到這個檔案，請確認是證券 App 匯出的 CSV。")
        return

    masters, live_refs = _load_base()
    name_by_id = dict(masters)
    rows, errors = parse_statement(recs, build_name_map(masters))
    mark_cross_user_dups(rows, live_refs)
    if errors:
        st.warning(f"有 {len(errors)} 列不會匯入：")
        st.dataframe(pd.DataFrame([{"列": e[0], "原因": e[1]} for e in errors]),
                     use_container_width=True, hide_index=True)
    if not rows:
        return

    summary_slot = st.empty()   # 摘要放最上面，但要等下面勾選、改人、選沖銷方式才算得出來
    assign, split = _assign_users(rows, main_user, traders, name_by_id,
                                  editor_key=f"bs_split_ed_{main_user}_{getattr(f, 'name', '')}")

    # 每個人各自判斷「已手 key」、各自配當沖、各自從自己的庫存沖
    users = [main_user] + sorted({assign[r["seq"]] for r in rows if r["status"] is None} - {main_user})
    ctx = {u: _load_user(u) for u in users}
    done = [r for r in rows if r["status"] is not None]          # 跨人比對已是「已匯入過」
    for r in done:
        r["user"] = r.get("dup_user") or main_user
    for u in users:
        mine = [r for r in rows if r["status"] is None and assign[r["seq"]] == u]
        for r in classify_rows(mine, ctx[u].trades, ctx[u].refs):
            r["user"] = u
            done.append(r)
    rows = sorted(done, key=lambda r: r["seq"])

    # ⚠️ 要確認的組：預設不匯，勾了才匯
    review_groups = {}
    for r in rows:
        if r["status"] == REVIEW:
            review_groups.setdefault((r["user"], r["trade_date"], r["stock_id"], r["side"]), []).append(r)
    force = set()
    if review_groups:
        st.markdown("##### ⚠️ 要確認：系統裡已經有手 key 的交易，但價格或股數對不起來（預設不匯，避免重複算）")
        for (u, d, sid, side), grp in review_groups.items():
            desc = "、".join(f"{r['price']:g} × {r['quantity']:,}" for r in grp)
            if st.checkbox(
                f"{u}｜{d:%m/%d} {sid} {name_by_id.get(sid, '')} {'買' if side == 'BUY' else '賣'}｜"
                f"手 key：{grp[0]['review_with']}｜對帳單：{desc} → 仍要匯入對帳單這幾筆",
                key=f"bs_force_{u}_{d}_{sid}_{side}",
            ):
                force.add((u, d, sid, side))

    to_import = [r for r in rows if r["status"] == NEW
                 or (r["status"] == REVIEW and (r["user"], r["trade_date"], r["stock_id"], r["side"]) in force)]
    assign_tmp_ids(to_import)   # seq 全檔唯一，所以暫時 id 跨人也不會撞
    new_by_user = {u: [r for r in to_import if r["user"] == u] for u in users}

    dt_rules, dt_flag = [], set()
    for u in users:
        rr, ff = plan_daytrade(new_by_user[u], ctx[u].trades, ctx[u].rules)
        dt_rules += rr
        dt_flag |= ff

    # 當沖以外還有新賣出 → 選統一沖銷方式（所有人共用一種）
    dt_sold = {}
    for s_id, _b, q in dt_rules:
        dt_sold[s_id] = dt_sold.get(s_id, 0) + q
    strategy = "NONE"
    if any(r["side"] == "SELL" and r["quantity"] > dt_sold.get(r["tmp_id"], 0) for r in to_import):
        strategy = st.radio(
            "賣出要沖哪幾筆買進（當沖以外的賣出，這次統一用同一種方式；之後可到「自定沖銷設定」個別改）",
            list(SELL_STRATEGIES), format_func=lambda k: SELL_STRATEGIES[k], key="bs_strategy",
        )
    sell_rules, shortage = [], {}
    for u in users:
        rr, sh = plan_sell_matches(new_by_user[u], ctx[u].trades, ctx[u].rules + dt_rules, strategy)
        sell_rules += rr
        shortage.update(sh)

    # 同一對（賣, 買）合併成一條規則（當沖跟統一沖銷可能沖到同一筆買進）
    all_rules = {}
    for s_id, b_id, q in dt_rules + sell_rules:
        all_rules[(s_id, b_id)] = all_rules.get((s_id, b_id), 0) + q

    # 預估這次多出的已實現損益（淨額，扣手續費稅）與每筆賣出沖到哪
    by_id = {int(t.id): t for u in users for t in ctx[u].trades}
    for r in to_import:
        by_id[r["tmp_id"]] = SimpleNamespace(id=r["tmp_id"], quantity=r["quantity"], price=r["price"],
                                             fee=r["fee"], tax=r["tax"], trade_date=r["trade_date"])
    pnl, match_text = 0.0, {}
    for (s_id, b_id), q in all_rules.items():
        b, s = by_id[b_id], by_id[s_id]
        pnl += net_pnl_for_match((b_id, s_id, q, b.price, s.price, (s.price - b.price) * q), by_id)
        match_text.setdefault(s_id, []).append(f"{b.trade_date:%m/%d} {b.price:g}×{q:,}")

    keyed = [r for r in rows if r["status"] in (KEYED_EXACT, KEYED_MERGED)]
    fee_updates = [r["fee_update"] for r in keyed if r["fee_update"]]
    cnt = {k: sum(1 for r in rows if r["status"] == k) for k in STATUS_LABEL}
    per_user = [(u, sum(1 for r in to_import if r["user"] == u)) for u in users]
    per_user_txt = "、".join(f"{u} {n}" for u, n in per_user if n)
    multi = len([1 for _u, n in per_user if n]) > 1
    summary_slot.markdown(
        f"**共 {len(rows)} 筆** → 🆕 新增 **{len(to_import)}**"
        + (f"（{per_user_txt}）" if multi else
           f"（買 {sum(r['side'] == 'BUY' for r in to_import)}／賣 {sum(r['side'] == 'SELL' for r in to_import)}）")
        + f"、✋ 已手 key {len(keyed)}、⏭️ 已匯入過 {cnt[DUP_IMPORT]}、⚠️ 要確認 {cnt[REVIEW]}"
        + (f"；當沖 {len(dt_rules)} 組" if dt_rules else "")
        + (f"；預估這次多出已實現損益 **{pnl:+,.0f}**" if all_rules else "")
    )
    if shortage:
        sid_of = {r["tmp_id"]: (r["user"], r["stock_id"]) for r in to_import}
        st.error("庫存不足：" + "、".join(
            f"{sid_of[s][0]} {by_id[s].trade_date:%m/%d} {sid_of[s][1]} 少 {q:,} 股" for s, q in shortage.items())
            + "（可能還有更早的買進沒 key；仍可匯入，沖不到的部分之後再補）")

    import_seqs = {r["seq"] for r in to_import}

    def view(r):
        tid = r.get("tmp_id") if r["seq"] in import_seqs else None
        status = STATUS_LABEL[r["status"]]
        if r["status"] == DUP_IMPORT and r.get("dup_user") and r["dup_user"] != main_user:
            status += f"（{r['dup_user']}）"
        if r["status"] == REVIEW and tid is not None:
            status += "（勾了要匯）"
        v = {"狀態": status}
        if split or multi:
            v["買賣人"] = r["user"]
        v.update({
            "日期": f"{r['trade_date']:%Y/%m/%d}", "股票": f"{r['stock_id']} {r['stock_name']}",
            "買/賣": "買" if r["side"] == "BUY" else "賣", "股數": r["quantity"], "價格": r["price"],
            "手續費": r["fee"], "稅": r["tax"], "委託書號": r["order_no"],
            "當沖": "✓" if tid is not None and tid in dt_flag else "",
            "沖銷到": "、".join(match_text.get(tid, [])) if tid is not None else "",
        })
        return v

    fmt = {"股數": "{:,.0f}", "價格": "{:,.2f}", "手續費": "{:,.0f}", "稅": "{:,.0f}"}
    main = [r for r in rows if r["status"] in (NEW, REVIEW)]
    other = [r for r in rows if r["status"] not in (NEW, REVIEW)]
    if main:
        st.dataframe(pd.DataFrame([view(r) for r in main]).style.format(fmt),
                     use_container_width=True, hide_index=True)
    if other:
        with st.expander(f"已在系統裡、這次不匯的 {len(other)} 筆"):
            st.dataframe(pd.DataFrame([view(r) for r in other]).style.format(fmt),
                         use_container_width=True, hide_index=True)

    do_fee = False
    if fee_updates:
        do_fee = st.checkbox(
            f"用對帳單的手續費／稅更新手 key 的交易（{len(fee_updates)} 筆；對帳單才是實際扣的金額）",
            value=True, key="bs_fee_update",
        )
        with st.expander("會改哪幾筆"):
            st.dataframe(pd.DataFrame([{
                "交易 id": u["trade_id"], "原手續費": u["old_fee"], "→ 手續費": u["fee"],
                "原稅": u["old_tax"], "→ 稅": u["tax"]} for u in fee_updates]),
                use_container_width=True, hide_index=True)

    if to_import:
        label = (f"確認匯入 {len(to_import)} 筆（{per_user_txt}）" if multi
                 else f"確認匯入 {len(to_import)} 筆到「{to_import[0]['user']}」")
    else:
        label = "沒有要新增的（只補上書號）" if keyed else "沒有要匯入的"
    if not st.button(label, type="primary", key="bs_do_import", disabled=not (to_import or keyed)):
        return

    sess = get_session()
    try:
        trs.ensure_table(sess)
        id_map = {}
        for r in sorted(to_import, key=lambda x: x["seq"]):
            t = Trade(user=r["user"], stock_id=r["stock_id"], trade_date=r["trade_date"], side=r["side"],
                      price=r["price"], quantity=r["quantity"], is_daytrade=r["tmp_id"] in dt_flag,
                      fee=r["fee"], tax=r["tax"], note=r["note"])
            sess.add(t)
            sess.flush()
            id_map[r["tmp_id"]] = int(t.id)
        for tid in dt_flag:
            if tid > 0:   # 當天手 key、這次配成當沖的既有交易
                t = sess.get(Trade, tid)
                if t is not None:
                    t.is_daytrade = True
        for (s_id, b_id), q in all_rules.items():
            sess.add(CustomMatchRule(sell_trade_id=id_map.get(s_id, s_id),
                                     buy_trade_id=id_map.get(b_id, b_id), matched_qty=int(q)))
        batch_id = datetime.now().strftime("%Y%m%d%H%M%S")
        for u in users:
            refs_new = [(r["trade_date"], r["order_no"], id_map[r["tmp_id"]], "import")
                        for r in to_import if r["user"] == u]
            refs_new += [(r["trade_date"], r["order_no"], r["claim_trade_id"], "backfill")
                         for r in keyed if r["user"] == u]
            trs.add_refs(sess, u, refs_new, batch_id)
        if do_fee:
            for upd in fee_updates:
                t = sess.get(Trade, upd["trade_id"])
                if t is not None:
                    t.fee, t.tax = upd["fee"], upd["tax"]
        sess.commit()
        st.session_state.pop("bs_split", None)   # 下一份對帳單預設回到整份給一個人
        st.session_state["bs_done_msg"] = (
            f"已匯入 {len(to_import)} 筆" + (f"（{per_user_txt}）" if multi else f"到「{to_import[0]['user']}」" if to_import else "")
            + (f"、補上書號 {len(keyed)} 筆" if keyed else "")
            + (f"、更新手續費／稅 {len(fee_updates)} 筆" if do_fee else "")
            + "。下面是同一個檔重新檢查的結果，應該全部變成「已匯入過／已手 key」。"
        )
        st.rerun()
    except OperationalError:
        sess.rollback()
        st.error("無法寫入資料庫（目前環境為唯讀）。")
    except Exception as e:
        sess.rollback()
        st.error(f"匯入失敗，什麼都沒寫入：{e}")
    finally:
        sess.close()
