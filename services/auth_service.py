# -*- coding: utf-8 -*-
"""登入與權限服務（管理者 / 一般）"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from datetime import datetime, timedelta
from typing import Iterable

import streamlit as st
from sqlalchemy import func

from db.database import get_session
from db.models import UserAccount, UserTraderBinding

ROLE_ADMIN = "admin"
ROLE_USER = "user"

# ---------- 「記住我」cookie 設定 ----------
REMEMBER_COOKIE = "st_remember"
REMEMBER_DAYS = 90
# 網址上的記住我 token：iPad Safari「防止跨網站追蹤」會擋掉元件（iframe）cookie，
# 此時改用網址參數自動登入。token 與 cookie 同一枚（HMAC 簽章、綁密碼、會過期）。
REMEMBER_QP = "rt"


def _get_url_token() -> str | None:
    try:
        v = st.query_params.get(REMEMBER_QP)
        return str(v) if v else None
    except Exception:
        return None


def _set_url_token(token: str) -> None:
    try:
        st.query_params[REMEMBER_QP] = token
    except Exception:
        pass


def _clear_url_token() -> None:
    try:
        if REMEMBER_QP in st.query_params:
            del st.query_params[REMEMBER_QP]
    except Exception:
        pass


def _cookie_manager():
    """回傳單一 CookieManager 實例（每個 session 只建立一次，避免重複 key 崩潰）。"""
    cm = st.session_state.get("_cookie_mgr")
    if cm is None:
        try:
            import extra_streamlit_components as stx
            cm = stx.CookieManager(key="auth_cookie_mgr")
        except Exception:
            cm = False  # 標記為不可用，之後略過 cookie 功能
        st.session_state["_cookie_mgr"] = cm
    return cm or None


def _make_remember_token(username: str, password_hash: str, days: int = REMEMBER_DAYS) -> str:
    """產生記住我 token：payload(base64).簽章；簽章金鑰=該帳號 password_hash，不含密碼本身。"""
    expiry = int(time.time()) + days * 24 * 3600
    payload = json.dumps({"u": username, "e": expiry}, separators=(",", ":"))
    payload_b64 = base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")
    sig = hmac.new(
        (password_hash or "").encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256
    ).hexdigest()
    return f"{payload_b64}.{sig}"


def _verify_remember_token(token: str) -> dict | None:
    """驗證 token，回傳登入用 user dict；失敗回 None。"""
    if not token or "." not in token:
        return None
    payload_b64, sig = token.rsplit(".", 1)
    try:
        payload = json.loads(base64.urlsafe_b64decode(payload_b64.encode("ascii")))
    except Exception:
        return None
    username = payload.get("u")
    expiry = payload.get("e")
    if not username or not expiry or int(expiry) < int(time.time()):
        return None
    sess = get_session()
    try:
        user = (
            sess.query(UserAccount)
            .filter(func.lower(UserAccount.username) == str(username).strip().lower())
            .first()
        )
        if (not user) or (not user.is_active):
            return None
        expected = hmac.new(
            (user.password_hash or "").encode("utf-8"),
            payload_b64.encode("ascii"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, sig):
            return None
        return {"id": int(user.id), "username": user.username, "role": user.role}
    finally:
        sess.close()


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
        # 首次建立 admin 後，若啟用 Google Sheet，立即落盤避免重啟遺失。
        try:
            from db.database import get_engine
            from services.sheet_sync import is_google_sheet_enabled, sync_db_to_sheet
            if is_google_sheet_enabled():
                sync_db_to_sheet(get_engine())
        except Exception:
            pass
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
    cm = _cookie_manager()

    # 待刪除的 cookie（登出、或登入時未勾記住我）在乾淨的一輪處理，避免與 rerun 競態
    if cm is not None and st.session_state.pop("_pending_remember_clear", False):
        try:
            cm.delete(REMEMBER_COOKIE)
        except Exception:
            pass

    if st.session_state.get("auth_logged_in"):
        return

    # 嘗試以 cookie 自動登入。
    # 註：CookieManager.get_all() 首輪與「真的沒有 cookie」都回傳 {}，無法區分；
    # 元件在瀏覽器端讀到 cookie 後會自動觸發 rerun，屆時本函式會再跑一次而讀到值。
    # 記住的使用者冷啟動可能先閃一下登入頁再自動登入，此為此元件的固有行為。
    # 登出後以 _remember_disabled 擋住自動登入。
    if cm is not None and not st.session_state.get("_remember_disabled"):
        try:
            cookies = cm.get_all() or {}
        except Exception:
            cookies = {}
        token = cookies.get(REMEMBER_COOKIE)
        if token:
            remembered = _verify_remember_token(str(token))
            if remembered:
                st.session_state["auth_logged_in"] = True
                st.session_state["auth_user"] = remembered
                st.rerun()

    # cookie 不可用/被擋（iPad Safari）時的後備：以網址 token 自動登入
    if not st.session_state.get("_remember_disabled"):
        url_token = _get_url_token()
        if url_token:
            remembered = _verify_remember_token(url_token)
            if remembered:
                st.session_state["auth_logged_in"] = True
                st.session_state["auth_user"] = remembered
                st.rerun()

    st.title("請先登入")
    st.caption("預設管理者：`admin / admin1234`（首次登入後請立即修改密碼）")
    with st.form("login_form", clear_on_submit=False):
        username = st.text_input("帳號")
        password = st.text_input("密碼", type="password")
        remember = st.checkbox("記住我（90 天內免登入）", value=True)
        submitted = st.form_submit_button("登入", type="primary")
    if submitted:
        sess = get_session()
        try:
            # 帳號登入不分大小寫（ELSA / elsa 皆可登入）
            user = (
                sess.query(UserAccount)
                .filter(func.lower(UserAccount.username) == username.strip().lower())
                .first()
            )
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
            if remember:
                token = _make_remember_token(user.username, user.password_hash or "")
                # cookie：於下一輪（已登入、乾淨畫面）寫入，確保確實落地
                if cm is not None:
                    st.session_state["_pending_remember"] = token
                # 網址 token：iPad Safari 擋 cookie 時的後備，立即寫進網址
                _set_url_token(token)
                st.session_state.pop("_remember_disabled", None)
            else:
                st.session_state["_pending_remember_clear"] = True
                st.session_state["_remember_disabled"] = True
                _clear_url_token()
            st.rerun()
        finally:
            sess.close()
    st.stop()


def render_auth_sidebar() -> None:
    user = get_current_user()
    if not user:
        return

    # 登入時勾了記住我：在此（已登入的乾淨一輪）真正寫入 cookie
    cm = _cookie_manager()
    if cm is not None and "_pending_remember" in st.session_state:
        token = st.session_state.pop("_pending_remember")
        try:
            cm.set(
                REMEMBER_COOKIE,
                token,
                expires_at=datetime.now() + timedelta(days=REMEMBER_DAYS),
            )
        except Exception:
            pass

    role_text = "管理者" if user.get("role") == ROLE_ADMIN else "一般"
    st.sidebar.markdown("---")
    st.sidebar.caption(f"👤 {user.get('username')}（{role_text}）")
    if st.sidebar.button("登出"):
        # 標記清除 cookie 並略過本 session 的自動登入，交由下一輪 login_guard 刪除
        st.session_state["_pending_remember_clear"] = True
        st.session_state["_remember_disabled"] = True
        _clear_url_token()  # 一併清掉網址上的記住我 token
        st.session_state.pop("auth_logged_in", None)
        st.session_state.pop("auth_user", None)
        st.rerun()
