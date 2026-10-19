# -*- coding: utf-8 -*-
"""只輸入密碼登入：用密碼分辨帳號、停用帳號不能登入、密碼不可重複、輸錯鎖定。"""
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import services.auth_service as auth
from db.models import Base, UserAccount


@pytest.fixture
def users(monkeypatch):
    e = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(e)
    S = sessionmaker(bind=e)
    s = S()
    for i, (name, pw, active) in enumerate([("老闆", "12345678", True), ("開發者", "dev12345678", True),
                                             ("admin", "12345678", False)], start=1):
        s.add(UserAccount(id=i, username=name, password_hash=auth.hash_password(pw), role="admin",
                          is_active=active, created_at=datetime(2026, 10, 19)))
    s.commit()
    s.close()
    monkeypatch.setattr(auth, "get_session", S)
    return S


def test_find_user_by_password(users):
    assert auth.find_user_by_password("12345678")["username"] == "老闆"
    assert auth.find_user_by_password("dev12345678")["username"] == "開發者"
    assert auth.find_user_by_password("wrong") is None
    assert auth.find_user_by_password("") is None


def test_disabled_account_cannot_login(users):
    s = users()
    s.query(UserAccount).filter(UserAccount.username == "老闆").first().is_active = False
    s.commit()
    s.close()
    assert auth.find_user_by_password("12345678") is None    # 停用的 admin 同密碼也不行


def test_password_in_use(users):
    assert auth.password_in_use("12345678")                   # 老闆在用
    assert not auth.password_in_use("12345678", exclude_user_id=1)   # 改自己的密碼不算重複
    assert not auth.password_in_use("brand-new-pw")


def test_recent_fails_window(monkeypatch):
    monkeypatch.setattr(auth, "_login_fails", [100.0, 130.0, 159.0])
    assert auth._recent_fails(159.5) == 3                     # 1 分鐘內 3 次
    assert auth._recent_fails(195.0) == 1                     # 60 秒前的不算
