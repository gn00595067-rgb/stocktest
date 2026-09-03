# -*- coding: utf-8 -*-
"""管理者頁：帳號與買賣人權限綁定"""
import streamlit as st
from sqlalchemy import func

from db.database import get_session, get_engine
from db.models import UserAccount, UserTraderBinding
from services.trader_service import all_trader_names
from services.auth_service import (
    ROLE_ADMIN,
    ROLE_USER,
    ensure_bootstrap_admin,
    login_guard,
    render_auth_sidebar,
    is_admin,
    hash_password,
)


def _sync_to_sheet_after_auth_change() -> tuple[bool, str | None]:
    """帳號/權限變更後立即強制同步，避免重啟後遺失。"""
    try:
        from services.sheet_sync import is_google_sheet_enabled, sync_db_to_sheet
        if not is_google_sheet_enabled():
            return True, None
        return sync_db_to_sheet(get_engine())
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"

st.set_page_config(page_title="帳號與權限", layout="wide")
from services.mobile_ui import inject_mobile_css
inject_mobile_css()
ensure_bootstrap_admin()
login_guard()
render_auth_sidebar()

if not is_admin():
    st.error("此頁僅管理者可使用。")
    st.stop()

st.title("帳號與權限管理")
st.caption("角色分為管理者 / 一般。一般帳號可綁定多位買賣人，只能查看與操作綁定資料。")

# 買賣人可綁定名單＝主檔 ∪ 交易出現過（含剛新增、還沒任何交易的買賣人）
trader_names = all_trader_names()

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
            # 帳號不分大小寫視為同一個（避免 ELSA / elsa 併存造成登入混淆）
            exists = (
                sess.query(UserAccount)
                .filter(func.lower(UserAccount.username) == new_username.strip().lower())
                .first()
            )
            if exists:
                st.error(f"帳號已存在（大小寫視為相同）：{exists.username}")
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
                ok, err = _sync_to_sheet_after_auth_change()
                if ok:
                    st.success("帳號建立成功，且已同步到 Google Sheet。")
                    st.rerun()
                else:
                    st.error(f"帳號已建立，但同步到 Google Sheet 失敗：{err}")
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
        new_name = st.text_input("帳號（可修改，大小寫皆可；登入時不分大小寫）", value=u.username, key=f"uname_{u.id}")
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
                        # 改帳號：不可空白；與其他帳號不分大小寫不可重複
                        uname = (new_name or "").strip()
                        name_error = None
                        if not uname:
                            name_error = "帳號不可空白。"
                        elif uname.lower() != (u.username or "").lower():
                            dup = (
                                sess2.query(UserAccount)
                                .filter(func.lower(UserAccount.username) == uname.lower())
                                .filter(UserAccount.id != int(u.id))
                                .first()
                            )
                            if dup:
                                name_error = f"帳號已被使用（大小寫視為相同）：{dup.username}"
                        if name_error:
                            st.error(name_error)
                        else:
                            target.username = uname
                            target.is_active = bool(active)
                            target.role = role
                            if new_pwd.strip():
                                target.password_hash = hash_password(new_pwd.strip())
                            sess2.commit()
                            ok, err = _sync_to_sheet_after_auth_change()
                            if ok:
                                st.success(f"已更新 {uname}，且已同步到 Google Sheet。")
                                st.rerun()
                            else:
                                st.error(f"已更新 {uname}，但同步到 Google Sheet 失敗：{err}")
                finally:
                    sess2.close()

        # 依「角色下拉當下的選擇」即時顯示買賣人綁定區（不是等存檔後才出現）。
        # 管理者可看全部買賣人、不需綁定，故只有選「一般」時才顯示。
        if role == ROLE_USER:
            if u.role != ROLE_USER:
                st.info("此帳號目前仍是「管理者」。請先按上方『儲存帳號設定』把角色改為一般，下面的買賣人綁定才會生效。")
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
                    ok, err = _sync_to_sheet_after_auth_change()
                    if ok:
                        st.success(f"已更新 {u.username} 綁定買賣人，且已同步到 Google Sheet。")
                        st.rerun()
                    else:
                        st.error(f"已更新 {u.username} 綁定，但同步到 Google Sheet 失敗：{err}")
                finally:
                    sess3.close()
        else:
            st.caption("👑 管理者可檢視／操作全部買賣人，無需綁定。若要限制只能看特定買賣人，請把「角色」改為『一般』並儲存。")
