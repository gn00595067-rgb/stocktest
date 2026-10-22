# -*- coding: utf-8 -*-
"""日成交明細表：每筆交易的詳細紀錄（參考券商日成交表格式）"""
import pandas as pd
from datetime import date
from collections import defaultdict
from typing import Optional, List, Tuple

from db.models import Trade
from services.pnl_engine import Lot, compute_matches, net_pnl_for_match


def build_daily_detail_df(trades, masters, policy: str = "CUSTOM", filter_date=None, custom_rules: Optional[List[Tuple[int, int, int]]] = None):
    """
    建構日成交明細表，一筆交易一行。
    policy 僅支援自定沖銷；custom_rules 必傳。
    """
    if not trades:
        return pd.DataFrame()
    masters = masters or {}

    # 計算每筆賣出的已實現損益（依 policy）
    buys_by_stock = defaultdict(list)
    sells_by_stock = defaultdict(list)
    for t in trades:
        lot = Lot(t.id, t.quantity, t.price, str(t.trade_date))
        if (t.side or "").strip().upper() in ("BUY", "配股"):
            buys_by_stock[t.stock_id].append(lot)
        else:
            sells_by_stock[t.stock_id].append(lot)
    trade_by_id = {t.id: t for t in trades}
    pnl_by_sell_id = defaultdict(float)
    for sid, sells in sells_by_stock.items():
        buys = buys_by_stock.get(sid, [])
        matches = compute_matches(buys, sells, policy, custom_rules=custom_rules)
        for m in matches:
            _buy_id, sell_id, _qty, _bp, _sp, _ = m
            pnl_by_sell_id[sell_id] += net_pnl_for_match(m, trade_by_id)

    rows = []
    for t in sorted(trades, key=lambda x: (x.trade_date, x.stock_id, x.id)):
        raw_side = (t.side or "").strip().upper()
        side_cht = "配股" if raw_side == "配股" else ("買" if raw_side == "BUY" else "賣")
        price = float(t.price or 0)
        qty = int(t.quantity or 0)
        fee = float(t.fee or 0)
        tax = float(t.tax or 0)
        gross = price * qty  # 成交金額

        if side_cht in ("買", "配股"):
            net = -(gross + fee)  # 淨付出
            pnl = None  # 買入無損益
        else:
            net = gross - fee - tax  # 淨收入
            pnl = pnl_by_sell_id.get(t.id, 0)

        m = masters.get(str(t.stock_id))
        name = getattr(m, "name", None) or ""
        company = f"{t.stock_id}{name}" if name else str(t.stock_id)

        rows.append({
            "誰名下": t.user,
            "買賣日": str(t.trade_date),
            "公司": company,
            "股數": qty,
            "股價": round(price, 2),
            "買/賣": side_cht,
            "當沖": bool(getattr(t, "is_daytrade", False)),
            "成交金額": round(gross, 0),
            "手續費": round(fee, 0),
            "證交稅": round(tax, 0),
            "淨收付": round(net, 0),
            "損益": round(pnl, 0) if pnl is not None else None,
        })

    df = pd.DataFrame(rows)
    if filter_date is not None:
        df = df[df["買賣日"] == str(filter_date)].copy()
    return df


STOCK_SUMMARY_COLUMNS = ["股票", "買進股數", "買進支出", "賣出股數", "賣出收入", "手續費", "證交稅", "損益"]


def build_daily_stock_summary(detail: pd.DataFrame, blank_buy_total: bool = False) -> pd.DataFrame:
    """每股損益簡表（像 Peggy Excel 的每股「合計」列）：每檔一列，最後一列「合計」。

    detail 為「當日交易明細」頁的每筆明細（欄位：股票、買/賣、股數、手續費、證交稅、
    交割應收、交割應付，以及原始未湊整的 _pnl_raw）。
    - 買進支出＝成交金額＋手續費（＝交割應付）；賣出收入＝成交金額－手續費－證交稅（＝交割應收）
    - 損益＝該檔當日賣出的已實現損益（以原始值加總、最後才四捨五入，與已實現損益頁一致）
    - blank_buy_total：合計列的「買進支出」留空（區間用：同一筆錢會買了賣、賣了再買，
      整段期間的買進加總遠大於實際本金，看了會誤會；Peggy 2026-10-22 回饋）
    """
    if detail is None or detail.empty:
        return pd.DataFrame(columns=STOCK_SUMMARY_COLUMNS)
    d = detail.copy()
    is_sell = d["買/賣"].astype(str).str.upper() == "SELL"
    d["_buy_qty"] = d["股數"].where(~is_sell, 0)
    d["_sell_qty"] = d["股數"].where(is_sell, 0)
    pnl_col = "_pnl_raw" if "_pnl_raw" in d.columns else "單筆損益"
    d["_pnl"] = pd.to_numeric(d[pnl_col], errors="coerce").fillna(0.0)
    g = d.groupby("股票", sort=True).agg(
        買進股數=("_buy_qty", "sum"),
        買進支出=("交割應付", "sum"),
        賣出股數=("_sell_qty", "sum"),
        賣出收入=("交割應收", "sum"),
        手續費=("手續費", "sum"),
        證交稅=("證交稅", "sum"),
        損益=("_pnl", "sum"),
    ).reset_index()
    total = {"股票": "合計"}
    for c in STOCK_SUMMARY_COLUMNS[1:]:
        total[c] = float(g[c].sum())
    # 不同股票的股數相加沒有意義（Peggy 2026-10-15 回饋 7-2），合計列留空
    total["買進股數"] = float("nan")
    total["賣出股數"] = float("nan")
    if blank_buy_total:
        total["買進支出"] = float("nan")
    out = pd.concat([g, pd.DataFrame([total])], ignore_index=True)
    out["損益"] = out["損益"].round(0)
    return out[STOCK_SUMMARY_COLUMNS]
