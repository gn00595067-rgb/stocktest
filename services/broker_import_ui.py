# -*- coding: utf-8 -*-
"""交易匯入頁「〇、券商對帳單（證券 App 匯出）」的畫面與寫入（docs/specs/對帳單匯入.md）。

判斷邏輯都在 services/broker_statement.py（純函式、有測試）；這裡只負責讀資料庫、顯示預覽、寫入。
平常整份給一個買賣人；勾「有幾筆要算在別人帳上」才逐筆改人（規格 §5.1），之後每個人各自判斷、各自配對。
"""
import csv
import io
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
    plan_daytrade, plan_sell_matches, settlement_by_user_date, table_from_lines,
    orphan_keyed_trades, plan_review_fix, has_order_no, same_day_dup_counts, content_key,
    cross_user_same_content, available_buys, check_picks, pending_sells,
)
from services.pnl_engine import net_pnl_for_match
from services.prefs import resolve_default_trader
from services.trader_service import all_trader_names


def _read_records(f):
    """→ (records, 第一筆的檔案列號)；讀不到回 None。表頭前的說明列由 table_from_lines 略過。"""
    f.seek(0)
    raw = f.read()
    for enc in ("utf-8-sig", "big5"):
        try:
            return table_from_lines(list(csv.reader(io.StringIO(raw.decode(enc)))))
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


def _other_users_trades(user, days):
    """別的買賣人在這些日期的交易（沒書號的檔用來提醒「可能之前匯給了別人」）。"""
    if not days:
        return []
    sess = get_session()
    try:
        out = (sess.query(Trade).filter(Trade.user != user, Trade.trade_date >= min(days), Trade.trade_date <= max(days))
               .order_by(Trade.id).all())
        sess.expunge_all()
        return out
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


# §7.1 少數幾筆另外指定：表格裡的選項文字 → 方式（SAME＝照上面統一的、PICK＝自己挑買進）
_OVR_SAME, _OVR_PICK = "SAME", "PICK"
_OVR_LABEL = {
    "照上面的方式": _OVR_SAME, "先進先出": "FIFO", "保守：成本高的先沖": "CONSERVATIVE",
    "樂觀：成本低的先沖": "OPTIMISTIC", "均價：最接近平均成本": "MEAN", "不指定（不沖）": "NONE",
    "自己挑買進": _OVR_PICK,
}


def _sell_overrides(users, new_by_user, ctx, dt_rules, strategy, name_by_id, key_sfx):
    """勾了「有幾筆賣出要另外指定」才出現；回傳 (overrides {賣出 tmp_id: 方式}, 自己挑的規則, 有沒有挑超過)。"""
    pend = [(u, r) for u in users for r in pending_sells(new_by_user[u], ctx[u].rules + dt_rules)]
    if not pend or not st.checkbox(
        "有幾筆賣出要另外指定沖銷（例如某一筆要沖特定那筆買進；其他照上面的方式）", key="bs_ovr",
    ):
        return {}, [], False
    # 2026-11-10 Elsa：原本是一張賣出表、要點兩下改「沖銷方式」才出現買進，看不懂 → 改成先勾賣出、直接列買進
    multi = len({u for u, _r in pend}) > 1

    def sell_label(u, r):
        return (f"{u}｜" if multi else "") + (
            f"{r['trade_date']:%m/%d} {r['stock_id']} {name_by_id.get(r['stock_id'], '')} 賣 {r['price']:g} × {r['remain']:,}"
            + (f"（書號 {r['order_no']}）" if r.get("order_no") else ""))

    by_seq = {r["seq"]: (u, r) for u, r in pend}
    chosen = st.multiselect(
        "① 哪幾筆**賣出**要另外指定？（沒選的照上面的方式）", list(by_seq),
        format_func=lambda q: sell_label(*by_seq[q]), key=f"bs_ovr_pick_{key_sfx}",
        placeholder="點這裡選賣出（可以選好幾筆）",
    )
    if chosen:
        st.caption("② 下面每筆賣出各有一張**可以沖的買進**，在「沖這筆幾股」填要沖的股數。")
    overrides, pick_rules, picks, sell_qty, avail_qty, label_of = {}, [], {}, {}, {}, {}
    how_opts = list(_OVR_LABEL)[1:]   # 已經選了要另外指定，不再列「照上面的方式」
    for seq in chosen:
        u, r = by_seq[seq]
        st.markdown("---")
        how = _OVR_LABEL[st.selectbox(
            f"**{sell_label(u, r)}** 怎麼沖", how_opts, key=f"bs_ovr_how_{key_sfx}_{seq}",
            index=how_opts.index("自己挑買進"),
        )]
        if how not in (_OVR_SAME, _OVR_PICK):
            overrides[r["tmp_id"]] = how
        if how != _OVR_PICK:
            continue
        lots = available_buys(r, new_by_user[u], ctx[u].trades, ctx[u].rules + dt_rules)
        if not lots:
            st.warning("賣出日以前沒有還沒沖完的買進可以挑（以前的賣出已經把庫存沖完）。")
            continue
        for b in lots:
            avail_qty[b.trade_id] = b.qty
            label_of[b.trade_id] = f"{b.date[5:].replace('-', '/')} {b.price:g}"
        bdf = pd.DataFrame([{
            "buy_id": b.trade_id, "沖這筆幾股": 0, "買進日期": b.date.replace("-", "/"), "價格": b.price,
            "來源": "這次匯入" if b.trade_id < 0 else "原本庫存", "還沒沖": b.qty,
        } for b in lots])
        bed = st.data_editor(
            bdf, hide_index=True, use_container_width=True,
            key=f"bs_pick_{key_sfx}_{r['seq']}_{abs(hash(tuple(b.trade_id for b in lots)))}",
            column_order=["沖這筆幾股", "買進日期", "價格", "來源", "還沒沖"],
            disabled=["買進日期", "價格", "來源", "還沒沖"],
            column_config={
                "沖這筆幾股": st.column_config.NumberColumn(min_value=0, step=1, format="%d"),
                "價格": st.column_config.NumberColumn(format="%.2f"),
                "還沒沖": st.column_config.NumberColumn(format="%d"),
            },
        )
        mine = {int(x["buy_id"]): int(x["沖這筆幾股"] or 0) for _, x in bed.iterrows() if int(x["沖這筆幾股"] or 0) > 0}
        picks[r["tmp_id"]] = mine
        sell_qty[r["tmp_id"]] = r["remain"]
        got = sum(mine.values())
        if got < r["remain"]:
            rest = "不沖（上面選了不指定）" if strategy == "NONE" else "照上面的方式補沖"
            st.caption(f"挑了 {got:,} 股，還差 {r['remain'] - got:,} 股{rest}。")
        pick_rules += [(r["tmp_id"], b, q) for b, q in mine.items()]
    errs = check_picks(picks, sell_qty, avail_qty)
    for kind, i, over in errs:
        st.error(f"挑太多：這筆賣出多挑了 {over:,} 股" if kind == "sell"
                 else f"挑太多：{label_of.get(i, i)} 那筆買進只剩 {avail_qty[i]:,} 股，挑了 {avail_qty[i] + over:,} 股"
                 "（有幾筆賣出挑同一筆時是合計）")
    return overrides, pick_rules, bool(errs)


def render_broker_statement_section():
    st.subheader("〇、券商對帳單（證券 App 匯出）")
    st.caption(
        "上傳證券 App 盤後匯出的成交明細 CSV（股名、日期、成交股數、淨收付、成交單價、手續費、交易稅、委託書號…）。"
        "會自動略過**已匯入過**（同一個委託書號）和**已經手 key** 的交易，只補沒 key 的；當沖會自動配對。"
        "沒有委託書號的格式（成交日期、類別、股票名稱(代號)…）也可以：用日期、股票、買賣、價格、股數比對。"
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
    read = _read_records(f)
    if read is None:
        st.error("讀不到這個檔案，請確認是證券 App 匯出的 CSV。")
        return
    recs, first_line = read

    masters, live_refs = _load_base()
    name_by_id = dict(masters)
    with_order = has_order_no(recs)
    rows, errors = parse_statement(recs, build_name_map(masters), first_line, master_ids=[m[0] for m in masters])
    mark_cross_user_dups(rows, live_refs)
    if not with_order and rows:
        st.info("這份對帳單**沒有委託書號**：會把它當成檔案日期內的全部交易，"
                "用日期、股票、買賣、價格、股數跟系統裡的交易比對；系統裡已有的不會重複匯入。")
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

    dup_counts = same_day_dup_counts(rows)
    if not with_order:
        n_same = [k for k, v in dup_counts.items() if v > 1]
        if n_same:
            st.warning(
                f"檔案裡同一天有完全相同的交易 {len(n_same)} 組（同股、同價、同股數；預覽表「同日相同」那欄）："
                + "、".join(f"{k[0]:%m/%d} {name_by_id.get(k[1], k[1])} {k[3]:g} × {k[4]:,} ×{dup_counts[k]}" for k in n_same[:6])
                + ("…" if len(n_same) > 6 else "")
                + "。分開成交是正常的，**全部照匯**；請跟券商 App 核對筆數。")
        if all(r["status"] in (KEYED_EXACT, KEYED_MERGED, DUP_IMPORT) for r in rows):
            st.success(f"#### ✅ 這份檔案的 {len(rows)} 筆都已經在系統裡了\n可能是重複匯入，不用再匯。")
        days = {r["trade_date"] for r in rows}
        pending = [r for r in rows if r["status"] not in (KEYED_EXACT, KEYED_MERGED, DUP_IMPORT)]
        cross = cross_user_same_content(pending, [t for t in _other_users_trades(main_user, days) if t.user not in users])
        if cross:
            by_u = {}
            for _r, u in cross:
                by_u[u] = by_u.get(u, 0) + 1
            st.warning(
                "⚠️ 別的買賣人在同一天有一模一樣的交易："
                + "、".join(f"{u} {n} 筆" for u, n in by_u.items())
                + "。可能這份之前匯給了別人，請確認「匯入到哪個買賣人」有沒有選對（只提醒，不擋）。")
            with st.expander("看是哪幾筆"):
                st.dataframe(pd.DataFrame([{
                    "已在誰的帳上": u, "日期": f"{r['trade_date']:%Y/%m/%d}", "股票": f"{r['stock_id']} {r['stock_name']}",
                    "買/賣": "買" if r["side"] == "BUY" else "賣", "股數": r["quantity"], "價格": r["price"],
                } for r, u in cross]), use_container_width=True, hide_index=True)

    # ⚠️ 要確認的組：預設先不處理；可選「照對帳單修正手 key 的那筆」或「另外匯入」（Peggy 2026-11-06：key 錯的要修正）
    review_groups = {}
    for r in rows:
        if r["status"] == REVIEW:
            review_groups.setdefault((r["user"], r["trade_date"], r["stock_id"], r["side"]), []).append(r)
    force, fixes = set(), []   # fixes：[(組, 修正內容)]
    if review_groups:
        st.markdown("##### ⚠️ 要確認：系統裡已經有手 key 的交易，但價格或股數對不起來")
        for gk, grp in review_groups.items():
            u, d, sid, side = gk
            desc = "、".join(f"{r['price']:g} × {r['quantity']:,}" for r in grp)
            tids = grp[0].get("review_trade_ids") or []
            opts = ["skip"] + (["fix"] if len(tids) == 1 else []) + ["import"]
            choice = st.radio(
                f"{u}｜{d:%m/%d} {sid} {name_by_id.get(sid, '')} {'買' if side == 'BUY' else '賣'}｜"
                f"手 key：{grp[0]['review_with']}｜對帳單：{desc}",
                opts, horizontal=True, key=f"bs_review_{u}_{d}_{sid}_{side}",
                format_func={"skip": "先不處理", "fix": "✏️ 手 key 打錯了：照對帳單修正那筆",
                             "import": "手 key 那筆是另一筆交易：對帳單照樣匯入"}.get,
            )
            if choice == "import":
                force.add(gk)
            elif choice == "fix":
                t0 = next(t for t in ctx[u].trades if int(t.id) == tids[0])
                fx = plan_review_fix(grp, t0)
                fixes.append((gk, fx))
                if fx["qty_changed"]:
                    st.caption(f"　↳ 股數會從 {int(t0.quantity):,} 改成 {fx['quantity']:,}，這筆原本的沖銷配對會清掉、改用預設沖銷"
                               "（要指定再到「自定沖銷設定」）。")
        if len(review_groups) > len(fixes) + len(force):
            st.caption("「先不處理」的組這次不匯；可以先到仿Yahoo 改手 key 的那筆，再重新上傳。")
    fix_keys = {gk for gk, _ in fixes}
    for r in rows:   # 選了修正的組：視同已手 key（補書號、不另外匯入）
        gk = (r["user"], r["trade_date"], r["stock_id"], r["side"])
        if r["status"] == REVIEW and gk in fix_keys:
            r["claim_trade_id"] = r["review_trade_ids"][0]

    # 對帳單日期內、手 key 了但對帳單裡沒有的：多半 key 錯股票／日期／買賣，勾了才刪
    orphan_del = set()
    orphans = [(u, t) for u in users
               for t in orphan_keyed_trades([r for r in rows if r["user"] == u], ctx[u].trades, ctx[u].refs,
                                            whole_range=not with_order)]
    if orphans:
        with st.expander(f"⚠️ 手 key 了、但對帳單裡沒有的交易 {len(orphans)} 筆（可能 key 錯股票、日期或買賣；勾了會刪掉）",
                         expanded=True):
            for u, t in orphans:
                if st.checkbox(
                    f"{u}｜{t.trade_date:%m/%d} {t.stock_id} {name_by_id.get(str(t.stock_id).strip(), '')} "
                    f"{'買' if str(t.side).upper() == 'BUY' else '賣'} {float(t.price):g} × {int(t.quantity or 0):,}"
                    f"（id {t.id}）→ 刪除", key=f"bs_orphan_{t.id}"):
                    orphan_del.add(int(t.id))
            st.caption("不是這個證券帳戶的交易（例如別家券商）就不要勾。刪除會連同它的沖銷配對一起刪。")

    # 修正／刪除先套用到記憶體裡的交易，下面配當沖、沖銷、預估損益才會照修正後的算
    fix_by_tid = {fx["trade_id"]: fx for _gk, fx in fixes}
    for u in users:
        c = ctx[u]
        drop = orphan_del | {tid for tid, fx in fix_by_tid.items() if fx["qty_changed"]}
        c.rules = [x for x in c.rules if x[0] not in drop and x[1] not in drop]
        c.trades = [SimpleNamespace(**{**{k: getattr(t, k) for k in ("id", "user", "stock_id", "trade_date", "side",
                                                                   "price", "quantity", "fee", "tax", "is_daytrade")},
                                       **({k: fix_by_tid[int(t.id)][k] for k in ("price", "quantity", "fee", "tax")}
                                          if int(t.id) in fix_by_tid else {})})
                    for t in c.trades if int(t.id) not in orphan_del]

    to_import = [r for r in rows if r["status"] == NEW
                 or (r["status"] == REVIEW and (r["user"], r["trade_date"], r["stock_id"], r["side"]) in force)]
    assign_tmp_ids(to_import)   # seq 全檔唯一，所以暫時 id 跨人也不會撞
    new_by_user = {u: [r for r in to_import if r["user"] == u] for u in users}

    # 有新賣出 → 選沖銷方式（所有人共用一種）：當沖沖今天哪幾筆買進、當沖以外從庫存沖哪幾筆，都照這個
    strategy = "NONE"
    if any(r["side"] == "SELL" for r in to_import):
        strategy = st.radio(
            "賣出要沖哪幾筆買進（這次統一用同一種方式；之後可到「自定沖銷設定」個別改）",
            list(SELL_STRATEGIES), format_func=lambda k: SELL_STRATEGIES[k], key="bs_strategy",
            help="從原本庫存＋這次的買進裡挑；當沖的賣出若選「只沖當天的買進」，就只在當天的買進裡挑。",
        )
    dt_rules, dt_flag = [], set()
    for u in users:
        rr, ff = plan_daytrade(new_by_user[u], ctx[u].trades, ctx[u].rules, strategy)
        dt_rules += rr
        dt_flag |= ff
    if dt_rules:
        dt_any = st.radio(
            "當沖的賣出要沖哪裡的買進",
            [True, False], key="bs_dt_any", horizontal=True,
            format_func=lambda v: "以前的庫存＋當天的買進都可以（照上面方式挑）" if v else "只沖當天的買進",
        )
        st.caption(
            "ℹ️ **怎麼判斷當沖**：看券商扣的證交稅——稅是成交金額 0.15%（當沖減半）＝當沖，0.3%＝一般賣出。"
            "當沖的賣出可以選要不要也沖以前幾天買的庫存；選「只沖當天的買進」時不動原本庫存，"
            "當天買的比賣的多，沖哪幾筆照上面選的方式（先進先出＝照成交順序）。"
        )
        if dt_any:
            # 新匯入的當沖賣出改走統一沖銷（從全部庫存挑）；稅是當沖稅，賣出仍標當沖。
            # 手 key 的既有賣出配上這次的買進，維持當沖互沖（統一沖銷只處理新賣出）
            keep = [x for x in dt_rules if x[0] > 0]
            new_dt_sells = {x[0] for x in dt_rules if x[0] < 0}
            dt_flag = new_dt_sells | {i for x in keep for i in x[:2]}
            dt_rules = keep
    # §7.1 少數幾筆另外指定：自己挑的先佔掉買進，其他賣出逐筆照各自的方式
    overrides, pick_rules, pick_err = _sell_overrides(users, new_by_user, ctx, dt_rules, strategy, name_by_id,
                                                      key_sfx=f"{main_user}_{getattr(f, 'name', '')}")
    sell_rules, shortage = [], {}
    for u in users:
        rr, sh = plan_sell_matches(new_by_user[u], ctx[u].trades, ctx[u].rules + dt_rules + pick_rules,
                                   strategy, overrides)
        sell_rules += rr
        shortage.update(sh)

    # 同一對（賣, 買）合併成一條規則（當沖、自己挑、統一沖銷可能沖到同一筆買進）
    all_rules = {}
    for s_id, b_id, q in dt_rules + pick_rules + sell_rules:
        all_rules[(s_id, b_id)] = all_rules.get((s_id, b_id), 0) + q

    # 預估這次多出的已實現損益（淨額，扣手續費稅）與每筆賣出沖到哪
    by_id = {int(t.id): t for u in users for t in ctx[u].trades}
    for r in to_import:
        by_id[r["tmp_id"]] = SimpleNamespace(id=r["tmp_id"], quantity=r["quantity"], price=r["price"],
                                             fee=r["fee"], tax=r["tax"], trade_date=r["trade_date"],
                                             stock_id=r["stock_id"])
    pnl, match_text = 0.0, {}
    pnl_by_sell = {}                  # 每筆賣出的預估損益（預覽表一欄）
    by_stock = {}                     # 股號 → [沖銷股數, 損益]：總數對不起來時看是哪檔
    for (s_id, b_id), q in all_rules.items():
        b, s = by_id[b_id], by_id[s_id]
        p = net_pnl_for_match((b_id, s_id, q, b.price, s.price, (s.price - b.price) * q), by_id)
        pnl += p
        pnl_by_sell[s_id] = pnl_by_sell.get(s_id, 0.0) + p
        acc = by_stock.setdefault(str(s.stock_id).strip(), [0, 0.0])
        acc[0] += q
        acc[1] += p
        match_text.setdefault(s_id, []).append(f"{b.trade_date:%m/%d} {b.price:g}×{q:,}")

    n_dt_sell = sum(1 for r in to_import if r["side"] == "SELL" and r["tmp_id"] in dt_flag)
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
        + (f"、✋ 已手 key {len(keyed)}" if with_order else f"、✅ 系統裡已有 {len(keyed)}")
        + f"、⏭️ 已匯入過 {cnt[DUP_IMPORT]}、⚠️ 要確認 {cnt[REVIEW]}"
        + (f"（修正 {len(fixes)} 筆手 key）" if fixes else "")
        + (f"；刪除對帳單沒有的手 key {len(orphan_del)} 筆" if orphan_del else "")
        + (f"；當沖賣出 {n_dt_sell} 筆" if n_dt_sell else "")
        + (f"；預估這次多出已實現損益 **{pnl:+,.0f}**" if all_rules else "")
    )
    if shortage:
        sid_of = {r["tmp_id"]: (r["user"], r["stock_id"]) for r in to_import}
        st.error("庫存不足：" + "、".join(
            f"{sid_of[s][0]} {by_id[s].trade_date:%m/%d} {sid_of[s][1]} 少 {q:,} 股" for s, q in shortage.items())
            + "（可能還有更早的買進沒 key；仍可匯入，沖不到的部分之後再補）")

    if by_stock:
        with st.expander(f"📊 預估損益分股看（{len(by_stock)} 檔；跟自己的帳對不起來時看這裡）", expanded=len(by_stock) > 1):
            st.dataframe(pd.DataFrame([
                {"股票": f"{sid} {name_by_id.get(sid, '')}", "沖銷股數": q, "預估已實現損益": v}
                for sid, (q, v) in sorted(by_stock.items())]).style.format(
                    {"沖銷股數": "{:,.0f}", "預估已實現損益": "{:+,.0f}"}),
                use_container_width=True, hide_index=True)
            st.caption(
                "對不起來通常是賣出沖到的買進不一樣（看下面預覽表「沖銷到」）。"
                "匯入前：換上面的沖銷方式重算；匯入後：到「自定沖銷設定」頁改那筆賣出要沖哪幾筆買進。"
                "如果是之前 key 的買進價格／手續費打錯：到「股票輸入（仿Yahoo）」或「交易輸入」改那筆買進。"
            )

    import_seqs = {r["seq"] for r in to_import}

    def view(r):
        tid = r.get("tmp_id") if r["seq"] in import_seqs else None
        status = STATUS_LABEL[r["status"]]
        if not with_order and r["status"] in (KEYED_EXACT, KEYED_MERGED):
            status = "✅ 系統裡已有" + ("（合併）" if r["status"] == KEYED_MERGED else "")
        if r["status"] == DUP_IMPORT and r.get("dup_user") and r["dup_user"] != main_user:
            status += f"（{r['dup_user']}）"
        if r["status"] == REVIEW and tid is not None:
            status += "（照樣匯入）"
        elif r["status"] == REVIEW and (r["user"], r["trade_date"], r["stock_id"], r["side"]) in fix_keys:
            status += "（照對帳單修正手 key）"
        v = {"狀態": status}
        if split or multi:
            v["買賣人"] = r["user"]
        v.update({
            "日期": f"{r['trade_date']:%Y/%m/%d}", "股票": f"{r['stock_id']} {r['stock_name']}",
            "買/賣": "買" if r["side"] == "BUY" else "賣", "股數": r["quantity"], "價格": r["price"],
            "手續費": r["fee"], "稅": r["tax"], "委託書號": r["order_no"],
            "同日相同": f"×{dup_counts[content_key(r)]}" if dup_counts.get(content_key(r), 0) > 1 else "",
            "當沖": "✓" if tid is not None and tid in dt_flag else "",
            "沖銷到": "、".join(match_text.get(tid, [])) if tid is not None else "",
            # 直接給字串：整欄都是 None 時 Styler 的格式不會套用，畫面會出現「None」
            "預估損益": f"{pnl_by_sell[tid]:+,.0f}" if tid in pnl_by_sell else "",
        })
        return v

    fmt = {"股數": "{:,.0f}", "價格": "{:,.2f}", "手續費": "{:,.0f}", "稅": "{:,.0f}"}

    def show(rs):
        df = pd.DataFrame([view(r) for r in rs])
        # 只有買進時「當沖／沖銷到／預估損益」整欄空白，拿掉讓表比較好讀
        df = df.drop(columns=[c for c in ("當沖", "沖銷到", "預估損益", "同日相同", "委託書號") if (df[c] == "").all()])
        st.dataframe(df.style.format(fmt), use_container_width=True, hide_index=True)

    main = [r for r in rows if r["status"] in (NEW, REVIEW)]
    other = [r for r in rows if r["status"] not in (NEW, REVIEW)]
    if main:
        show(main)
    if other:
        with st.expander(f"已在系統裡、這次不匯的 {len(other)} 筆"):
            show(other)

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
        label = ("沒有要新增的（只修正／補書號）" if (fixes or orphan_del)
                 else "沒有要新增的（只補上書號）" if keyed and with_order
                 else "沒有要新增的（只更新手續費／稅）" if fee_updates else "沒有要匯入的")
    # 沒書號的檔：已有的交易沒有書號可補，只剩手續費／稅可更新
    keyed_work = keyed if with_order else fee_updates
    # 按鈕旁列出每人每天的交割款（整份對帳單、不分狀態），上傳時順便跟券商 App 對一下
    settle = settlement_by_user_date(rows)
    b1, b2 = st.columns([1, 2], vertical_alignment="center")
    clicked = b1.button(label, type="primary", key="bs_do_import", disabled=pick_err or not (to_import or keyed_work or fixes or orphan_del))
    b2.markdown("　".join(
        f"💰 {f'{u} ' if len({k[0] for k in settle}) > 1 else ''}{d:%m/%d} 交割款："
        + (f"**應付 {-v:,.0f}**" if v < 0 else f"**應收 {v:,.0f}**")
        for (u, d), v in sorted(settle.items(), key=lambda kv: (kv[0][1], str(kv[0][0])))))
    if not clicked:
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
            # 沒書號的檔不記 trade_refs（重匯靠內容比對認回來）
            refs_new = [(r["trade_date"], r["order_no"], id_map[r["tmp_id"]], "import")
                        for r in to_import if r["user"] == u and r["order_no"]]
            refs_new += [(r["trade_date"], r["order_no"], r["claim_trade_id"], "backfill")
                         for r in keyed if r["user"] == u and r["order_no"]]
            trs.add_refs(sess, u, refs_new, batch_id)
        for (u, _d, _sid, _side), fx in fixes:   # 照對帳單修正手 key 的那筆（同一個交易裡）
            t = sess.get(Trade, fx["trade_id"])
            if t is not None:
                t.price, t.quantity, t.fee, t.tax = fx["price"], fx["quantity"], fx["fee"], fx["tax"]
                if fx["qty_changed"]:
                    sess.query(CustomMatchRule).filter((CustomMatchRule.sell_trade_id == t.id)
                                                       | (CustomMatchRule.buy_trade_id == t.id)).delete()
        fix_refs = [(r["user"], r["trade_date"], r["order_no"], r["claim_trade_id"]) for r in rows
                    if r["status"] == REVIEW and (r["user"], r["trade_date"], r["stock_id"], r["side"]) in fix_keys]
        for u in users:
            mine = [(d, o, tid, "fix") for uu, d, o, tid in fix_refs if uu == u and o]
            if mine:
                trs.add_refs(sess, u, mine, batch_id)
        for tid in orphan_del:
            sess.query(CustomMatchRule).filter((CustomMatchRule.sell_trade_id == tid)
                                               | (CustomMatchRule.buy_trade_id == tid)).delete()
            sess.query(Trade).filter(Trade.id == tid).delete()
        if do_fee:
            for upd in fee_updates:
                t = sess.get(Trade, upd["trade_id"])
                if t is not None:
                    t.fee, t.tax = upd["fee"], upd["tax"]
        sess.commit()
        st.session_state.pop("bs_split", None)   # 下一份對帳單預設回到整份給一個人
        st.session_state["bs_done_msg"] = (
            f"已匯入 {len(to_import)} 筆" + (f"（{per_user_txt}）" if multi else f"到「{to_import[0]['user']}」" if to_import else "")
            + (f"、補上書號 {len(keyed)} 筆" if keyed and with_order else "")
            + (f"、更新手續費／稅 {len(fee_updates)} 筆" if do_fee else "")
            + (f"、照對帳單修正手 key {len(fixes)} 筆" if fixes else "")
            + (f"、刪除對帳單沒有的手 key {len(orphan_del)} 筆" if orphan_del else "")
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
