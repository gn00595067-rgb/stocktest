# -*- coding: utf-8 -*-
"""登入與權限服務（管理者 / 一般）"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from typing import Iterable

import streamlit as st

from db.database import get_session
from db.models import UserAccount, UserTraderBinding

ROLE_ADMIN = "admin"
ROLE_USER = "user"


def hash_password(password: str, salt: str | None = None) -> str:
    if salt is None:
        salt = secrets.token_hex(16)
    pwd = (password or "").encode("utf-8")
    digest = hashlib.pbkdf2_hmac("sha256", pwd, salt.encode("utf-8"), 100_000).hex()
    return f"{salt}${digest}"


def verify_password(password: str, hashed: str) -> bool:
    if not hashed or "$" not in hashed:
        return False
    salt, old_digest = hashed.split("$", 1)
    new_hashed = hash_password(password, salt)
    _, new_digest = new_hashed.split("$", 1)
    return hmac.compare_digest(old_digest, new_digest)


def ensure_bootstrap_admin() -> None:
    """若尚無任何帳號，建立預設管理者 admin/admin1234。"""
    sess = get_session()
    try:
        has_user = sess.query(UserAccount.id).first() is not None
        if has_user:
            return
        sess.add(
            UserAccount(
                username="admin",
                password_hash=hash_password("admin1234"),
                role=ROLE_ADMIN,
                is_active=True,
            )
        )
        sess.commit()
    finally:
        sess.close()


def get_current_user() -> dict | None:
    return st.session_state.get("auth_user")


def is_admin() -> bool:
    user = get_current_user()
    return bool(user and user.get("role") == ROLE_ADMIN)


def get_allowed_traders() -> list[str] | None:
    """
    回傳可操作的買賣人名單。
    - 管理者：None（代表不限制）
    - 一般：綁定清單
    """
    user = get_current_user()
    if not user:
        return []
    if user.get("role") == ROLE_ADMIN:
        return None
    sess = get_session()
    try:
        rows = (
            sess.query(UserTraderBinding.trader_name)
            .filter(UserTraderBinding.user_id == int(user["id"]))
            .all()
        )
        return sorted({r[0] for r in rows if r[0]})
    finally:
        sess.close()


def can_access_trader(trader_name: str) -> bool:
    allowed = get_allowed_traders()
    if allowed is None:
        return True
    return trader_name in set(allowed)


def filter_trades_by_permission(trades: Iterable) -> list:
    allowed = get_allowed_traders()
    if allowed is None:
        return list(trades)
    allowed_set = set(allowed)
    return [t for t in trades if getattr(t, "user", None) in allowed_set]


def login_guard() -> None:
    if st.session_state.get("auth_logged_in"):
        return

    st.title("請先登入")
    st.caption("預設管理者：`admin / admin1234`（首次登入後請立即修改密碼）")
    with st.form("login_form", clear_on_submit=False):
        username = st.text_input("帳號")
        password = st.text_input("密碼", type="password")
        submitted = st.form_submit_button("登入", type="primary")
    if submitted:
        sess = get_session()
        try:
            user = sess.query(UserAccount).filter(UserAccount.username == username.strip()).first()
            if (not user) or (not user.is_active):
                st.error("帳號不存在或已停用。")
                return
            if not verify_password(password, user.password_hash or ""):
                st.error("密碼錯誤。")
                return
            st.session_state["auth_logged_in"] = True
            st.session_state["auth_user"] = {
                "id": int(user.id),
                "username": user.username,
                "role": user.role,
            }
            st.rerun()
        finally:
            sess.close()
    st.stop()


def render_auth_sidebar() -> None:
    user = get_current_user()
    if not user:
        return
    role_text = "管理者" if user.get("role") == ROLE_ADMIN else "一般"
    st.sidebar.markdown("---")
    st.sidebar.caption(f"👤 {user.get('username')}（{role_text}）")
    if st.sidebar.button("登出"):
        st.session_state.pop("auth_logged_in", None)
        st.session_state.pop("auth_user", None)
        st.rerun()
