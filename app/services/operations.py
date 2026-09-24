"""Backend-owned operational query services.

This module is used by REST/API services. The AI runtime reaches these
queries through `/api/v1/operations/context`, not by importing this module.
"""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

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


async def invalidate_operational_cache(
    *,
    branch_id: int | None = None,
    table_session_id: int | None = None,
) -> None:
    """Invalidate known read-model keys after a committed mutation."""
    keys: list[str] = []
    if table_session_id is not None:
        keys.extend(
            [
                f"table_session:{table_session_id}",
                f"active_orders:{branch_id}:{table_session_id}" if branch_id is not None else None,
            ]
        )
    if branch_id is not None:
        keys.extend(
            [
                f"station_queues:{branch_id}",
                f"bom_inventory:{branch_id}",
                f"branch_inventory:{branch_id}",
                f"active_orders:{branch_id}:None",
            ]
        )
    for key in (key for key in keys if key):
        await cache_client.delete(key)


async def get_table_session_state(db: AsyncSession, table_session_id: int) -> dict[str, Any]:
    cache_key = f"table_session:{table_session_id}"
    cached = await cache_client.get(cache_key)
    if cached is not None:
        return deepcopy(cached)
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
            "guest_session_id": guest.id,
            "display_name": guest.display_name,
            "status": guest.status,
            "otp_verified": guest.otp_verified,
            "dietary_preferences": guest.dietary_preferences,
        }
        for guest in session.guests
    ]
    data = {
        "found": True,
        "table_session_id": session.id,
        "branch_id": session.branch_id,
        "table_session_status": session.status,
        "table_id": session.table_id,
        "table_status": session.table.status if session.table else None,
        "table_code": session.table.code if session.table else None,
        "active_guest_count": len([guest for guest in session.guests if guest.status != "left"]),
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
        return deepcopy(cached)
    stmt = (
        select(Order)
        .join(TableSession, Order.table_session_id == TableSession.id)
        .options(selectinload(Order.items), selectinload(Order.payments))
        .where(
            TableSession.branch_id == branch_id,
            TableSession.status != "closed",
            Order.status.notin_(["cancelled", "served"]),
        )
    )
    if table_session_id is not None:
        stmt = stmt.where(Order.table_session_id == table_session_id)
    result = await db.execute(stmt)
    payload = []
    for order in result.scalars().unique().all():
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
                        "payment_id": payment.id,
                        "guest_session_id": payment.guest_session_id,
                        "amount": str(payment.amount),
                        "payment_status": payment.status,
                        "method": payment.method,
                    }
                    for payment in order.payments
                ],
            }
        )
    await cache_client.set(cache_key, payload, ttl_seconds=5)
    return payload


async def get_station_queues(db: AsyncSession, branch_id: int) -> dict[str, Any]:
    cache_key = f"station_queues:{branch_id}"
    cached = await cache_client.get(cache_key)
    if cached is not None:
        return deepcopy(cached)
    result = await db.execute(
        select(OrderItem, Order, TableSession)
        .join(Order, OrderItem.order_id == Order.id)
        .join(TableSession, Order.table_session_id == TableSession.id)
        .where(
            TableSession.branch_id == branch_id,
            TableSession.status != "closed",
            Order.status.notin_(["cancelled", "served"]),
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
        return deepcopy(cached)
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
    cache_key = f"branch_inventory:{branch_id}"
    cached = await cache_client.get(cache_key)
    if cached is not None:
        return deepcopy(cached)
    result = await db.execute(select(InventorySku).where(InventorySku.branch_id == branch_id))
    payload = [
        {
            "sku_id": sku.id,
            "sku_code": sku.sku_code,
            "name": sku.name,
            "on_hand": str(sku.on_hand),
            "par_level": str(sku.par_level),
            "unit": sku.unit,
        }
        for sku in result.scalars().all()
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
    return {"table_session_id": table_session_id, "splits": rows, "independent_settlement": True}


async def get_reservations(
    db: AsyncSession,
    branch_id: int,
    *,
    status: str | None = None,
    statuses: tuple[str, ...] | list[str] | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    stmt = select(Reservation).where(Reservation.branch_id == branch_id)
    if status is not None:
        stmt = stmt.where(Reservation.status == status)
    elif statuses is not None:
        stmt = stmt.where(Reservation.status.in_(list(statuses)))
    result = await db.execute(stmt.order_by(Reservation.start_at.asc()).limit(limit))
    return [
        {
            "reservation_id": reservation.id,
            "branch_id": reservation.branch_id,
            "table_id": reservation.table_id,
            "guest_name": reservation.guest_name,
            "party_size": reservation.party_size,
            "reservation_status": reservation.status,
            "start_at": reservation.start_at.isoformat() if reservation.start_at else None,
        }
        for reservation in result.scalars().all()
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
    return {
        "active_orders": request_context.get("active_orders") or orders,
        "station_queues": request_context.get("station_queues") or queues,
        "inventory_alerts": request_context.get("inventory_alerts") or inventory_alerts,
        "inventory_skus": request_context.get("inventory_skus") or inventory_skus or [],
        "live_table_session": table_state,
        "payment_splits": payment_splits,
        "reservations": reservations or [],
    }


def decimal_safe(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None
