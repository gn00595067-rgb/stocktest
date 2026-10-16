# -*- coding: utf-8 -*-
"""Neon 資料庫額度監控（規格：docs/specs/資料庫額度監控.md）。

- 查本月運算用量：Neon API GET /projects/{id} 的 compute_time_seconds（CU 秒）。
- 判斷連線錯誤是不是「額度用完」。
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

API_BASE = "https://console.neon.tech/api/v2"
FREE_CU_HOURS = 100.0     # 免費版每月運算額度
WARN_CU_HOURS = 70.0      # 側欄黃字提醒
DANGER_CU_HOURS = 90.0    # 側欄紅字
ALERT_CU_HOURS = 80.0     # 每日檢查：超過就讓 GitHub Actions 失敗寄信


def get_secret(name: str) -> Optional[str]:
    """先讀環境變數，再讀 Streamlit Secrets（雲端）。"""
    v = os.environ.get(name)
    if v:
        return v.strip()
    try:
        import streamlit as st
        v = st.secrets.get(name)
        return str(v).strip() if v else None
    except Exception:
        return None


def is_quota_error(exc: BaseException) -> bool:
    """Neon 額度用完時拒絕連線，錯誤訊息會提到 quota（例：exceeded the compute time quota）。"""
    msg = str(exc).lower()
    return "quota" in msg and ("exceed" in msg or "limit" in msg)


def _get(path: str, api_key: str, timeout: float = 10):
    import requests
    r = requests.get(f"{API_BASE}{path}", headers={"Authorization": f"Bearer {api_key}",
                                                     "Accept": "application/json"}, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _parse_dt(v) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")) if v else None
    except ValueError:
        return None


def summarize_project(project: dict) -> dict:
    """從 Neon 的 project 物件取出本期用量（CU 小時、起訖日）。"""
    sec = project.get("compute_time_seconds")
    if sec is None:
        sec = project.get("cpu_used_sec") or 0
    return {
        "project_id": project.get("id"),
        "project_name": project.get("name"),
        "cu_hours": round(float(sec or 0) / 3600.0, 2),
        "active_hours": round(float(project.get("active_time_seconds") or 0) / 3600.0, 1),
        "period_start": _parse_dt(project.get("consumption_period_start")),
        "period_end": _parse_dt(project.get("consumption_period_end")),
    }


def fetch_usage(api_key: str, project_id: Optional[str] = None) -> dict:
    """查本期用量。沒給 project_id 時：帳號下只有一個專案就用它，否則找名稱 stocktest。"""
    if not project_id:
        projects = _get("/projects", api_key).get("projects") or []
        if not projects:
            raise RuntimeError("這把 API key 底下沒有任何 Neon 專案")
        pick = projects[0] if len(projects) == 1 else next(
            (p for p in projects if p.get("name") == "stocktest"), None)
        if not pick:
            raise RuntimeError("有多個專案，請設定 NEON_PROJECT_ID")
        project_id = pick["id"]
    return summarize_project(_get(f"/projects/{project_id}", api_key).get("project") or {})


def level(cu_hours: float) -> str:
    """ok / warn / danger / over"""
    if cu_hours >= FREE_CU_HOURS:
        return "over"
    if cu_hours >= DANGER_CU_HOURS:
        return "danger"
    if cu_hours >= WARN_CU_HOURS:
        return "warn"
    return "ok"
