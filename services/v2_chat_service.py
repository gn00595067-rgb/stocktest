# -*- coding: utf-8 -*-
"""
services/v2_chat_service.py — Claude API（prompt caching + 模型路由）

特色：
    1. Prompt caching：報告 context 放第 2 個 system block 並標 cache_control
    2. 模型路由：依關鍵字升 Opus 4.7，預設 Sonnet 4.6
    3. 失敗回退：API key 缺、SDK 缺都不會 crash
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Optional

OPUS_MODEL = "claude-opus-4-7"
SONNET_MODEL = "claude-sonnet-4-6"

SONNET_IN = 3.0
SONNET_OUT = 15.0
SONNET_CACHED_IN = 0.30
OPUS_IN = 15.0
OPUS_OUT = 75.0
OPUS_CACHED_IN = 1.50

_OPUS_TRIGGERS = re.compile(
    r"(互審|多股|跨股|比較.*股|深入分析|深度分析|完整推理|"
    r"cross.?check|多檔|逐檔對比|互相驗證|矛盾|衝突)",
    re.IGNORECASE,
)


def pick_model(user_msg: str) -> str:
    if _OPUS_TRIGGERS.search(user_msg or ""):
        return OPUS_MODEL
    return SONNET_MODEL


@dataclass
class ChatMessage:
    role: str
    content: str
    model: str = ""
    cost_usd: float = 0.0
    msg_id: str = ""
    feedback: Optional[str] = None


_BASE_SYSTEM = """你是一位資深台股研究員兼操盤助理，負責回答用戶對「今日報告」的問題。

回答規則：
1. **嚴格根據 context**：只引用提供的報告內容，不臆測未提供的數字
2. **承認不知道**：若 context 沒提到，明寫「報告未提供」而非編造
3. **簡潔**：對話風格，3-6 句、必要時用條列。不要寫長篇 essay
4. **誠實**：若 context 顯示防守模式或 BLOCKER，必須明說「今日不可操作」
5. **無越權**：不給「明日 X 元買進」具體價格決策；可給觀察條件
6. **中文回答**
7. 若用戶問某檔股票而 context 沒有 → 明白回「今日報告未涵蓋此股」
"""


def build_system_blocks(context: dict) -> list[dict]:
    context_str = json.dumps(context, ensure_ascii=False, indent=2, default=str)
    return [
        {"type": "text", "text": _BASE_SYSTEM},
        {
            "type": "text",
            "text": f"# 今日報告 context\n\n```json\n{context_str}\n```",
            "cache_control": {"type": "ephemeral"},
        },
    ]


def estimate_cost(model: str, usage) -> float:
    if usage is None:
        return 0.0
    in_tok = getattr(usage, "input_tokens", 0) or 0
    out_tok = getattr(usage, "output_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cache_create = getattr(usage, "cache_creation_input_tokens", 0) or 0
    if model.startswith("claude-opus"):
        in_p, out_p, cached_p = OPUS_IN, OPUS_OUT, OPUS_CACHED_IN
    else:
        in_p, out_p, cached_p = SONNET_IN, SONNET_OUT, SONNET_CACHED_IN
    normal_in = in_tok - cache_read
    cost = (
        max(0, normal_in) / 1e6 * in_p
        + cache_read / 1e6 * cached_p
        + out_tok / 1e6 * out_p
    )
    if cache_create > 0:
        cost += cache_create / 1e6 * in_p * 0.25
    return round(cost, 4)


def send_message(
    *,
    user_msg: str,
    context: dict,
    history: list[ChatMessage],
    forced_model: Optional[str] = None,
) -> ChatMessage:
    model = forced_model or pick_model(user_msg)

    try:
        import anthropic
    except ImportError:
        return ChatMessage(
            role="assistant", model=model,
            content="❌ anthropic SDK 未安裝。requirements.txt 已含 anthropic，請 reinstall。",
        )

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return ChatMessage(
            role="assistant", model=model,
            content=(
                "❌ 未設定 **ANTHROPIC_API_KEY**。\n\n"
                "請到 Streamlit Cloud → Settings → Secrets 加：\n"
                "```\nANTHROPIC_API_KEY = \"sk-ant-...\"\n```"
            ),
        )

    msgs: list[dict] = []
    for m in history:
        msgs.append({"role": m.role, "content": m.content})
    msgs.append({"role": "user", "content": user_msg})

    system_blocks = build_system_blocks(context)

    try:
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=model,
            max_tokens=1500,
            system=system_blocks,
            messages=msgs,
        )
        text = ""
        for block in resp.content:
            if hasattr(block, "text"):
                text += block.text
        cost = estimate_cost(model, getattr(resp, "usage", None))
        return ChatMessage(
            role="assistant", content=text.strip(),
            model=model, cost_usd=cost,
        )
    except Exception as e:
        return ChatMessage(
            role="assistant", model=model,
            content=f"❌ Claude 呼叫失敗：{e}",
        )
