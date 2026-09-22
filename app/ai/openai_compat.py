"""OpenAI-compatible facade so third-party chat clients can drive JARVIS.

Implements the two endpoints every OpenAI-style client probes first:

- ``GET  /v1/models``
- ``POST /v1/chat/completions``

JARVIS-specific inputs (``role``, ``branch_id``, ``table_session_id``,
``context_payload``) are accepted as optional extra fields. Plain OpenAI
clients omit them and receive manager-level defaults.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agent import run_jarvis
from app.ai.router import build_operational_context
from app.ai.schemas import ContextPayload, JarvisExecuteRequest, JarvisExecuteResponse, JarvisRole
from app.core.config import settings
from app.core.database import get_db

router = APIRouter(prefix="/v1", tags=["openai-compatible"])

DEFAULT_QUERY = "Analyze current operational state."


class ChatMessage(BaseModel):
    role: Literal["system", "developer", "user", "assistant", "tool"]
    content: str = ""


class ChatCompletionRequest(BaseModel):
    model: str | None = None
    messages: list[ChatMessage] = Field(min_length=1)
    temperature: float | None = None

    # JARVIS extensions — optional, so stock OpenAI clients still work.
    role: JarvisRole = "manager"
    branch_id: int = 1
    table_session_id: int | None = None
    context_payload: ContextPayload = Field(default_factory=ContextPayload)


@router.get("/models")
async def list_models() -> dict[str, Any]:
    """Advertise the configured model so client probes stop returning 404."""
    return {
        "object": "list",
        "data": [
            {
                "id": settings.groq_model,
                "object": "model",
                "created": 0,
                "owned_by": "groq",
            }
        ],
    }


@router.post("/chat/completions")
async def chat_completions(
    payload: ChatCompletionRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    request = JarvisExecuteRequest(
        role=payload.role,
        branch_id=payload.branch_id,
        table_session_id=payload.table_session_id,
        user_query=_user_query(payload),
        context_payload=payload.context_payload,
    )
    operational_context = await build_operational_context(db, request)
    jarvis = await run_jarvis(request, operational_context)
    content = _render(jarvis)

    prompt_tokens = _approx_tokens(request.user_query)
    completion_tokens = _approx_tokens(content)
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": jarvis.model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def _user_query(payload: ChatCompletionRequest) -> str:
    """Last user message wins; fall back to the last message, then a default."""
    for message in reversed(payload.messages):
        if message.role == "user" and message.content.strip():
            return message.content.strip()
    for message in reversed(payload.messages):
        if message.content.strip():
            return message.content.strip()
    return DEFAULT_QUERY


def _render(result: JarvisExecuteResponse) -> str:
    """Chat clients display plain text, so format the briefing for reading."""
    lines = [result.summary.strip() or "No summary produced."]
    if result.recommendations:
        lines.append("")
        lines.append("Next-best-actions:")
        for index, rec in enumerate(result.recommendations, start=1):
            lines.append(f"{index}. [{rec.priority}] {rec.title}")
            lines.append(f"   Action: {rec.action}")
            lines.append(f"   Why: {rec.rationale}")
    return "\n".join(lines)


def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)
