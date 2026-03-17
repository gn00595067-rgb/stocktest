# -*- coding: utf-8 -*-
# 延遲載入 price_service / pnl_engine，避免在「只 import stock_list_loader」時觸及 st.secrets 等導致 KeyError（例如 Streamlit Cloud）。

__all__ = [
    "get_quote_cached",
    "get_price_service",
    "fetch_stock_list_finmind",
    "fetch_stock_list_cached",
    "clear_quote_cache",
    "get_finmind_debug",
    "compute_matches",
    "Lot",
]


def __getattr__(name):
    try:
        if name == "get_quote_cached":
            from .price_service import get_quote_cached
            return get_quote_cached
        if name == "get_price_service":
            from .price_service import get_price_service
            return get_price_service
        if name == "fetch_stock_list_finmind":
            from .price_service import fetch_stock_list_finmind
            return fetch_stock_list_finmind
        if name == "fetch_stock_list_cached":
            from .price_service import fetch_stock_list_cached
            return fetch_stock_list_cached
        if name == "clear_quote_cache":
            from .price_service import clear_quote_cache
            return clear_quote_cache
        if name == "get_finmind_debug":
            from .price_service import get_finmind_debug
            return get_finmind_debug
        if name == "compute_matches":
            from .pnl_engine import compute_matches
            return compute_matches
        if name == "Lot":
            from .pnl_engine import Lot
            return Lot
    except KeyError:
        # 雲端環境有時 import 會觸發 KeyError（例如 st.secrets），轉成 AttributeError
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
