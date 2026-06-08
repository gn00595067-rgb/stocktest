# -*- coding: utf-8 -*-
"""
services/v2_report_context.py — 載入 v2 PipelineResult / HTML 報告給 9_v2分析報告對話 用

掃描優先順序：
    1. 環境變數 V2_REPORTS_PATH（外部 taiwan-stock-analyzer/reports 目錄）
    2. stockanalysis/reports/（本機）
    3. 使用者透過 file_uploader 上傳

回傳 ReportEntry list（可選） + load_context(entry) 給 chat 用。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ─── 掃描路徑 ──────────────────────────────────────────────────────

def _candidate_dirs() -> list[Path]:
    out: list[Path] = []
    ext = os.environ.get("V2_REPORTS_PATH", "").strip()
    if ext:
        p = Path(ext)
        if p.exists():
            out.append(p)
    local = Path(__file__).parent.parent / "reports"
    if local.exists():
        out.append(local)
    return out


@dataclass
class ReportEntry:
    date: str
    session: str
    html_path: Optional[Path] = None
    json_path: Optional[Path] = None
    mtime: float = 0.0

    @property
    def label(self) -> str:
        sess_map = {
            "pre": "盤前", "open": "盤中",
            "post_e": "盤後早段", "post_l": "盤後完整",
            "weekend": "週末", "generic": "—",
        }
        return f"{self.date} · {sess_map.get(self.session, self.session)}"


def list_available_reports() -> list[ReportEntry]:
    entries: dict[tuple[str, str], ReportEntry] = {}
    for d in _candidate_dirs():
        # 版本化檔名：v2pro_YYYYMMDD_session_NNN.html
        for p in d.glob("v2pro_*.html"):
            name = p.stem
            parts = name.split("_")
            if len(parts) < 3:
                continue
            ymd = parts[1]
            if len(ymd) != 8 or not ymd.isdigit():
                continue
            if len(parts) >= 4 and parts[2] == "post":
                session = f"post_{parts[3]}"
            else:
                session = parts[2]
            date = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}"
            key = (date, session)
            mtime = p.stat().st_mtime
            if key not in entries or mtime > entries[key].mtime:
                entries[key] = ReportEntry(
                    date=date, session=session, html_path=p, mtime=mtime,
                )

        # 退而求其次：v2_pro_report_YYYYMMDD.html
        for p in d.glob("v2_pro_report_*.html"):
            name = p.stem
            ymd = name.split("_")[-1]
            if len(ymd) != 8 or not ymd.isdigit():
                continue
            date = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}"
            key = (date, "generic")
            if key not in entries:
                entries[key] = ReportEntry(
                    date=date, session="generic", html_path=p,
                    mtime=p.stat().st_mtime,
                )

        # v2_result_*.json
        for p in d.glob("v2_result_*.json"):
            name = p.stem
            parts = name.split("_")
            if len(parts) < 3:
                continue
            ymd = parts[2]
            if len(ymd) != 8 or not ymd.isdigit():
                continue
            if len(parts) >= 5 and parts[3] == "post":
                session = f"post_{parts[4]}"
            elif len(parts) >= 4:
                session = parts[3]
            else:
                session = "generic"
            date = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}"
            key = (date, session)
            if key in entries:
                entries[key].json_path = p
            else:
                entries[key] = ReportEntry(
                    date=date, session=session, json_path=p,
                    mtime=p.stat().st_mtime,
                )
    return sorted(entries.values(), key=lambda e: e.mtime, reverse=True)


# ─── 給 Chat 用的精簡 context ──────────────────────────────────────

@dataclass
class ReportContext:
    date: str
    session: str
    market_brief: str = ""
    scenario: str = ""
    macro_bias: str = ""
    permission: str = ""
    permission_reasons: list[str] = field(default_factory=list)
    forbidden_actions: list[str] = field(default_factory=list)
    executable_summary: list[dict] = field(default_factory=list)
    observe_summary: list[dict] = field(default_factory=list)
    position_defense_summary: list[dict] = field(default_factory=list)
    urgent_holdings: list[dict] = field(default_factory=list)
    ai_explanations: dict[str, str] = field(default_factory=dict)
    raw_result_path: str = ""


def _action_card_brief(c: dict) -> dict:
    out = {
        "code": c.get("code", ""),
        "name": c.get("name", ""),
        "role": c.get("stock_role", ""),
        "sector": c.get("sector", ""),
        "permission": c.get("action_permission", ""),
        "today_action": c.get("today_action", ""),
        "action_score": c.get("action_score"),
        "freshness": c.get("freshness_label", ""),
    }
    if isinstance(c.get("entry_plan"), dict):
        out["entry"] = c["entry_plan"].get("entry_price")
    if isinstance(c.get("stop_plan"), dict):
        out["stop"] = c["stop_plan"].get("price")
    if isinstance(c.get("target_plan"), dict):
        out["target"] = c["target_plan"].get("target_1")
    if isinstance(c.get("rr"), dict):
        out["rr"] = c["rr"].get("rr_ratio")
    if c.get("key_reasons"):
        out["reasons"] = list(c["key_reasons"])[:3]
    return out


def load_context(entry: ReportEntry) -> ReportContext:
    ctx = ReportContext(
        date=entry.date, session=entry.session,
        raw_result_path=str(entry.json_path) if entry.json_path else "",
    )
    if not entry.json_path or not entry.json_path.exists():
        return ctx
    try:
        data = json.loads(entry.json_path.read_text(encoding="utf-8"))
    except Exception:
        return ctx

    ctx.market_brief = data.get("market_brief_text", "") or ""
    scen = data.get("scenario") or {}
    if isinstance(scen, dict):
        prim = scen.get("primary")
        if isinstance(prim, dict):
            ctx.scenario = prim.get("value") or ""
        elif isinstance(prim, str):
            ctx.scenario = prim

    intl = data.get("international") or {}
    if isinstance(intl, dict):
        mb = intl.get("macro_bias")
        if isinstance(mb, dict):
            ctx.macro_bias = mb.get("value") or ""
        elif isinstance(mb, str):
            ctx.macro_bias = mb

    perm = data.get("permission") or {}
    if isinstance(perm, dict):
        pp = perm.get("permission")
        if isinstance(pp, dict):
            ctx.permission = pp.get("value") or ""
        elif isinstance(pp, str):
            ctx.permission = pp
        ctx.permission_reasons = list(perm.get("reasons") or [])[:5]
        ctx.forbidden_actions = list(perm.get("forbidden_actions") or [])[:8]

    bl = data.get("battle_list") or {}
    if isinstance(bl, dict):
        ctx.executable_summary = [_action_card_brief(c) for c in (bl.get("executable_list") or [])[:15]]
        ctx.observe_summary = [_action_card_brief(c) for c in (bl.get("observe_only_list") or [])[:20]]
        ctx.position_defense_summary = [_action_card_brief(c) for c in (bl.get("position_defense_list") or [])[:15]]

    hu = data.get("holdings_urgency") or {}
    if isinstance(hu, dict):
        urgent = []
        for code, u in hu.items():
            if not isinstance(u, dict):
                continue
            urgent.append({
                "code": code,
                "urgency": u.get("urgency", ""),
                "reason": (u.get("reason") or "")[:200],
                "suggested": u.get("suggested_action", ""),
            })
        urg_order = {"execute_stop": 0, "review": 1, "trailing_stop": 2}
        ctx.urgent_holdings = sorted(urgent, key=lambda x: urg_order.get(x["urgency"], 9))[:10]

    aiex = data.get("ai_explanations") or {}
    if isinstance(aiex, dict):
        ctx.ai_explanations = {c: (t or "")[:600] for c, t in list(aiex.items())[:20]}

    return ctx


def load_context_from_uploaded_json(json_text: str) -> ReportContext:
    """從上傳的 JSON 字串直接建 context（給 file_uploader 用）。"""
    try:
        data = json.loads(json_text)
    except Exception:
        return ReportContext(date="uploaded", session="generic")
    date = data.get("date", "uploaded")
    if len(date) == 8 and date.isdigit():
        date = f"{date[:4]}-{date[4:6]}-{date[6:]}"
    session = data.get("run_phase", "generic")
    # 用一個臨時 entry 走 load_context 邏輯
    import tempfile
    p = Path(tempfile.mktemp(suffix=".json"))
    p.write_text(json_text, encoding="utf-8")
    entry = ReportEntry(date=date, session=session, json_path=p, mtime=0.0)
    ctx = load_context(entry)
    try:
        p.unlink()
    except Exception:
        pass
    return ctx
