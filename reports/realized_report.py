# -*- coding: utf-8 -*-
"""已實現損益彙整：把所有股票的「買→賣」沖銷配對攤平成一份總帳（ledger），
供「已實現損益」頁做各種面向的統計（依股票／買賣人／產業／月份／持有天數）。

每一列 = 一筆已實現沖銷（一段買進對應一段賣出），淨損益已扣買進手續費、
賣出手續費、證交稅（沿用 stock_detail_report.build_stock_sold_df 的口徑）。
"""
from datetime import date
from typing import Optional, List, Tuple, Dict

import pandas as pd

from reports.stock_detail_report import build_stock_sold_df


LEDGER_COLUMNS = [
    "買賣人", "代號", "名稱", "產業", "買進日", "賣出日", "持有天數",
    "股數", "買價", "賣價", "買進成本", "賣出金額",
    "買手續費", "賣手續費", "證交稅", "總費用", "淨損益", "報酬率%", "當沖",
]


def _to_date(v):
    try:
        return pd.to_datetime(str(v)).date()
    except Exception:
        return None


def build_realized_ledger(
    trades,
    masters,
    policy: str,
    custom_rules: Optional[List[Tuple[int, int, int]]] = None,
) -> pd.DataFrame:
    """回傳所有股票已實現沖銷的總帳 DataFrame（欄位見 LEDGER_COLUMNS）。無資料時回空表。"""
    masters = masters or {}
    stock_ids = sorted({str(getattr(t, "stock_id", "")).strip() for t in trades if getattr(t, "stock_id", None)})
    stock_ids = [s for s in stock_ids if s]

    rows: List[dict] = []
    for sid in stock_ids:
        sold_df, _rev = build_stock_sold_df(sid, trades, masters, policy, custom_rules=custom_rules)
        if sold_df is None or sold_df.empty:
            continue
        m = masters.get(sid)
        name = getattr(m, "name", None) or ""
        industry = (getattr(m, "industry_name", None) or "") or "其他"
        for _, r in sold_df.iterrows():
            bd = _to_date(r.get("買賣日"))
            sd = _to_date(r.get("出售日"))
            hold_days = (sd - bd).days if (bd and sd) else None
            qty = int(r.get("股數") or 0)
            buy_price = float(r.get("股價") or 0)
            sell_price = float(r.get("賣價") or 0)
            buy_cost = float(r.get("買股票支出") or 0)          # 含買進手續費
            sell_amount = float(r.get("賣出金額") or 0)
            buy_fee = float(r.get("手續費") or 0)
            sell_fee = float(r.get("賣出手續費") or 0)
            tax = float(r.get("證交稅") or 0)
            net = float(r.get("單筆損益") or 0)
            ret_pct = (net / buy_cost * 100) if buy_cost else 0.0
            rows.append({
                "買賣人": r.get("買賣人") or "",
                "代號": sid,
                "名稱": name,
                "產業": industry,
                "買進日": bd,
                "賣出日": sd,
                "持有天數": hold_days,
                "股數": qty,
                "買價": round(buy_price, 2),
                "賣價": round(sell_price, 2),
                "買進成本": round(buy_cost, 0),
                "賣出金額": round(sell_amount, 0),
                "買手續費": round(buy_fee, 0),
                "賣手續費": round(sell_fee, 0),
                "證交稅": round(tax, 0),
                "總費用": round(buy_fee + sell_fee + tax, 0),
                "淨損益": round(net, 0),
                "報酬率%": round(ret_pct, 2),
                "當沖": bool(r.get("當沖")),
            })

    if not rows:
        return pd.DataFrame(columns=LEDGER_COLUMNS)
    df = pd.DataFrame(rows, columns=LEDGER_COLUMNS)
    df = df.sort_values(by=["賣出日", "代號"], kind="mergesort").reset_index(drop=True)
    return df


def summarize_ledger(df: pd.DataFrame) -> Dict[str, float]:
    """由總帳算出 KPI。空表回傳全 0 的字典。"""
    keys = [
        "總淨損益", "總賣出金額", "總買進成本", "已實現報酬率%",
        "筆數", "獲利筆數", "虧損筆數", "打平筆數", "勝率%",
        "平均每筆", "平均獲利", "平均虧損", "盈虧比", "期望值",
        "最大單筆獲利", "最大單筆虧損",
        "總手續費", "總證交稅", "總費用",
        "平均持有天數", "當沖筆數", "當沖淨損益", "波段筆數", "波段淨損益",
    ]
    if df is None or df.empty:
        return {k: 0.0 for k in keys}

    net = df["淨損益"].astype(float)
    wins = net[net > 0]
    losses = net[net < 0]
    flats = net[net == 0]
    n = len(net)
    total_cost = float(df["買進成本"].sum())
    avg_win = float(wins.mean()) if len(wins) else 0.0
    avg_loss = float(losses.mean()) if len(losses) else 0.0  # 負值
    win_rate = (len(wins) / n * 100) if n else 0.0
    # 期望值 = 勝率×平均獲利 + 敗率×平均虧損
    lose_rate = (len(losses) / n) if n else 0.0
    expectancy = (win_rate / 100.0) * avg_win + lose_rate * avg_loss
    payoff = (avg_win / abs(avg_loss)) if avg_loss != 0 else 0.0
    hold_days = pd.to_numeric(df["持有天數"], errors="coerce").dropna()
    day = df[df["當沖"]]
    swing = df[~df["當沖"]]

    return {
        "總淨損益": float(net.sum()),
        "總賣出金額": float(df["賣出金額"].sum()),
        "總買進成本": total_cost,
        "已實現報酬率%": (float(net.sum()) / total_cost * 100) if total_cost else 0.0,
        "筆數": n,
        "獲利筆數": int(len(wins)),
        "虧損筆數": int(len(losses)),
        "打平筆數": int(len(flats)),
        "勝率%": win_rate,
        "平均每筆": float(net.mean()) if n else 0.0,
        "平均獲利": avg_win,
        "平均虧損": avg_loss,
        "盈虧比": payoff,
        "期望值": expectancy,
        "最大單筆獲利": float(net.max()) if n else 0.0,
        "最大單筆虧損": float(net.min()) if n else 0.0,
        "總手續費": float(df["買手續費"].sum() + df["賣手續費"].sum()),
        "總證交稅": float(df["證交稅"].sum()),
        "總費用": float(df["總費用"].sum()),
        "平均持有天數": float(hold_days.mean()) if len(hold_days) else 0.0,
        "當沖筆數": int(len(day)),
        "當沖淨損益": float(day["淨損益"].sum()) if len(day) else 0.0,
        "波段筆數": int(len(swing)),
        "波段淨損益": float(swing["淨損益"].sum()) if len(swing) else 0.0,
    }


def aggregate_by(df: pd.DataFrame, group_col: str) -> pd.DataFrame:
    """依某欄位彙總：淨損益、筆數、獲利筆數、勝率%、賣出金額、買進成本、報酬率%。"""
    if df is None or df.empty:
        return pd.DataFrame()
    g = df.copy()
    g["_win"] = (g["淨損益"].astype(float) > 0).astype(int)
    agg = g.groupby(group_col).agg(
        淨損益=("淨損益", "sum"),
        筆數=("淨損益", "count"),
        獲利筆數=("_win", "sum"),
        賣出金額=("賣出金額", "sum"),
        買進成本=("買進成本", "sum"),
        總費用=("總費用", "sum"),
    ).reset_index()
    agg["勝率%"] = (agg["獲利筆數"] / agg["筆數"] * 100).round(1)
    agg["報酬率%"] = (agg["淨損益"] / agg["買進成本"].replace(0, pd.NA) * 100).round(2)
    agg = agg.sort_values(by="淨損益", ascending=False, kind="mergesort").reset_index(drop=True)
    return agg


def monthly_series(df: pd.DataFrame) -> pd.DataFrame:
    """依『賣出月份』彙總每月已實現淨損益與累積損益。"""
    if df is None or df.empty:
        return pd.DataFrame(columns=["月份", "當月已實現", "累積已實現", "筆數"])
    g = df.dropna(subset=["賣出日"]).copy()
    if g.empty:
        return pd.DataFrame(columns=["月份", "當月已實現", "累積已實現", "筆數"])
    g["月份"] = pd.to_datetime(g["賣出日"]).dt.strftime("%Y-%m")
    m = g.groupby("月份").agg(當月已實現=("淨損益", "sum"), 筆數=("淨損益", "count")).reset_index()
    m = m.sort_values(by="月份", kind="mergesort").reset_index(drop=True)
    m["累積已實現"] = m["當月已實現"].cumsum()
    return m
