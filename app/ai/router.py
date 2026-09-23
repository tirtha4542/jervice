import asyncio
import json
import logging
from typing import Any, AsyncGenerator

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agent import run_jarvis
from app.ai.schemas import JarvisExecuteRequest, JarvisExecuteResponse, JarvisRole
from app.ai.tools import (
    check_recipe_bom_inventory,
    get_active_orders,
    get_payment_split_view,
    get_reservations,
    get_station_queues,
    get_table_session_state,
    merge_context,
)
from app.core.audit import record_audit
from app.core.auth import ActorContext, get_actor_context
from app.core.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/jarvis", tags=["jarvis"])


async def build_operational_context(db: AsyncSession, payload: JarvisExecuteRequest) -> dict[str, Any]:
    """Merge live database state with whatever the client already supplied.

    Gracefully degrades to empty context if DB is unpopulated or offline.
    """
    table_state = None
    payment_splits = None
    orders: list[dict[str, Any]] = []
    queues: dict[str, Any] = {}
    inventory_alerts: list[dict[str, Any]] = []
    reservations: list[dict[str, Any]] = []

    try:
        if payload.table_session_id is not None:
            table_state = await get_table_session_state(db, payload.table_session_id)
            payment_splits = await get_payment_split_view(db, payload.table_session_id)

        orders = await get_active_orders(db, payload.branch_id, payload.table_session_id)
        queues = await get_station_queues(db, payload.branch_id)
        inventory_alerts = await check_recipe_bom_inventory(db, payload.branch_id)
        reservations = await get_reservations(
            db, payload.branch_id, statuses=("requested", "confirmed")
        )
    except Exception as exc:
        logger.warning("Database context build notice (%s); using payload context.", exc)

    return merge_context(
        payload.context_payload.model_dump(),
        table_state=table_state,
        orders=orders,
        queues=queues,
        inventory_alerts=inventory_alerts,
        payment_splits=payment_splits,
        reservations=reservations,
    )


@router.post("/execute", response_model=JarvisExecuteResponse)
async def execute_jarvis(
    payload: JarvisExecuteRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    actor: ActorContext = Depends(get_actor_context),
) -> JarvisExecuteResponse:
    # Scope Injection: Force request branch to match ActorContext branch
    if actor.branch_id and payload.branch_id != actor.branch_id:
        payload.branch_id = actor.branch_id

    operational_context = await build_operational_context(db, payload)
    response = await run_jarvis(payload, operational_context, actor=actor)

    try:
        await record_audit(
            db,
            actor_role=actor.role,
            event_type="JARVIS_ORCHESTRATION_EXECUTE",
            payload={
                "user_id": actor.user_id,
                "branch_id": payload.branch_id,
                "recommendations_count": len(response.recommendations),
                "model": response.model,
            },
            branch_id=payload.branch_id,
        )
        await db.commit()
    except Exception as exc:
        logger.warning("Audit logging DB notice (%s); continuing response.", exc)

    return response


@router.post("/stream")
async def stream_jarvis(
    payload: JarvisExecuteRequest,
    db: AsyncSession = Depends(get_db),
    actor: ActorContext = Depends(get_actor_context),
) -> StreamingResponse:
    """Server-Sent Events (SSE) streaming endpoint for JARVIS tokens and operational steps."""
    if actor.branch_id and payload.branch_id != actor.branch_id:
        payload.branch_id = actor.branch_id

    operational_context = await build_operational_context(db, payload)

    async def event_generator() -> AsyncGenerator[str, None]:
        yield f"data: {json.dumps({'event': 'context_resolved', 'role': actor.role, 'branch_id': payload.branch_id})}\n\n"
        await asyncio.sleep(0.01)

        yield f"data: {json.dumps({'event': 'inferring', 'query': payload.user_query})}\n\n"
        await asyncio.sleep(0.01)

        response = await run_jarvis(payload, operational_context, actor=actor)

        words = response.summary.split(" ")
        for i in range(0, len(words), 3):
            chunk = " ".join(words[i : i + 3])
            yield f"data: {json.dumps({'event': 'summary_chunk', 'text': chunk + ' '})}\n\n"
            await asyncio.sleep(0.01)

        recs_data = [r.model_dump() for r in response.recommendations]
        yield f"data: {json.dumps({'event': 'recommendations', 'data': recs_data, 'model': response.model})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
