"""Backend-owned, read-only operational context route used by AI."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.operations import (
    check_recipe_bom_inventory,
    get_active_orders,
    get_branch_inventory,
    get_payment_split_view,
    get_reservations,
    get_station_queues,
    get_table_session_state,
)
from app.core.auth import ActorContext, get_actor_context, require_branch_access, require_permission
from app.core.database import get_db

router = APIRouter(prefix="/api/v1/operations", tags=["operations"])


class JarvisAuditRequest(BaseModel):
    branch_id: int = Field(ge=1)
    model: str = Field(default="", max_length=128)
    recommendations_count: int = Field(default=0, ge=0, le=100)


@router.get("/context")
async def operational_context(
    branch_id: int = Query(ge=1),
    table_session_id: int | None = Query(default=None, ge=1),
    actor: ActorContext = Depends(get_actor_context),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Return the minimum live context allowed for the current actor.

    The AI calls this route over HTTP. It is intentionally a backend route so
    authorization and data shaping stay on the backend side of the boundary.
    """
    require_branch_access(actor, branch_id)

    if table_session_id is not None:
        require_permission(actor, "session.read")
        from app.models import TableSession

        session_row = await db.get(TableSession, table_session_id)
        if session_row is None or session_row.branch_id != branch_id:
            raise HTTPException(status_code=404, detail="Table session not found in branch")
        state = await get_table_session_state(db, table_session_id)
        if not state.get("found"):
            raise HTTPException(status_code=404, detail="Table session not found")
    else:
        state = None

    orders: list[dict[str, Any]] = []
    if actor.has_permission("orders.read"):
        orders = await get_active_orders(db, branch_id, table_session_id)
        if not actor.has_permission("payments.read"):
            for order in orders:
                if isinstance(order, dict):
                    order["payments"] = []

    queues: dict[str, Any] = {}
    if actor.has_permission("kitchen.queue.read"):
        queues = await get_station_queues(db, branch_id)

    inventory_alerts: list[dict[str, Any]] = []
    inventory_skus: list[dict[str, Any]] = []
    if actor.has_permission("inventory.read"):
        inventory_alerts = await check_recipe_bom_inventory(db, branch_id)
        inventory_skus = await get_branch_inventory(db, branch_id)

    reservations: list[dict[str, Any]] = []
    if actor.has_permission("tables.read"):
        reservations = await get_reservations(
            db,
            branch_id,
            statuses=("requested", "confirmed"),
        )

    payment_splits = None
    if table_session_id is not None and actor.has_permission("payments.read"):
        payment_splits = await get_payment_split_view(db, table_session_id)

    return {
        "source": "backend_routes",
        "branch_id": branch_id,
        "table_session_id": table_session_id,
        "live_table_session": state,
        "active_orders": orders,
        "station_queues": queues,
        "inventory_alerts": inventory_alerts,
        "inventory_skus": inventory_skus,
        "reservations": reservations,
        "payment_splits": payment_splits,
    }


@router.post("/audit/jarvis")
async def record_jarvis_audit(
    payload: JarvisAuditRequest,
    actor: ActorContext = Depends(get_actor_context),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Persist a minimal AI audit event through the backend boundary."""
    from app.core.audit import record_audit

    branch_id = payload.branch_id
    require_branch_access(actor, branch_id)
    await record_audit(
        db,
        actor_role=actor.role,
        event_type="jarvis.orchestration",
        payload={
            "user_id": actor.user_id,
            "model": payload.model,
            "recommendations_count": payload.recommendations_count,
        },
        branch_id=branch_id,
    )
    await db.commit()
    return {"status": "recorded"}
