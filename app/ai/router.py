import asyncio
import copy
import json
import logging
from typing import Any, AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from app.ai.agent import run_jarvis
from app.ai.backend_client import BackendRouteClient, BackendRouteError
from app.ai.memory import (
    assert_session_access,
    clear_conversation_history,
    get_conversation_history,
)
from app.ai.schemas import (
    JarvisExecuteRequest,
    JarvisExecuteResponse,
    JarvisRole,
    MemoryHistoryResponse,
)
from app.core.auth import ActorContext, get_actor_context, get_raw_bearer_token
from app.core.config import settings
from app.core.rate_limit import allow_event

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/jarvis", tags=["jarvis"])


def _role_for_actor(actor: ActorContext, requested: JarvisRole) -> JarvisRole:
    if actor.role == "owner":
        return "owner" if requested == "owner" else "manager"
    if requested != actor.role:
        raise HTTPException(status_code=403, detail="Requested role does not match authenticated role")
    return requested


def _empty_context(branch_id: int, table_session_id: int | None, reason: str) -> dict[str, Any]:
    return {
        "source": "unavailable",
        "degraded": True,
        "degraded_reason": reason,
        "branch_id": branch_id,
        "table_session_id": table_session_id,
        "live_table_session": None,
        "active_orders": [],
        "station_queues": {},
        "inventory_alerts": [],
        "inventory_skus": [],
        "reservations": [],
        "payment_splits": None,
    }


async def build_operational_context(
    payload: JarvisExecuteRequest,
    actor: ActorContext,
    access_token: str,
    *,
    client: BackendRouteClient | None = None,
) -> dict[str, Any]:
    """Fetch live context exclusively through the backend operations route."""
    payload.branch_id = actor.branch_id
    payload.role = _role_for_actor(actor, payload.role)
    route_client = client or BackendRouteClient(access_token=access_token)

    try:
        context = await route_client.operational_context(
            branch_id=actor.branch_id,
            table_session_id=payload.table_session_id,
        )
    except BackendRouteError as exc:
        if settings.allow_test_context:
            fixture = copy.deepcopy(payload.context_payload.model_dump())
            context = {
                **_empty_context(actor.branch_id, payload.table_session_id, "test_fixture"),
                **fixture,
                "source": "test_fixture",
                "degraded": False,
            }
        else:
            logger.warning("Backend context unavailable: %s", exc.__class__.__name__)
            context = _empty_context(
                actor.branch_id,
                payload.table_session_id,
                "backend route unavailable; no client context accepted",
            )

    # The backend already applies permissions. This second small boundary keeps
    # accidental payment fields out of a low-privilege AI response.
    if not actor.has_permission("payments.read"):
        context.pop("payment_splits", None)
        for order in context.get("active_orders") or []:
            if isinstance(order, dict):
                order["payments"] = []
    if not actor.has_permission("inventory.read"):
        context.pop("inventory_alerts", None)
        context.pop("inventory_skus", None)
    if not actor.has_permission("tables.read"):
        context.pop("reservations", None)
    if not actor.has_permission("orders.read"):
        context["active_orders"] = []
    if not actor.has_permission("kitchen.queue.read"):
        context["station_queues"] = {}
    return context


@router.post("/execute", response_model=JarvisExecuteResponse)
async def execute_jarvis(
    payload: JarvisExecuteRequest,
    actor: ActorContext = Depends(get_actor_context),
    access_token: str = Depends(get_raw_bearer_token),
) -> JarvisExecuteResponse:
    payload.role = _role_for_actor(actor, payload.role)
    payload.branch_id = actor.branch_id
    if not allow_event(f"ai:{actor.user_id}:{actor.branch_id}", limit=30, window_seconds=60):
        raise HTTPException(status_code=429, detail="AI request limit exceeded; try again shortly")
    client = BackendRouteClient(access_token=access_token)
    operational_context = await build_operational_context(
        payload,
        actor,
        access_token,
        client=client,
    )
    response = await run_jarvis(payload, operational_context, actor=actor)

    try:
        await client.post_json(
            "/api/v1/operations/audit/jarvis",
            {
                "branch_id": actor.branch_id,
                "model": response.model,
                "recommendations_count": len(response.recommendations),
            },
        )
    except BackendRouteError as exc:
        # AI remains read-only and available in local development if audit
        # routing is temporarily unavailable; the log identifies the failure.
        logger.warning("AI audit route unavailable: %s", exc.__class__.__name__)
    return response


@router.post("/stream")
async def stream_jarvis(
    payload: JarvisExecuteRequest,
    actor: ActorContext = Depends(get_actor_context),
    access_token: str = Depends(get_raw_bearer_token),
) -> StreamingResponse:
    payload.role = _role_for_actor(actor, payload.role)
    payload.branch_id = actor.branch_id
    if not allow_event(f"ai:{actor.user_id}:{actor.branch_id}", limit=30, window_seconds=60):
        raise HTTPException(status_code=429, detail="AI request limit exceeded; try again shortly")
    client = BackendRouteClient(access_token=access_token)
    operational_context = await build_operational_context(
        payload,
        actor,
        access_token,
        client=client,
    )

    async def event_generator() -> AsyncGenerator[str, None]:
        yield f"data: {json.dumps({'event': 'context_resolved', 'role': actor.role, 'branch_id': actor.branch_id})}\n\n"
        await asyncio.sleep(0.01)
        yield f"data: {json.dumps({'event': 'inferring', 'query': payload.user_query})}\n\n"
        response = await run_jarvis(payload, operational_context, actor=actor)
        words = response.summary.split(" ")
        for index in range(0, len(words), 3):
            chunk = " ".join(words[index : index + 3])
            yield f"data: {json.dumps({'event': 'summary_chunk', 'text': chunk + ' '})}\n\n"
            await asyncio.sleep(0.01)
        recs_data = [item.model_dump() for item in response.recommendations]
        yield f"data: {json.dumps({'event': 'recommendations', 'data': recs_data, 'model': response.model, 'session_id': response.session_id})}\n\n"
        try:
            await client.post_json(
                "/api/v1/operations/audit/jarvis",
                {
                    "branch_id": actor.branch_id,
                    "model": response.model,
                    "recommendations_count": len(response.recommendations),
                },
            )
        except BackendRouteError as exc:
            logger.warning("AI stream audit route unavailable: %s", exc.__class__.__name__)
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/history/{session_id}", response_model=MemoryHistoryResponse)
async def get_session_history(
    session_id: str,
    actor: ActorContext = Depends(get_actor_context),
) -> MemoryHistoryResponse:
    try:
        assert_session_access(session_id, actor)
    except PermissionError as exc:
        raise HTTPException(status_code=404, detail="Conversation session not found") from exc
    entries = get_conversation_history(session_id)
    return MemoryHistoryResponse(
        session_id=session_id,
        total_turns=len(entries),
        history=entries,
    )


@router.delete("/history/{session_id}", status_code=status.HTTP_200_OK)
async def clear_session_history(
    session_id: str,
    actor: ActorContext = Depends(get_actor_context),
) -> dict[str, object]:
    try:
        assert_session_access(session_id, actor)
    except PermissionError as exc:
        raise HTTPException(status_code=404, detail="Conversation session not found") from exc
    deleted = clear_conversation_history(session_id)
    return {
        "session_id": session_id,
        "cleared_checkpoints": deleted,
        "message": f"Cleared {deleted} local checkpoint(s).",
    }
