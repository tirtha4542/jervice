"""Branch dashboard: aggregate reads across every table in one round trip."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.operations import check_recipe_bom_inventory, get_reservations, get_station_queues
from app.core.auth import ActorContext, get_actor_context, require_branch_access, require_permission
from app.core.database import get_db
from app.models import (
    AuditLog,
    Branch,
    Department,
    DiningTable,
    Employee,
    GuestSession,
    InventorySku,
    MenuItem,
    Order,
    OrderItem,
    Payment,
    Reservation,
    TableSession,
)

router = APIRouter(prefix="/api/v1", tags=["dashboard"])


def _count(value: Any) -> int:
    return int(value or 0)


def _money(value: Any) -> str:
    return str(Decimal(value or 0).quantize(Decimal("0.01")))


async def _group_counts(db: AsyncSession, column, where_clause) -> dict[str, int]:
    rows = (await db.execute(select(column, func.count()).where(where_clause).group_by(column))).all()
    return {str(status): _count(total) for status, total in rows}


@router.get("/dashboard/{branch_id}")
async def branch_dashboard(
    branch_id: int,
    hours: int = Query(default=24, ge=1, le=168, description="Look-back window"),
    db: AsyncSession = Depends(get_db),
    actor: ActorContext = Depends(get_actor_context),
) -> dict[str, Any]:
    """One payload for the whole branch: hierarchy, floor, kitchen, money, stock."""
    require_branch_access(actor, branch_id)
    require_permission(actor, "reports.read")
    branch = (
        await db.execute(
            select(Branch).where(Branch.id == branch_id)
        )
    ).scalar_one_or_none()
    if branch is None:
        raise HTTPException(status_code=404, detail=f"Branch {branch_id} not found")

    since = datetime.utcnow() - timedelta(hours=hours)

    # Floor
    table_statuses = await _group_counts(db, DiningTable.status, DiningTable.branch_id == branch_id)
    session_statuses = await _group_counts(
        db, TableSession.status, TableSession.branch_id == branch_id
    )
    open_sessions = (
        await db.execute(
            select(TableSession).where(
                TableSession.branch_id == branch_id,
                TableSession.status != "closed",
            )
        )
    ).scalars().all()
    open_session_ids = [s.id for s in open_sessions]

    active_guests = _count(
        await db.scalar(
            select(func.count(GuestSession.id)).where(
                GuestSession.table_session_id.in_(open_session_ids or [0]),
                GuestSession.status != "left",
            )
        )
    )

    # Orders & kitchen
    order_statuses = await _group_counts(
        db,
        Order.status,
        Order.table_session_id.in_(open_session_ids or [0]),
    )
    queues = await get_station_queues(db, branch_id)
    queue_totals = {station: len(tickets) for station, tickets in queues.items()}

    # Money
    paid_row = (
        await db.execute(
            select(func.coalesce(func.sum(Payment.amount), 0), func.count(Payment.id))
            .join(Order, Payment.order_id == Order.id)
            .join(TableSession, Order.table_session_id == TableSession.id)
            .where(
                TableSession.branch_id == branch_id,
                TableSession.opened_at >= since,
                Payment.status == "settled",
            )
        )
    ).one()
    open_row = (
        await db.execute(
            select(func.coalesce(func.sum(Payment.amount), 0), func.count(Payment.id))
            .join(Order, Payment.order_id == Order.id)
            .join(TableSession, Order.table_session_id == TableSession.id)
            .where(
                TableSession.branch_id == branch_id,
                TableSession.opened_at >= since,
                Payment.status.in_(["unpaid", "partial", "authorized"]),
            )
        )
    ).one()

    # Reservations
    reservation_statuses = await _group_counts(
        db, Reservation.status, Reservation.branch_id == branch_id
    )
    upcoming = await get_reservations(db, branch_id, statuses=("requested", "confirmed"), limit=10)

    # Inventory
    inventory_alerts = await check_recipe_bom_inventory(db, branch_id)
    low_stock = (
        await db.execute(
            select(func.count(InventorySku.id)).where(
                InventorySku.branch_id == branch_id, InventorySku.on_hand < InventorySku.par_level
            )
        )
    ).scalar_one()

    # Org chart & catalog
    employee_count = _count(
        await db.scalar(
            select(func.count(Employee.id))
            .join(Department, Employee.department_id == Department.id)
            .where(Department.branch_id == branch_id)
        )
    )
    menu_count = _count(
        await db.scalar(select(func.count(MenuItem.id)).where(MenuItem.branch_id == branch_id))
    )

    # Audit stream
    audit_rows = (
        await db.execute(
            select(AuditLog)
            .where(AuditLog.branch_id == branch_id, AuditLog.created_at >= since)
            .order_by(AuditLog.created_at.desc())
            .limit(20)
        )
    ).scalars().all()

    return {
        "branch": {"id": branch.id, "name": branch.name, "timezone": branch.timezone},
        "window_hours": hours,
        "floor": {
            "tables_total": sum(table_statuses.values()),
            "tables_by_status": table_statuses,
            "sessions_by_status": session_statuses,
            "open_sessions": len(open_session_ids),
            "active_guests": active_guests,
        },
        "kitchen": {
            "orders_by_status": order_statuses,
            "queues_by_station": queue_totals,
            "open_tickets": sum(queue_totals.values()),
        },
        "money": {
            "settled_total": _money(paid_row[0]),
            "settled_count": _count(paid_row[1]),
            "outstanding_total": _money(open_row[0]),
            "outstanding_count": _count(open_row[1]),
        },
        "reservations": {
            "by_status": reservation_statuses,
            "upcoming": upcoming,
        },
        "inventory": {
            "alerts": inventory_alerts,
            "low_stock_skus": _count(low_stock),
        },
        "org": {"employees": employee_count, "menu_items": menu_count},
        "audit": [
            {
                "id": row.id,
                "actor_role": row.actor_role,
                "event_type": row.event_type,
                "payload": row.payload,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in audit_rows
        ],
    }
