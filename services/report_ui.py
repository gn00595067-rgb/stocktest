# -*- coding: utf-8 -*-
"""報表頁共用 UI：篩選條件列、KPI 卡、頁尾說明。

Peggy 2026-10-14 回饋：每個報表的篩選條件要放同樣位置、同樣格式；
重點數字放第一列並放大；說明文字一律放頁尾。規格見 docs/specs/Peggy回饋_2026-10-14.md。
"""
from datetime import date, timedelta
from typing import Callable, List, Optional, Tuple

# 快速區間鈕（順序即畫面順序）
QUICK_RANGES = ["當日", "近3天", "當週", "當月", "當年", "近1年", "全部"]
# 「全部」的起日：與各頁既有口徑一致（兩頁「全部」才對得起來）
ALL_START = date(2000, 1, 1)


def quick_range(label: str, today: date) -> Tuple[date, date]:
    """快速區間 → (開始日, 結束日)。當週＝本週一起；當月＝本月 1 日起；當年＝今年 1/1 起。"""
    if label == "當日":
        start = today
    elif label == "近3天":
        start = today - timedelta(days=3)
    elif label == "當週":
        start = today - timedelta(days=today.weekday())
    elif label == "當月":
        start = today.replace(day=1)
    elif label == "當年":
        start = date(today.year, 1, 1)
    elif label == "近1年":
        start = today - timedelta(days=365)
    elif label == "全部":
        start = ALL_START
    else:
        raise ValueError(f"未知的快速區間：{label}")
    return start, today


def active_quick_range(start: date, end: date, today: date) -> Optional[str]:
    """目前日期對應哪一顆快速鈕（用來高亮）；手動改過日期對不上任何一顆時回 None。
    多顆結果相同時（例如週一的「當日」＝「當週」）取排前面的那顆。"""
    for lab in QUICK_RANGES:
        if quick_range(lab, today) == (start, end):
            return lab
    return None


def render_report_filter(
    prefix: str,
    users: List[str],
    default_range: str = "當年",
    default_users: Optional[List[str]] = None,
    extra: Optional[Callable[[], object]] = None,
    show_title: bool = True,
):
    """畫出統一格式的篩選條件：快速區間鈕一列 → 開始／結束日期／（extra）／買賣人（最右）。

    回傳 (start, end, picked_users, extra_value)；picked_users 為 None 代表「全部」。
    日期存在 session_state[f"{prefix}_start"/f"{prefix}_end"]，買賣人在 f"{prefix}_filter_user_multi"。
    """
    import streamlit as st

    today = date.today()
    sk, ek = f"{prefix}_start", f"{prefix}_end"
    if sk not in st.session_state or ek not in st.session_state:
        st.session_state[sk], st.session_state[ek] = quick_range(default_range, today)

    def _set(lab):
        # on_click 在 widget 建立前執行，改 date_input 的 key 才會可靠生效
        st.session_state[sk], st.session_state[ek] = quick_range(lab, date.today())

    if show_title:
        st.markdown("#### 篩選條件")
    active = active_quick_range(st.session_state.get(sk), st.session_state.get(ek), today)
    cols = st.columns(len(QUICK_RANGES))
    for i, lab in enumerate(QUICK_RANGES):
        cols[i].button(
            lab, key=f"{prefix}_qr_{i}", on_click=_set, args=(lab,),
            use_container_width=True, type=("primary" if active == lab else "secondary"),
        )

    widths = [1, 1, 1.2, 1.4] if extra else [1, 1, 1.4]
    row = st.columns(widths)
    with row[0]:
        start = st.date_input("開始日期", key=sk)
    with row[1]:
        end = st.date_input("結束日期", key=ek)
    extra_value = None
    if extra:
        with row[2]:
            extra_value = extra()
    with row[-1]:
        picked = trader_multiselect(users, f"{prefix}_filter_user_multi", default_users)
    if start > end:
        start, end = end, start
    return start, end, picked, extra_value


def trader_multiselect(users: List[str], key: str, default_users: Optional[List[str]] = None):
    """買賣人多選（含「全部」）。回傳 None＝全部；否則所選名單。
    交易在進頁前已依權限篩過，所以一般帳號選「全部」也只會看到自己被授權的人。"""
    import streamlit as st
    from services.prefs import resolve_default_trader

    if default_users is None:
        d = resolve_default_trader(users)
        default_users = [d] if d else ["全部"]
    picked = st.multiselect("買賣人", options=["全部"] + list(users), default=default_users, key=key)
    return None if (not picked or "全部" in picked) else picked


# ─── KPI 卡 ───────────────────────────────────────────────────────────────

KPI_CSS = """
<style>
.portfolio-kpi-card {
    background: linear-gradient(145deg, #fff 0%, #f8f9fa 100%);
    border: 1px solid #e9ecef; border-radius: 12px;
    padding: 1rem 1.25rem; margin-bottom: 0.5rem;
    box-shadow: 0 1px 3px rgba(0,0,0,0.06);
}
.portfolio-kpi-label { font-size: 0.9rem; color: #6c757d; margin-bottom: 0.25rem; }
.portfolio-kpi-value { font-size: 1.35rem; font-weight: 700; font-variant-numeric: tabular-nums; }
.portfolio-kpi-value--positive { color: #c62828; }
.portfolio-kpi-value--negative { color: #2e7d32; }
.portfolio-kpi-meta { font-size: 0.85rem; color: #6b7280; margin-top: 0.25rem; }
.portfolio-kpi-name { font-size: 0.95rem; color: #475569; margin-bottom: 0.2rem; }
/* 重點卡：大字＋淡藍底，一眼看到 */
.portfolio-kpi-card.kpi-hero { background: #eef4ff; border: 1px solid #bfd3fb; min-height: 8.8rem; padding: 1rem 1rem; }
.kpi-hero .portfolio-kpi-label { font-size: 1rem; color: #1e3a8a; font-weight: 600; }
.portfolio-kpi-card { container-type: inline-size; }
.portfolio-kpi-value { white-space: nowrap; }
/* 依卡片自身寬度縮放（側欄開關、視窗寬窄都不折行） */
.kpi-hero .portfolio-kpi-value { font-size: 2rem; line-height: 1.2; }
.kpi-hero .portfolio-kpi-name { font-size: 1.05rem; font-weight: 600; color: #1f2937; }
</style>
"""


def inject_kpi_css():
    import streamlit as st
    st.markdown(KPI_CSS, unsafe_allow_html=True)


def pnl_cls(val) -> str:
    """台股慣例：正紅、負綠（0 算紅，與既有卡片一致）。"""
    try:
        v = float(val)
    except (TypeError, ValueError):
        return ""
    if v != v:  # NaN
        return ""
    return "portfolio-kpi-value--positive" if v >= 0 else "portfolio-kpi-value--negative"


def _hero_font_css(value: str) -> str:
    """重點卡數字字級：依字數讓數字剛好塞滿卡片寬（cqi＝卡片寬的 1%），最大 2.1rem、最小 1.4rem（不小於一般卡）。
    完整千分位（259,923,000）在窄卡會縮小；「+1.92億」這種短數字可以更大。"""
    import re
    text = re.sub(r"<[^>]+>", "", str(value))
    em = sum(1.0 if ord(ch) > 0x2E80 else 0.62 for ch in text) or 1.0
    return f"font-size:clamp(1.4rem,{86 / em:.1f}cqi,2.1rem)"


def kpi_card(label: str, value: str, cls: str = "", meta: str = "", name: str = "",
             hero: bool = False, help_text: str = "") -> str:
    """回傳一張 KPI 卡的 HTML（交給 st.markdown(..., unsafe_allow_html=True)）。"""
    tip = f' title="{help_text}"' if help_text else ""
    style = f' style="{_hero_font_css(value)}"' if hero else ""
    lab = f"{label} ⓘ" if help_text else label
    return (
        f'<div class="portfolio-kpi-card{" kpi-hero" if hero else ""}"{tip}>'
        f'<div class="portfolio-kpi-label">{lab}</div>'
        + (f'<div class="portfolio-kpi-name">{name}</div>' if name else "")
        + f'<div class="portfolio-kpi-value {cls}"{style}>{value}</div>'
        + (f'<div class="portfolio-kpi-meta">{meta}</div>' if meta else "")
        + "</div>"
    )


def render_page_notes(*paragraphs: str, title: str = "📖 說明（計算口徑、操作提示）"):
    """頁尾說明區：把原本散在頁首的 caption 收在這裡（Peggy：說明放下面，重點放上面）。"""
    import streamlit as st
    st.markdown("---")
    with st.expander(title, expanded=False):
        for p in paragraphs:
            st.markdown(p)
