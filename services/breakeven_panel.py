# -*- coding: utf-8 -*-
"""「🧮 底價怎麼算？」計算面板：庫存損益、交易輸入共用（規格見 docs/specs/底價.md）。"""
from typing import Iterable, Tuple

import pandas as pd
import streamlit as st

from services.trade_fees import breakeven_breakdown


def render_breakeven_panel(items: Iterable[Tuple[str, str, int, float, bool]], key_hint: str = "") -> None:
    """items：(股票代號, 名稱, 股數, 持股成本(含買進手續費), 是否 ETF)。無持股的列自動略過。"""
    rows = []
    rates = None
    for sid, name, qty, cost, is_etf in items:
        d = breakeven_breakdown(float(cost or 0), int(qty or 0), is_etf=bool(is_etf))
        if not d:
            continue
        rates = rates or d
        rows.append({
            "代號": sid,
            "名稱": name,
            "股數": int(qty),
            "持股成本": float(cost),
            "均價": d["avg_cost"],
            "理論打平價": d["theoretical"],
            "升降單位": d["tick"],
            "底價": d["price"],
            "賣在底價：成交金額": d["gross"],
            "－手續費": d["fee"],
            "－證交稅": d["tax"],
            "＝實拿": d["net"],
            "賣在底價損益": d["pnl"],
            "低一檔價": d["prev_price"],
            "低一檔損益": d["prev_pnl"],
        })
    if not rates:
        return
    with st.expander("🧮 底價怎麼算？（點開看每檔的計算過程）"):
        fr, tr = rates["fee_rate"], rates["tax_rate"]
        st.markdown(f"""
**底價**＝全部股數賣在這個價，扣掉賣出手續費和證交稅後，拿回的錢 ≥ 持股成本（剛好不虧的最低價）。

1. **持股成本**＝買進成本（已含買進手續費）；**均價**＝持股成本 ÷ 股數
2. **理論打平價**＝持股成本 ÷ 股數 ÷ (1 − 手續費率 {fr * 100:.5f}% − 證交稅率 {tr * 100:.1f}%)
3. 依台股**升降單位**向上進位成可以掛單的價格（未滿 10 元 0.01、10–50 元 0.05、50–100 元 0.1、100–500 元 0.5、500–1000 元 1、1000 元以上 5）
4. 用實際規則驗算：手續費、證交稅都**無條件捨去到整數**（手續費最低 1 元），不夠就再往上一檔
5. 最後一欄「低一檔損益」是負的，代表再低一檔賣就會虧，所以底價不能再低

💡 手續費＋證交稅合計約 {(fr + tr) * 100:.2f}%，所以底價一定比均價高一點，不是均價直接進位。ETF 證交稅為 0.1%。
""")
        df = pd.DataFrame(rows)
        int_cols = ["股數", "持股成本", "賣在底價：成交金額", "－手續費", "－證交稅", "＝實拿", "賣在底價損益", "低一檔損益"]
        px_cols = ["均價", "理論打平價", "升降單位", "底價", "低一檔價"]
        fmt = {c: "{:,.0f}" for c in int_cols}
        fmt.update({c: "{:,.2f}" for c in px_cols})
        sty = df.style.format(fmt, na_rep="—")
        sty = sty.apply(lambda s: ["color: #1565c0; font-weight: 600;"] * len(s), subset=["底價"])
        sty = sty.apply(
            lambda s: ["color: #c62828;" if (v is not None and v == v and v >= 0) else "color: #2e7d32;" for v in s],
            subset=["賣在底價損益", "低一檔損益"],
        )
        # 全部展開（每列約 35px + 表頭），不在小框內捲動
        st.dataframe(sty, use_container_width=True, hide_index=True, height=35 * (len(df) + 1) + 3)
