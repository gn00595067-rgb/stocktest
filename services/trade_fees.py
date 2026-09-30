# -*- coding: utf-8 -*-
"""台股手續費與證交稅估算（寫入交易時若未填 fee/tax 則套用）

計算基準：以國泰證券為準——手續費率 0.1425% × 2.5 折，金額一律「無條件捨去」
到整數元（小數不計），非四捨五入。證交稅同樣無條件捨去。
"""
import math
from typing import Optional, Tuple

BROKER_FEE_MIN = 1.0  # 電子下單最低手續費 1 元
# 手續費率預設：公定 0.1425% 打 2.5 折（0.001425 × 25%）= 0.00035625
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


def estimate_broker_fee(price: float, quantity: int, fee_rate: Optional[float] = None) -> float:
    """單筆成交手續費（買賣皆收，最低 1 元，無條件捨去至整數，國泰基準）。"""
    if quantity <= 0 or price <= 0:
        return 0.0
    rate = fee_rate if fee_rate is not None else get_fee_tax_rates()[0]
    amount = price * quantity
    return float(max(BROKER_FEE_MIN, math.floor(amount * rate)))


def estimate_sell_tax(
    price: float,
    quantity: int,
    is_etf: bool = False,
    tax_rate: Optional[float] = None,
    is_daytrade: bool = False,
) -> float:
    """賣出證交稅（無條件捨去至整數，國泰基準）。

    現股當沖：一般個股證交稅減半（0.3% → 0.15%）。ETF 本即 0.1%，不再另外減半。
    """
    if quantity <= 0 or price <= 0:
        return 0.0
    if is_etf:
        rate = DEFAULT_ETF_TAX_RATE
    else:
        rate = tax_rate if tax_rate is not None else get_fee_tax_rates()[1]
        if is_daytrade:
            rate = rate / 2.0  # 現股當沖證交稅減半
    return float(math.floor(price * quantity * rate))


def fees_for_trade(
    side: str,
    price: float,
    quantity: int,
    is_etf: bool = False,
    is_daytrade: bool = False,
) -> Tuple[float, float]:
    """
    回傳 (fee, tax)。
    買進：僅手續費；賣出：手續費 + 證交稅（當沖時一般個股稅減半）。
    """
    fee = estimate_broker_fee(price, quantity)
    side_u = (side or "").strip().upper()
    if side_u == "SELL":
        tax = estimate_sell_tax(price, quantity, is_etf=is_etf, is_daytrade=is_daytrade)
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
