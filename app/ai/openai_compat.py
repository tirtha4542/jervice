"""Authenticated OpenAI-compatible facade for the local/cloud backend."""

from __future__ import annotations

import time
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, model_validator

from app.ai.agent import run_jarvis
from app.ai.backend_client import BackendRouteClient
from app.ai.router import build_operational_context
from app.ai.schemas import ContextPayload, JarvisExecuteRequest, JarvisExecuteResponse, JarvisRole
from app.core.auth import ActorContext, get_actor_context, get_raw_bearer_token
from app.core.config import settings
from app.core.rate_limit import allow_event

router = APIRouter(prefix="/v1", tags=["openai-compatible"])
DEFAULT_QUERY = "Analyze current operational state."


class ChatMessage(BaseModel):
    role: Literal["system", "developer", "user", "assistant", "tool"]
    content: str = Field(default="", max_length=settings.ai_max_query_length)


class ChatCompletionRequest(BaseModel):
    model: str | None = None
    messages: list[ChatMessage] = Field(min_length=1, max_length=50)
    temperature: float | None = Field(default=None, ge=0, le=2)

    # These fields remain for client compatibility, but the authenticated
    # actor is authoritative for role and branch.
    role: JarvisRole | None = None
    branch_id: int | None = Field(default=None, ge=1)
    table_session_id: int | None = Field(default=None, ge=1)
    context_payload: ContextPayload = Field(default_factory=ContextPayload)
    session_id: str | None = Field(default=None, min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")

    @model_validator(mode="after")
    def bound_total_content(self):
        total = sum(len(message.content) for message in self.messages)
        if total > 20_000:
            raise ValueError("combined message content is too large")
        return self


@router.get("/models")
async def list_models(actor: ActorContext = Depends(get_actor_context)) -> dict[str, Any]:
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
    actor: ActorContext = Depends(get_actor_context),
    access_token: str = Depends(get_raw_bearer_token),
) -> dict[str, Any]:
    role = payload.role or actor.role
    if actor.role != "owner" and role != actor.role:
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail="Requested role does not match authenticated role")
    if actor.role == "owner" and role not in {"owner", "manager"}:
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail="Owner may use owner or manager context")

    request = JarvisExecuteRequest(
        role=role,
        branch_id=actor.branch_id,
        table_session_id=payload.table_session_id,
        user_query=_user_query(payload),
        context_payload=payload.context_payload,
        session_id=payload.session_id,
    )
    if not allow_event(f"openai:{actor.user_id}:{actor.branch_id}", limit=30, window_seconds=60):
        from fastapi import HTTPException

        raise HTTPException(status_code=429, detail="Chat request limit exceeded; try again shortly")
    client = BackendRouteClient(access_token=access_token)
    operational_context = await build_operational_context(
        request,
        actor,
        access_token,
        client=client,
    )
    jarvis = await run_jarvis(request, operational_context, actor=actor)
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
                "session_id": jarvis.session_id,
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def _user_query(payload: ChatCompletionRequest) -> str:
    for message in reversed(payload.messages):
        if message.role == "user" and message.content.strip():
            return message.content.strip()
    for message in reversed(payload.messages):
        if message.content.strip():
            return message.content.strip()
    return DEFAULT_QUERY


def _render(result: JarvisExecuteResponse) -> str:
    lines = [result.summary.strip() or "No summary produced."]
    if result.recommendations:
        lines.extend(["", "Next-best-actions:"])
        for index, rec in enumerate(result.recommendations, start=1):
            lines.append(f"{index}. [{rec.priority}] {rec.title}")
            lines.append(f"   Action: {rec.action}")
            lines.append(f"   Why: {rec.rationale}")
    return "\n".join(lines)


def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)
