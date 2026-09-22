from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agent import run_jarvis
from app.ai.schemas import JarvisExecuteRequest, JarvisExecuteResponse
from app.ai.tools import (
    check_recipe_bom_inventory,
    get_active_orders,
    get_payment_split_view,
    get_reservations,
    get_station_queues,
    get_table_session_state,
    merge_context,
)
from app.core.database import get_db

router = APIRouter(prefix="/api/v1/jarvis", tags=["jarvis"])


async def build_operational_context(db: AsyncSession, payload: JarvisExecuteRequest) -> dict[str, Any]:
    """Merge live database state with whatever the client already supplied.

    Shared by the native route and the OpenAI-compatible facade so both produce
    the same operational context.
    """
    table_state = None
    payment_splits = None
    if payload.table_session_id is not None:
        table_state = await get_table_session_state(db, payload.table_session_id)
        payment_splits = await get_payment_split_view(db, payload.table_session_id)

    orders = await get_active_orders(db, payload.branch_id, payload.table_session_id)
    queues = await get_station_queues(db, payload.branch_id)
    inventory_alerts = await check_recipe_bom_inventory(db, payload.branch_id)
    reservations = await get_reservations(
        db, payload.branch_id, statuses=("requested", "confirmed")
    )

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
    db: AsyncSession = Depends(get_db),
) -> JarvisExecuteResponse:
    operational_context = await build_operational_context(db, payload)
    return await run_jarvis(payload, operational_context)
