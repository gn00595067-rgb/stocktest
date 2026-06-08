# -*- coding: utf-8 -*-
"""
services/v2_feedback_store.py — Web 回饋蒐集（本地 jsonl + Google Sheet）

兩種回饋：
    record_chat_feedback(msg_id, vote, ...)
        → reports/web_feedback/chat_feedback.jsonl
        → Google Sheet「Web_Feedback」分頁（用 gspread）

    record_report_feedback(date, session, rating, ...)
        → reports/web_feedback/report_feedback.jsonl
        → Google Sheet「Web_Feedback」分頁

Sheet 失敗只 warning；本地永遠寫成功。
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).parent.parent / "reports" / "web_feedback"


def _ensure_dir():
    _ROOT.mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def gen_msg_id() -> str:
    return uuid.uuid4().hex[:12]


def record_chat_feedback(
    *,
    msg_id: str,
    vote: str,
    note: str = "",
    user_question: str = "",
    ai_answer: str = "",
    model: str = "",
    report_date: str = "",
    report_session: str = "",
    session_id: str = "",
    cost_usd: float = 0.0,
    user_name: str = "",
) -> dict:
    _ensure_dir()
    row = {
        "type": "chat",
        "ts": _now_iso(),
        "msg_id": msg_id,
        "vote": vote,
        "note": (note or "")[:1500],
        "user_question": (user_question or "")[:500],
        "ai_answer": (ai_answer or "")[:1500],
        "model": model,
        "report_date": report_date,
        "report_session": report_session,
        "session_id": session_id,
        "cost_usd": cost_usd,
        "user_name": user_name,
    }
    with (_ROOT / "chat_feedback.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    _try_sync_to_sheet(row)
    return row


def record_report_feedback(
    *,
    date: str,
    session: str,
    rating: int,
    note: str = "",
    user_name: str = "",
) -> dict:
    _ensure_dir()
    row = {
        "type": "report",
        "ts": _now_iso(),
        "date": date,
        "session": session,
        "rating": int(rating),
        "note": (note or "")[:1500],
        "user_name": user_name or "anonymous",
    }
    with (_ROOT / "report_feedback.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    _try_sync_to_sheet(row)
    return row


# ─── gspread sync（沿用 stockanalysis 的 credential 模式）──────────

def _try_sync_to_sheet(row: dict) -> bool:
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        logger.debug("[feedback] gspread 不可用，跳過")
        return False

    creds_json = None
    sheet_id = None
    # 從 Streamlit secrets / env 取
    try:
        import streamlit as st
        if hasattr(st, "secrets"):
            creds_json = (
                st.secrets.get("GOOGLE_SHEET_CREDENTIALS")
                or st.secrets.get("GOOGLE_SHEET_CREDENTIALS_B64")
            )
            sheet_id = (
                st.secrets.get("WEB_FEEDBACK_SHEET_ID")
                or st.secrets.get("GOOGLE_SHEET_ID")
            )
    except Exception:
        pass
    if not creds_json:
        creds_json = (
            os.environ.get("GOOGLE_SHEET_CREDENTIALS")
            or os.environ.get("GOOGLE_SHEET_CREDENTIALS_B64")
        )
    if not sheet_id:
        sheet_id = (
            os.environ.get("WEB_FEEDBACK_SHEET_ID")
            or os.environ.get("GOOGLE_SHEET_ID")
        )

    if not creds_json or not sheet_id:
        logger.debug("[feedback] 無 credentials/sheet_id，跳過 sheet sync")
        return False

    # 解析 credentials（與 sheet_sync.py 同樣的模式）
    if isinstance(creds_json, str):
        s = creds_json.strip()
        if s.startswith("{"):
            try:
                creds_json = json.loads(s)
            except json.JSONDecodeError:
                creds_json = None
        else:
            try:
                import base64
                creds_json = json.loads(base64.b64decode(s).decode("utf-8"))
            except Exception:
                creds_json = None

    if not isinstance(creds_json, dict):
        logger.warning("[feedback] 無法解析 GOOGLE_SHEET_CREDENTIALS")
        return False

    try:
        creds = Credentials.from_service_account_info(
            creds_json,
            scopes=["https://www.googleapis.com/auth/spreadsheets",
                    "https://www.googleapis.com/auth/drive.file"],
        )
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(sheet_id)
        try:
            ws = sh.worksheet("Web_Feedback")
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(title="Web_Feedback", rows=1000, cols=12)
            ws.append_row([
                "type", "ts", "date_or_msg", "session", "vote_or_rating",
                "note", "user_question", "ai_answer", "model", "cost_usd",
                "session_id", "user_name",
            ])
        ws.append_row(_row_to_line(row))
        return True
    except Exception as e:
        logger.warning(f"[feedback] sheet sync 失敗：{e}")
        return False


def _row_to_line(row: dict) -> list:
    if row.get("type") == "chat":
        return [
            "chat", row.get("ts", ""), row.get("msg_id", ""),
            row.get("report_session", ""), row.get("vote", ""),
            row.get("note", ""), row.get("user_question", ""),
            (row.get("ai_answer", "") or "")[:500],
            row.get("model", ""), row.get("cost_usd", 0),
            row.get("session_id", ""), row.get("user_name", ""),
        ]
    return [
        "report", row.get("ts", ""), row.get("date", ""),
        row.get("session", ""), row.get("rating", ""),
        row.get("note", ""), "", "", "", 0, "",
        row.get("user_name", ""),
    ]
