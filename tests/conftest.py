# -*- coding: utf-8 -*-
"""全部測試共用：不要真的連網抓官方收盤價（收盤後跑測試會去打證交所／櫃買，又慢又會讓報價測試結果跟時間有關）。"""
import pytest


@pytest.fixture(autouse=True)
def _no_official_close_fetch(monkeypatch):
    import services.price_service as ps
    monkeypatch.setattr(ps, "official_closes", lambda now_ts=None: {})
