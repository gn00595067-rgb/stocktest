# -*- coding: utf-8 -*-
"""「記住我」cookie token 的簽發/驗證安全性測試（不觸碰真實 DB）。"""
from types import SimpleNamespace

import services.auth_service as auth
from services.auth_service import _make_remember_token, _verify_remember_token, hash_password


def _fake_session_for(user):
    """回傳一個假 session，query(...).filter(...).first() 一律回傳指定 user。"""
    class _Q:
        def filter(self, *a, **k):
            return self

        def first(self):
            return user

    class _S:
        def query(self, *a, **k):
            return _Q()

        def close(self):
            pass

    return _S()


def _patch_user(monkeypatch, user):
    monkeypatch.setattr(auth, "get_session", lambda: _fake_session_for(user))


def test_round_trip_valid(monkeypatch):
    pw = hash_password("secret123")
    user = SimpleNamespace(id=7, username="jonathan", role="admin", is_active=True, password_hash=pw)
    _patch_user(monkeypatch, user)

    token = _make_remember_token("jonathan", pw)
    got = _verify_remember_token(token)
    assert got == {"id": 7, "username": "jonathan", "role": "admin"}


def test_tampered_signature_rejected(monkeypatch):
    pw = hash_password("secret123")
    user = SimpleNamespace(id=7, username="jonathan", role="admin", is_active=True, password_hash=pw)
    _patch_user(monkeypatch, user)

    token = _make_remember_token("jonathan", pw)
    payload_b64, _sig = token.rsplit(".", 1)
    forged = f"{payload_b64}.deadbeef"
    assert _verify_remember_token(forged) is None


def test_expired_token_rejected(monkeypatch):
    pw = hash_password("secret123")
    user = SimpleNamespace(id=7, username="jonathan", role="admin", is_active=True, password_hash=pw)
    _patch_user(monkeypatch, user)

    token = _make_remember_token("jonathan", pw, days=-1)  # 已過期
    assert _verify_remember_token(token) is None


def test_password_change_invalidates(monkeypatch):
    old_pw = hash_password("secret123")
    token = _make_remember_token("jonathan", old_pw)

    # DB 端密碼已換 → 簽章金鑰不同 → 失效
    new_pw = hash_password("brand-new-pw")
    user = SimpleNamespace(id=7, username="jonathan", role="admin", is_active=True, password_hash=new_pw)
    _patch_user(monkeypatch, user)
    assert _verify_remember_token(token) is None


def test_inactive_user_rejected(monkeypatch):
    pw = hash_password("secret123")
    token = _make_remember_token("jonathan", pw)
    user = SimpleNamespace(id=7, username="jonathan", role="admin", is_active=False, password_hash=pw)
    _patch_user(monkeypatch, user)
    assert _verify_remember_token(token) is None


def test_unknown_user_rejected(monkeypatch):
    pw = hash_password("secret123")
    token = _make_remember_token("ghost", pw)
    _patch_user(monkeypatch, None)
    assert _verify_remember_token(token) is None


def test_garbage_token_rejected(monkeypatch):
    _patch_user(monkeypatch, None)
    assert _verify_remember_token("") is None
    assert _verify_remember_token("no-dot") is None
    assert _verify_remember_token("not_base64.abc") is None
