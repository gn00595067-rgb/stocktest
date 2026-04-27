# -*- coding: utf-8 -*-
"""管理者頁：帳號與買賣人權限綁定"""
import streamlit as st

from db.database import get_session
from db.models import Trade, UserAccount, UserTraderBinding
from services.auth_service import (
    ROLE_ADMIN,
    ROLE_USER,
    ensure_bootstrap_admin,
    login_guard,
    render_auth_sidebar,
    is_admin,
    hash_password,
)

st.set_page_config(page_title="帳號與權限", layout="wide")
ensure_bootstrap_admin()
login_guard()
render_auth_sidebar()

if not is_admin():
    st.error("此頁僅管理者可使用。")
    st.stop()

st.title("帳號與權限管理")
st.caption("角色分為管理者 / 一般。一般帳號可綁定多位買賣人，只能查看與操作綁定資料。")

sess = get_session()
try:
    trader_names = sorted({x[0] for x in sess.query(Trade.user).distinct().all() if x[0]})
finally:
    sess.close()

st.subheader("新增帳號")
with st.form("create_user_form", clear_on_submit=True):
    c1, c2, c3 = st.columns(3)
    with c1:
        new_username = st.text_input("帳號")
    with c2:
        new_password = st.text_input("密碼", type="password")
    with c3:
        new_role = st.selectbox("角色", options=[ROLE_USER, ROLE_ADMIN], format_func=lambda x: "一般" if x == ROLE_USER else "管理者")
    create_submitted = st.form_submit_button("建立帳號", type="primary")

if create_submitted:
    if not new_username.strip() or not new_password.strip():
        st.error("帳號與密碼不可空白。")
    else:
        sess = get_session()
        try:
            exists = sess.query(UserAccount).filter(UserAccount.username == new_username.strip()).first()
            if exists:
                st.error("帳號已存在。")
            else:
                sess.add(
                    UserAccount(
                        username=new_username.strip(),
                        password_hash=hash_password(new_password),
                        role=new_role,
                        is_active=True,
                    )
                )
                sess.commit()
                st.success("帳號建立成功。")
                st.rerun()
        finally:
            sess.close()

st.markdown("---")
st.subheader("帳號清單與權限")

sess = get_session()
try:
    users = sess.query(UserAccount).order_by(UserAccount.id).all()
    bindings = sess.query(UserTraderBinding).all()
finally:
    sess.close()

bind_map = {}
for b in bindings:
    bind_map.setdefault(int(b.user_id), set()).add(b.trader_name)

for u in users:
    with st.container(border=True):
        role_label = "管理者" if u.role == ROLE_ADMIN else "一般"
        st.markdown(f"**{u.username}**（{role_label}）")
        c1, c2, c3, c4 = st.columns([1.4, 1.2, 1.8, 1.2])
        with c1:
            active = st.toggle("啟用", value=bool(u.is_active), key=f"active_{u.id}")
        with c2:
            role = st.selectbox("角色", options=[ROLE_USER, ROLE_ADMIN], index=0 if u.role == ROLE_USER else 1, format_func=lambda x: "一般" if x == ROLE_USER else "管理者", key=f"role_{u.id}")
        with c3:
            new_pwd = st.text_input("重設密碼（留白不變）", type="password", key=f"pwd_{u.id}")
        with c4:
            if st.button("儲存帳號設定", key=f"save_user_{u.id}", type="primary"):
                sess2 = get_session()
                try:
                    target = sess2.query(UserAccount).filter(UserAccount.id == int(u.id)).first()
                    if target:
                        target.is_active = bool(active)
                        target.role = role
                        if new_pwd.strip():
                            target.password_hash = hash_password(new_pwd.strip())
                        sess2.commit()
                        st.success(f"已更新 {u.username}")
                        st.rerun()
                finally:
                    sess2.close()

        if u.role == ROLE_USER:
            selected = st.multiselect(
                "可操作買賣人（多選）",
                options=trader_names,
                default=sorted(bind_map.get(int(u.id), set())),
                key=f"binds_{u.id}",
            )
            if st.button("儲存綁定", key=f"save_bind_{u.id}"):
                sess3 = get_session()
                try:
                    sess3.query(UserTraderBinding).filter(UserTraderBinding.user_id == int(u.id)).delete()
                    for name in selected:
                        sess3.add(UserTraderBinding(user_id=int(u.id), trader_name=name))
                    sess3.commit()
                    st.success(f"已更新 {u.username} 綁定買賣人。")
                    st.rerun()
                finally:
                    sess3.close()
