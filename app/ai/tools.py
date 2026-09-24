"""Read-oriented operational tools. These query independently of UI occupancy flags."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.audit import record_audit
from app.core.redis import cache_client
from app.models import (
    DiningTable,
    GuestSession,
    InventorySku,
    Order,
    OrderItem,
    Payment,
    RecipeComponent,
    Reservation,
    TableSession,
)


async def get_table_session_state(db: AsyncSession, table_session_id: int) -> dict[str, Any]:
    cache_key = f"table_session:{table_session_id}"
    cached = await cache_client.get(cache_key)
    if cached is not None:
        return cached

    result = await db.execute(
        select(TableSession)
        .options(
            selectinload(TableSession.table),
            selectinload(TableSession.guests),
            selectinload(TableSession.orders),
        )
        .where(TableSession.id == table_session_id)
    )
    session = result.scalar_one_or_none()
    if session is None:
        res = {"found": False, "table_session_id": table_session_id}
        await cache_client.set(cache_key, res, ttl_seconds=10)
        return res

    guests = [
        {
            "guest_session_id": g.id,
            "display_name": g.display_name,
            "status": g.status,
            "otp_verified": g.otp_verified,
            "dietary_preferences": g.dietary_preferences,
        }
        for g in session.guests
    ]
    data = {
        "found": True,
        "table_session_id": session.id,
        "table_session_status": session.status,
        "table_id": session.table_id,
        "table_status": session.table.status if session.table else None,
        "table_code": session.table.code if session.table else None,
        "active_guest_count": len([g for g in session.guests if g.status != "left"]),
        "guests": guests,
        "multi_guest_join_allowed": True,
        "note": "Additional QR joins attach to this table session; they do not occupy the table twice.",
    }
    await cache_client.set(cache_key, data, ttl_seconds=10)
    return data


async def get_active_orders(db: AsyncSession, branch_id: int, table_session_id: int | None) -> list[dict[str, Any]]:
    cache_key = f"active_orders:{branch_id}:{table_session_id}"
    cached = await cache_client.get(cache_key)
    if cached is not None:
        return cached

    stmt = (
        select(Order)
        .join(TableSession, Order.table_session_id == TableSession.id)
        .options(selectinload(Order.items), selectinload(Order.payments))
        .where(TableSession.branch_id == branch_id, Order.status.notin_(["cancelled"]))
    )
    if table_session_id is not None:
        stmt = stmt.where(Order.table_session_id == table_session_id)
    result = await db.execute(stmt)
    orders = result.scalars().unique().all()
    payload = []
    for order in orders:
        payload.append(
            {
                "order_id": order.id,
                "table_session_id": order.table_session_id,
                "guest_session_id": order.guest_session_id,
                "is_shared": order.is_shared,
                "order_status": order.status,
                "items": [
                    {
                        "order_item_id": item.id,
                        "menu_item_id": item.menu_item_id,
                        "quantity": item.quantity,
                        "modifiers": item.modifiers,
                        "order_item_status": item.status,
                        "station": item.station,
                    }
                    for item in order.items
                ],
                "payments": [
                    {
                        "payment_id": p.id,
                        "guest_session_id": p.guest_session_id,
                        "amount": str(p.amount),
                        "payment_status": p.status,
                        "method": p.method,
                    }
                    for p in order.payments
                ],
            }
        )
    await cache_client.set(cache_key, payload, ttl_seconds=5)
    return payload


async def get_station_queues(db: AsyncSession, branch_id: int) -> dict[str, Any]:
    cache_key = f"station_queues:{branch_id}"
    cached = await cache_client.get(cache_key)
    if cached is not None:
        return cached

    result = await db.execute(
        select(OrderItem, Order, TableSession)
        .join(Order, OrderItem.order_id == Order.id)
        .join(TableSession, Order.table_session_id == TableSession.id)
        .where(
            TableSession.branch_id == branch_id,
            OrderItem.status.in_(["queued", "prepping", "fired", "ready"]),
        )
    )
    queues: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item, order, session in result.all():
        queues[item.station].append(
            {
                "order_item_id": item.id,
                "order_id": order.id,
                "table_session_id": session.id,
                "order_item_status": item.status,
                "quantity": item.quantity,
            }
        )
    data = {station: tickets for station, tickets in queues.items()}
    await cache_client.set(cache_key, data, ttl_seconds=5)
    return data


async def check_recipe_bom_inventory(db: AsyncSession, branch_id: int) -> list[dict[str, Any]]:
    cache_key = f"bom_inventory:{branch_id}"
    cached = await cache_client.get(cache_key)
    if cached is not None:
        return cached

    result = await db.execute(
        select(RecipeComponent, InventorySku)
        .join(InventorySku, RecipeComponent.sku_id == InventorySku.id)
        .where(InventorySku.branch_id == branch_id)
    )
    alerts: list[dict[str, Any]] = []
    for component, sku in result.all():
        short = sku.on_hand < sku.par_level
        insufficient_for_one = sku.on_hand < component.quantity
        if short or insufficient_for_one:
            alerts.append(
                {
                    "sku_id": sku.id,
                    "sku_code": sku.sku_code,
                    "name": sku.name,
                    "on_hand": str(sku.on_hand),
                    "par_level": str(sku.par_level),
                    "unit": sku.unit,
                    "menu_item_id": component.menu_item_id,
                    "bom_qty_per_portion": str(component.quantity),
                    "severity": "critical" if insufficient_for_one else "warning",
                }
            )
    await cache_client.set(cache_key, alerts, ttl_seconds=15)
    return alerts


async def get_branch_inventory(db: AsyncSession, branch_id: int) -> list[dict[str, Any]]:
    """Retrieve all inventory SKUs and on-hand quantities for a branch."""
    cache_key = f"branch_inventory:{branch_id}"
    cached = await cache_client.get(cache_key)
    if cached is not None:
        return cached

    result = await db.execute(
        select(InventorySku).where(InventorySku.branch_id == branch_id)
    )
    skus = result.scalars().all()
    payload = [
        {
            "sku_id": s.id,
            "sku_code": s.sku_code,
            "name": s.name,
            "on_hand": str(s.on_hand),
            "par_level": str(s.par_level),
            "unit": s.unit,
        }
        for s in skus
    ]
    await cache_client.set(cache_key, payload, ttl_seconds=15)
    return payload


async def get_payment_split_view(db: AsyncSession, table_session_id: int) -> dict[str, Any]:
    result = await db.execute(
        select(Payment, Order, GuestSession)
        .join(Order, Payment.order_id == Order.id)
        .outerjoin(GuestSession, Payment.guest_session_id == GuestSession.id)
        .where(Order.table_session_id == table_session_id)
    )
    rows = []
    for payment, order, guest in result.all():
        rows.append(
            {
                "payment_id": payment.id,
                "order_id": order.id,
                "order_status": order.status,
                "payment_status": payment.status,
                "guest_session_id": payment.guest_session_id,
                "guest_name": guest.display_name if guest else None,
                "amount": str(payment.amount),
            }
        )
    return {
        "table_session_id": table_session_id,
        "splits": rows,
        "independent_settlement": True,
    }


async def get_reservations(
    db: AsyncSession,
    branch_id: int,
    *,
    status: str | None = None,
    statuses: tuple[str, ...] | list[str] | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Reservations for a branch, oldest first."""
    stmt = select(Reservation).where(Reservation.branch_id == branch_id)
    if status is not None:
        stmt = stmt.where(Reservation.status == status)
    elif statuses is not None:
        stmt = stmt.where(Reservation.status.in_(list(statuses)))
    result = await db.execute(stmt.order_by(Reservation.start_at.asc()).limit(limit))
    return [
        {
            "reservation_id": r.id,
            "branch_id": r.branch_id,
            "table_id": r.table_id,
            "guest_name": r.guest_name,
            "party_size": r.party_size,
            "reservation_status": r.status,
            "start_at": r.start_at.isoformat() if r.start_at else None,
        }
        for r in result.scalars().all()
    ]


def merge_context(
    request_context: dict[str, Any],
    *,
    table_state: dict[str, Any] | None,
    orders: list[dict[str, Any]],
    queues: dict[str, Any],
    inventory_alerts: list[dict[str, Any]],
    inventory_skus: list[dict[str, Any]] | None = None,
    payment_splits: dict[str, Any] | None,
    reservations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    merged = {
        "active_orders": request_context.get("active_orders") or orders,
        "station_queues": request_context.get("station_queues") or queues,
        "inventory_alerts": request_context.get("inventory_alerts") or inventory_alerts,
        "inventory_skus": request_context.get("inventory_skus") or inventory_skus or [],
        "live_table_session": table_state,
        "payment_splits": payment_splits,
        "reservations": reservations or [],
    }
    if request_context.get("active_orders") and orders:
        merged["active_orders"] = orders
    return merged


def decimal_safe(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return str(value)
