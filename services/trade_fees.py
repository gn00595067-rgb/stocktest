# -*- coding: utf-8 -*-
"""台股手續費與證交稅估算（寫入交易時若未填 fee/tax 則套用）

計算基準：以國泰證券為準——手續費率 0.1425% × 2.5 折，金額一律「無條件捨去」
到整數元（小數不計），非四捨五入。證交稅同樣無條件捨去。
"""
import math
from typing import Optional, Tuple

BROKER_FEE_MIN = 1.0  # 電子下單最低手續費 1 元
BASE_FEE_RATE = 0.001425  # 公定手續費率 0.1425%
# 手續費折數依買賣人（見 docs/specs/買賣人手續費折數.md）：Peggy姐 2.5 折、一般人 2.8 折
DEFAULT_DISCOUNT = 0.28
# 沒有指定買賣人時的預設：0.1425% 打 2.5 折（0.001425 × 25%）= 0.00035625
DEFAULT_FEE_RATE = 0.00035625
DEFAULT_TAX_RATE = 0.003
DEFAULT_ETF_TAX_RATE = 0.001


def get_fee_tax_rates() -> Tuple[float, float]:
    """從 Streamlit session 讀取費率；無 session 時用預設。"""
    try:
        import streamlit as st
        fee_rate = float(st.session_state.get("fee_rate", DEFAULT_FEE_RATE))
        tax_rate = float(st.session_state.get("tax_rate", DEFAULT_TAX_RATE))
        return fee_rate, tax_rate
    except Exception:
        return DEFAULT_FEE_RATE, DEFAULT_TAX_RATE


def fee_rate_for_discount(discount: Optional[float]) -> float:
    """折數 → 手續費率（例 0.28 → 0.1425% × 0.28）；空白用一般人 2.8 折。"""
    d = DEFAULT_DISCOUNT if discount is None or discount != discount or discount <= 0 else float(discount)
    return BASE_FEE_RATE * d


def estimate_broker_fee(price: float, quantity: int, fee_rate: Optional[float] = None) -> float:
    """單筆成交手續費（買賣皆收，最低 1 元，無條件捨去至整數，國泰基準）。"""
    if quantity <= 0 or price <= 0:
        return 0.0
    rate = fee_rate if fee_rate is not None else get_fee_tax_rates()[0]
    amount = price * quantity
    # 先四捨五入到小數 6 位再捨去：避免 40.0 被浮點算成 39.9999 而少 1 元
    return float(max(BROKER_FEE_MIN, math.floor(round(amount * rate, 6))))


def estimate_sell_tax(
    price: float,
    quantity: int,
    is_etf: bool = False,
    tax_rate: Optional[float] = None,
    is_daytrade: bool = False,
    daytrade_qty: Optional[int] = None,
) -> float:
    """賣出證交稅（無條件捨去至整數，國泰基準）。

    現股當沖：一般個股證交稅減半（0.3% → 0.15%）。ETF 本即 0.1%，不再另外減半。
    daytrade_qty：這筆賣出裡「當天有買進可對沖」的股數（見 daytrade_capacity）；
    只有這些股數減半，其餘是賣庫存、照一般稅率。沒給＝整筆都算當沖（舊行為）。
    """
    if quantity <= 0 or price <= 0:
        return 0.0
    if is_etf:
        return float(math.floor(round(price * quantity * DEFAULT_ETF_TAX_RATE, 6)))
    rate = tax_rate if tax_rate is not None else get_fee_tax_rates()[1]
    if not is_daytrade:
        return float(math.floor(round(price * quantity * rate, 6)))
    dq = quantity if daytrade_qty is None else max(0, min(int(daytrade_qty), quantity))
    amt = price * dq * rate / 2.0 + price * (quantity - dq) * rate   # 現股當沖證交稅減半
    return float(math.floor(round(amt, 6)))


def fees_for_trade(
    side: str,
    price: float,
    quantity: int,
    is_etf: bool = False,
    is_daytrade: bool = False,
    fee_rate: Optional[float] = None,
    daytrade_qty: Optional[int] = None,
) -> Tuple[float, float]:
    """
    回傳 (fee, tax)。
    買進：僅手續費；賣出：手續費 + 證交稅（當沖時一般個股稅減半）。
    fee_rate：該買賣人的手續費率（trader_service.trader_fee_rate）；空白用設定頁的預設費率。
    """
    fee = estimate_broker_fee(price, quantity, fee_rate=fee_rate)
    side_u = (side or "").strip().upper()
    if side_u == "SELL":
        tax = estimate_sell_tax(price, quantity, is_etf=is_etf, is_daytrade=is_daytrade, daytrade_qty=daytrade_qty)
        return fee, tax
    return fee, 0.0


def tick_size(price: float, is_etf: bool = False) -> float:
    """台股升降單位（每檔跳動價位）。ETF：未滿 50 元 0.01、50 元以上 0.05。"""
    if is_etf:
        return 0.01 if price < 50 else 0.05
    if price < 10:
        return 0.01
    if price < 50:
        return 0.05
    if price < 100:
        return 0.1
    if price < 500:
        return 0.5
    if price < 1000:
        return 1.0
    return 5.0


def breakeven_sell_price(
    total_cost: float,
    quantity: int,
    is_etf: bool = False,
    fee_rate: Optional[float] = None,
    tax_rate: Optional[float] = None,
) -> Optional[float]:
    """底價：全部股數以此價賣出，扣除賣出手續費與證交稅後，拿回的錢 ≥ 持股總成本（含買進手續費）。

    回傳符合升降單位的最低價；無持股或成本 ≤ 0 時回 None。
    """
    if quantity <= 0 or total_cost is None or total_cost <= 0:
        return None
    if fee_rate is None or tax_rate is None:
        _fr, _tr = get_fee_tax_rates()
        fee_rate = _fr if fee_rate is None else fee_rate
        tax_rate = _tr if tax_rate is None else tax_rate

    def _net(p: float) -> float:
        fee = estimate_broker_fee(p, quantity, fee_rate=fee_rate)
        tax = estimate_sell_tax(p, quantity, is_etf=is_etf, tax_rate=tax_rate)
        return p * quantity - fee - tax

    def _ceil_tick(p: float) -> float:
        t = tick_size(p, is_etf)
        return round(math.ceil(round(p / t, 6)) * t, 2)

    eff_tax = DEFAULT_ETF_TAX_RATE if is_etf else tax_rate
    p = _ceil_tick(total_cost / (quantity * (1 - fee_rate - eff_tax)))
    # 捨去造成的誤差：不夠就往上跳一檔，前一檔仍夠就往下退
    while _net(p) < total_cost:
        p = round(p + tick_size(p, is_etf), 2)
    while p > 0:
        prev = round(p - tick_size(p - 1e-9, is_etf), 2)
        if prev <= 0 or _net(prev) < total_cost:
            break
        p = prev
    return p


def breakeven_breakdown(
    total_cost: float,
    quantity: int,
    is_etf: bool = False,
    fee_rate: Optional[float] = None,
    tax_rate: Optional[float] = None,
) -> Optional[dict]:
    """底價的計算明細（給「底價怎麼算」面板用），與 breakeven_sell_price 同一套規則。

    回傳 dict：理論打平價、升降單位、底價、賣在底價的成交金額／手續費／證交稅／實拿／損益、
    低一檔賣出的損益；無持股或成本 ≤ 0 時回 None。
    """
    if fee_rate is None or tax_rate is None:
        _fr, _tr = get_fee_tax_rates()
        fee_rate = _fr if fee_rate is None else fee_rate
        tax_rate = _tr if tax_rate is None else tax_rate
    price = breakeven_sell_price(total_cost, quantity, is_etf, fee_rate, tax_rate)
    if price is None:
        return None
    eff_tax = DEFAULT_ETF_TAX_RATE if is_etf else tax_rate

    def _sell(p: float) -> dict:
        gross = p * quantity
        fee = estimate_broker_fee(p, quantity, fee_rate=fee_rate)
        tax = estimate_sell_tax(p, quantity, is_etf=is_etf, tax_rate=tax_rate)
        net = gross - fee - tax
        return {"gross": gross, "fee": fee, "tax": tax, "net": net, "pnl": net - total_cost}

    at = _sell(price)
    tick = tick_size(price, is_etf)
    prev_price = round(price - tick_size(price - 1e-9, is_etf), 2)
    prev = _sell(prev_price) if prev_price > 0 else None
    return {
        "avg_cost": total_cost / quantity,
        "fee_rate": fee_rate,
        "tax_rate": eff_tax,
        "theoretical": total_cost / (quantity * (1 - fee_rate - eff_tax)),
        "tick": tick,
        "price": price,
        "gross": at["gross"],
        "fee": at["fee"],
        "tax": at["tax"],
        "net": at["net"],
        "pnl": at["pnl"],
        "prev_price": prev_price if prev else None,
        "prev_pnl": prev["pnl"] if prev else None,
    }



def daytrade_sell_taxes(trades, is_etf=None, tax_rate: float = DEFAULT_TAX_RATE) -> dict:
    """勾了「當沖」的賣出，依「當天同一人同一檔的買進股數」算應繳證交稅。

    現股當沖減半只適用於當天買進可以對沖的股數；超過的部分是賣原本的庫存，照一般稅率。
    例：當天買 3,000、賣 5,000（每筆 1,000，都勾當沖）→ 依 id 順序前 3 筆減半、後 2 筆全額。
    ETF 一律 0.1%，不減半。回傳 {賣出 id: (應繳證交稅, 可當沖股數)}。
    """
    from collections import defaultdict
    is_etf = is_etf or (lambda sid: False)
    buy_qty = defaultdict(int)
    for t in trades:
        if (t.side or "").strip().upper() == "BUY":
            buy_qty[(t.user, t.stock_id, t.trade_date)] += int(t.quantity or 0)
    out = {}
    for t in sorted(trades, key=lambda x: x.id):
        if (t.side or "").strip().upper() != "SELL" or not getattr(t, "is_daytrade", False):
            continue
        key = (t.user, t.stock_id, t.trade_date)
        q, price = int(t.quantity or 0), float(t.price or 0)
        dt_qty = min(q, buy_qty[key])
        buy_qty[key] -= dt_qty
        out[t.id] = (estimate_sell_tax(price, q, is_etf=is_etf(t.stock_id), tax_rate=tax_rate,
                                       is_daytrade=True, daytrade_qty=dt_qty), dt_qty)
    return out


def daytrade_capacity(trades, user, stock_id, trade_date, exclude_id=None) -> int:
    """這個人、這檔、這天還能算「當沖」的股數＝當天買進股數 − 已被當天其他當沖賣出用掉的股數。

    exclude_id：修改既有賣出時排除它自己。輸入新賣出時用 min(賣出股數, 這個值) 當 daytrade_qty。
    """
    u, sid = (user or "").strip(), str(stock_id).strip()
    same = [t for t in trades
            if (t.user or "").strip() == u and str(t.stock_id).strip() == sid
            and t.trade_date == trade_date and t.id != exclude_id]
    bought = sum(int(t.quantity or 0) for t in same if (t.side or "").strip().upper() == "BUY")
    used = sum(q for _, q in daytrade_sell_taxes(same).values())
    return max(0, bought - used)
