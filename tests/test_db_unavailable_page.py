# -*- coding: utf-8 -*-
"""資料庫連不上時，頁面顯示看得懂的原因（docs/specs/資料庫額度監控.md）。"""
from streamlit.testing.v1 import AppTest

# 腳本內容只能用英文：AppTest 在 Windows 會用系統編碼（cp950）寫暫存檔
_SCRIPT = """
from sqlalchemy.exc import OperationalError
import services.auth_service as auth

class _Sess:
    def query(self, *a, **k):
        raise OperationalError("SELECT 1", {}, Exception(MSG))
    def close(self):
        pass

_orig = auth.get_session
auth.get_session = lambda: _Sess()
try:
    auth.ensure_bootstrap_admin()
finally:
    auth.get_session = _orig   # restore for other tests in the same process
import streamlit as st
st.write("SHOULD_NOT_RUN")
"""


def _run(msg):
    at = AppTest.from_string(f"MSG = {msg!r}\n" + _SCRIPT)
    at.run(timeout=30)
    return at


def test_quota_exceeded_shows_clear_message():
    at = _run("ERROR: Your account or project has exceeded the compute time quota. Upgrade your plan to increase limits.")
    assert not at.exception
    assert "免費額度已用完" in at.error[0].value
    assert "資料都還在" in at.error[0].value
    assert not any("SHOULD_NOT_RUN" in m.value for m in at.markdown)


def test_other_connection_error_shows_generic_message():
    at = _run("could not connect to server: Connection timed out")
    assert not at.exception
    assert "暫時連不上" in at.error[0].value
