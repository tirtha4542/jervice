"""Orders, order items (station prep), and payments — three separate lifecycles."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.services.operations import (
    get_active_orders,
    get_station_queues,
    invalidate_operational_cache,
)
from app.core.audit import record_audit
from app.core.auth import (
    ActorContext,
    get_actor_context,
    require_branch_access,
    require_permission,
)
from app.core.database import get_db
from app.domain.state_machines import (
    InvalidStateTransition,
    OrderItemStatus,
    OrderStatus,
    PaymentStatus,
    TRANSITIONS,
    apply_transition,
)
from app.models import GuestSession, MenuItem, Order, OrderItem, Payment, TableSession

router = APIRouter(prefix="/api/v1", tags=["orders"])

OrderStatusValue = Literal["draft", "submitted", "in_kitchen", "ready", "served", "cancelled"]
OrderItemStatusValue = Literal["queued", "prepping", "fired", "ready", "picked_up", "voided"]
PaymentStatusValue = Literal["unpaid", "partial", "authorized", "settled", "failed", "refunded"]


def _allowed_from(enum_cls: type, current: str) -> list[str]:
    try:
        state = enum_cls(current)
    except ValueError:
        return []
    return sorted(s.value for s in TRANSITIONS[enum_cls].get(state, set()))


def _conflict(exc: Exception, enum_cls: type, current: str) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail=f"{exc} — allowed from '{current}': {_allowed_from(enum_cls, current)}",
    )


class OrderItemCreate(BaseModel):
    menu_item_id: int = Field(ge=1)
    quantity: int = Field(default=1, ge=1, le=99)
    modifiers: list[str] = Field(default_factory=list, max_length=20, examples=[["no onions"]])


class OrderCreate(BaseModel):
    table_session_id: int = Field(ge=1)
    guest_session_id: int | None = Field(
        default=None, description="Omit for a shared/table-wide order"
    )
    is_shared: bool = Field(default=False)
    items: list[OrderItemCreate] = Field(default_factory=list, max_length=100)


class OrderItemOut(BaseModel):
    id: int
    order_id: int
    menu_item_id: int
    menu_item_name: str | None = None
    quantity: int
    modifiers: list[str]
    status: OrderItemStatusValue
    station: str
    unit_price: Decimal | None = None


class OrderOut(BaseModel):
    id: int
    table_session_id: int
    guest_session_id: int | None
    is_shared: bool
    status: OrderStatusValue
    items: list[OrderItemOut] = []
    total: Decimal = Decimal("0.00")


class PaymentCreate(BaseModel):
    order_id: int = Field(ge=1)
    guest_session_id: int | None = None
    amount: Decimal = Field(gt=0, le=Decimal("100000000"), examples=["24.50"])
    method: Literal["card", "cash", "split", "wallet"] = "card"

    @field_validator("amount")
    @classmethod
    def amount_must_be_finite(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("amount must be finite")
        return value


class PaymentOut(BaseModel):
    id: int
    order_id: int
    guest_session_id: int | None
    amount: Decimal
    status: PaymentStatusValue
    method: str


class StatusUpdate(BaseModel):
    status: str


async def _load_order(db: AsyncSession, order_id: int) -> Order | None:
    return (
        await db.execute(
            select(Order)
            .options(selectinload(Order.items), selectinload(Order.payments))
            .where(Order.id == order_id)
        )
    ).scalar_one_or_none()


async def _order_for_actor(
    db: AsyncSession,
    order_id: int,
    actor: ActorContext,
    permission: str,
) -> Order:
    order = (
        await db.execute(
            select(Order)
            .options(selectinload(Order.items), selectinload(Order.payments))
            .where(Order.id == order_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=404, detail=f"Order {order_id} not found")
    session = await db.get(TableSession, order.table_session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Order session not found")
    require_branch_access(actor, session.branch_id)
    require_permission(actor, permission)
    return order


def _serialize_order(order: Order, prices: dict[int, tuple[str, Decimal]] | None = None) -> dict:
    prices = prices or {}
    items_out = []
    total = Decimal("0.00")
    for item in order.items:
        name, price = prices.get(item.menu_item_id, (None, None))
        if price is not None:
            total += price * item.quantity
        items_out.append(
            {
                "id": item.id,
                "order_id": item.order_id,
                "menu_item_id": item.menu_item_id,
                "menu_item_name": name,
                "quantity": item.quantity,
                "modifiers": item.modifiers,
                "status": item.status,
                "station": item.station,
                "unit_price": price,
            }
        )
    return {
        "id": order.id,
        "table_session_id": order.table_session_id,
        "guest_session_id": order.guest_session_id,
        "is_shared": order.is_shared,
        "status": order.status,
        "items": items_out,
        "total": total,
    }


async def _prices_for(db: AsyncSession, menu_ids: set[int]) -> dict[int, tuple[str, Decimal]]:
    if not menu_ids:
        return {}
    rows = (
        await db.execute(select(MenuItem).where(MenuItem.id.in_(menu_ids)))
    ).scalars().all()
    return {row.id: (row.name, row.price) for row in rows}


@router.get("/orders", response_model=list[OrderOut])
async def list_orders(
    branch_id: int = Query(ge=1),
    table_session_id: int | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    actor: ActorContext = Depends(get_actor_context),
) -> list[dict]:
    """Active orders for a branch — the same tool JARVIS reads."""
    require_branch_access(actor, branch_id)
    require_permission(actor, "orders.read")
    if table_session_id is not None:
        session = await db.get(TableSession, table_session_id)
        if session is None or session.branch_id != branch_id:
            raise HTTPException(status_code=404, detail="Table session not found in branch")
    payload = await get_active_orders(db, branch_id, table_session_id)
    if not payload:
        return []
    menu_ids = {i["menu_item_id"] for o in payload for i in o["items"]}
    prices = await _prices_for(db, menu_ids)
    orders = []
    for entry in payload:
        items = []
        total = Decimal("0.00")
        for raw in entry["items"]:
            name, price = prices.get(raw["menu_item_id"], (None, None))
            if price is not None:
                total += price * raw["quantity"]
            items.append(
                {
                    # JARVIS tool uses order_item_id / order_item_status keys;
                    # the REST response model wants id / order_id / status.
                    "id": raw["order_item_id"],
                    "order_id": entry["order_id"],
                    "menu_item_id": raw["menu_item_id"],
                    "menu_item_name": name,
                    "quantity": raw["quantity"],
                    "modifiers": raw["modifiers"],
                    "status": raw["order_item_status"],
                    "station": raw["station"],
                    "unit_price": price,
                }
            )
        orders.append(
            {
                "id": entry["order_id"],
                "table_session_id": entry["table_session_id"],
                "guest_session_id": entry["guest_session_id"],
                "is_shared": entry["is_shared"],
                "status": entry["order_status"],
                "items": items,
                "total": total,
            }
        )
    return orders


@router.post("/orders", response_model=OrderOut, status_code=201)
async def create_order(
    payload: OrderCreate,
    db: AsyncSession = Depends(get_db),
    actor: ActorContext = Depends(get_actor_context),
) -> dict:
    """Create a draft order with line items on an open table session."""
    session = await db.get(TableSession, payload.table_session_id)
    if session is None:
        raise HTTPException(
            status_code=400,
            detail=f"table_session_id {payload.table_session_id} does not exist.",
        )
    require_branch_access(actor, session.branch_id)
    require_permission(actor, "orders.create")
    if session.status == "closed":
        raise HTTPException(status_code=409, detail="Cannot create an order on a closed session")
    if payload.guest_session_id is not None:
        guest = await db.get(GuestSession, payload.guest_session_id)
        if guest is None or guest.table_session_id != payload.table_session_id:
            raise HTTPException(
                status_code=400,
                detail="guest_session_id must belong to this table_session.",
            )

    order = Order(
        table_session_id=payload.table_session_id,
        guest_session_id=payload.guest_session_id,
        is_shared=payload.is_shared,
        status=OrderStatus.DRAFT.value,
    )
    db.add(order)
    await db.flush()

    for line in payload.items:
        menu = await db.get(MenuItem, line.menu_item_id)
        if menu is None:
            raise HTTPException(
                status_code=400, detail=f"menu_item_id {line.menu_item_id} does not exist."
            )
        if menu.branch_id != session.branch_id:
            raise HTTPException(status_code=400, detail="Menu item is outside the order branch")
        db.add(
            OrderItem(
                order_id=order.id,
                menu_item_id=menu.id,
                quantity=line.quantity,
                modifiers=line.modifiers,
                status=OrderItemStatus.QUEUED.value,
                station=menu.station,
            )
        )

    await record_audit(
        db,
        actor_role=actor.role,
        event_type="order.created",
        payload={"order_id": order.id, "table_session_id": payload.table_session_id},
        branch_id=session.branch_id,
    )
    await db.commit()
    await invalidate_operational_cache(branch_id=session.branch_id, table_session_id=order.table_session_id)

    loaded = await _load_order(db, order.id)
    prices = await _prices_for(db, {i.menu_item_id for i in loaded.items})
    return _serialize_order(loaded, prices)


@router.get("/orders/{order_id}", response_model=OrderOut)
async def get_order(
    order_id: int,
    db: AsyncSession = Depends(get_db),
    actor: ActorContext = Depends(get_actor_context),
) -> dict:
    order = await _order_for_actor(db, order_id, actor, "orders.read")
    prices = await _prices_for(db, {i.menu_item_id for i in order.items})
    return _serialize_order(order, prices)


@router.post("/orders/{order_id}/items", response_model=OrderOut, status_code=201)
async def add_order_item(
    order_id: int,
    payload: OrderItemCreate,
    db: AsyncSession = Depends(get_db),
    actor: ActorContext = Depends(get_actor_context),
) -> dict:
    order = await _order_for_actor(db, order_id, actor, "orders.update")
    if order.status in (OrderStatus.CANCELLED.value, OrderStatus.SERVED.value):
        raise HTTPException(
            status_code=409,
            detail=f"Order {order_id} is {order.status}; items can no longer be added.",
        )
    menu = await db.get(MenuItem, payload.menu_item_id)
    if menu is None:
        raise HTTPException(
            status_code=400, detail=f"menu_item_id {payload.menu_item_id} does not exist."
        )
    session = await db.get(TableSession, order.table_session_id)
    if session is None or menu.branch_id != session.branch_id:
        raise HTTPException(status_code=400, detail="Menu item is outside the order branch")
    item = OrderItem(
        order_id=order.id,
        menu_item_id=menu.id,
        quantity=payload.quantity,
        modifiers=payload.modifiers,
        status=OrderItemStatus.QUEUED.value,
        station=menu.station,
    )
    db.add(item)
    await db.flush()
    await record_audit(
        db,
        actor_role=actor.role,
        event_type="order_item.added",
        payload={"order_id": order.id, "menu_item_id": menu.id, "quantity": payload.quantity},
        branch_id=session.branch_id,
    )
    await db.commit()
    await invalidate_operational_cache(branch_id=session.branch_id, table_session_id=order.table_session_id)
    loaded = await _load_order(db, order_id)
    prices = await _prices_for(db, {i.menu_item_id for i in loaded.items})
    return _serialize_order(loaded, prices)


@router.patch("/orders/{order_id}/status", response_model=OrderOut)
async def update_order_status(
    order_id: int,
    payload: StatusUpdate,
    db: AsyncSession = Depends(get_db),
    actor: ActorContext = Depends(get_actor_context),
) -> dict:
    """Advance the ticket lifecycle; illegal jumps return 409 with the allow-list."""
    order = await _order_for_actor(db, order_id, actor, "orders.update")
    try:
        target = OrderStatus(payload.status)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if target is OrderStatus.CANCELLED:
        require_permission(actor, "orders.cancel")
    try:
        apply_transition(OrderStatus, OrderStatus(order.status), target)
    except (InvalidStateTransition, ValueError) as exc:
        raise _conflict(exc, OrderStatus, order.status) from exc

    order.status = target.value
    session = await db.get(TableSession, order.table_session_id)
    await record_audit(
        db,
        actor_role=actor.role,
        event_type="order.status_changed",
        payload={"order_id": order.id, "status": order.status},
        branch_id=session.branch_id if session else None,
    )
    await db.commit()
    if session:
        await invalidate_operational_cache(branch_id=session.branch_id, table_session_id=order.table_session_id)
    loaded = await _load_order(db, order_id)
    prices = await _prices_for(db, {i.menu_item_id for i in loaded.items})
    return _serialize_order(loaded, prices)


@router.patch("/order-items/{item_id}/status", response_model=OrderItemOut)
async def update_order_item_status(
    item_id: int,
    payload: StatusUpdate,
    db: AsyncSession = Depends(get_db),
    actor: ActorContext = Depends(get_actor_context),
) -> dict:
    """Station-level prep: queued → prepping → fired → ready → picked_up."""
    item = await db.get(OrderItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"Order item {item_id} not found")
    order = await _order_for_actor(db, item.order_id, actor, "kitchen.queue.update")
    try:
        target = OrderItemStatus(payload.status)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    try:
        apply_transition(OrderItemStatus, OrderItemStatus(item.status), target)
    except (InvalidStateTransition, ValueError) as exc:
        raise _conflict(exc, OrderItemStatus, item.status) from exc

    item.status = target.value
    session = await db.get(TableSession, order.table_session_id)
    await record_audit(
        db,
        actor_role=actor.role,
        event_type="order_item.status_changed",
        payload={"order_item_id": item.id, "status": item.status},
        branch_id=session.branch_id if session else None,
    )
    await db.commit()
    if session:
        await invalidate_operational_cache(branch_id=session.branch_id, table_session_id=order.table_session_id)
    await db.refresh(item)
    menu = await db.get(MenuItem, item.menu_item_id)
    return {
        "id": item.id,
        "order_id": item.order_id,
        "menu_item_id": item.menu_item_id,
        "menu_item_name": menu.name if menu else None,
        "quantity": item.quantity,
        "modifiers": item.modifiers,
        "status": item.status,
        "station": item.station,
        "unit_price": menu.price if menu else None,
    }


@router.get("/kitchen/queues")
async def kitchen_queues(
    branch_id: int = Query(ge=1),
    db: AsyncSession = Depends(get_db),
    actor: ActorContext = Depends(get_actor_context),
) -> dict[str, Any]:
    """Live tickets grouped by station — the kitchen display payload."""
    require_branch_access(actor, branch_id)
    require_permission(actor, "kitchen.queue.read")
    return await get_station_queues(db, branch_id)


@router.post("/payments", response_model=PaymentOut, status_code=201)
async def create_payment(
    payload: PaymentCreate,
    db: AsyncSession = Depends(get_db),
    actor: ActorContext = Depends(get_actor_context),
) -> dict:
    """Post a payment against an order (start of a per-guest or shared settle)."""
    order = await _order_for_actor(db, payload.order_id, actor, "payments.create")
    if payload.guest_session_id is not None:
        guest = await db.get(GuestSession, payload.guest_session_id)
        if guest is None or guest.table_session_id != order.table_session_id:
            raise HTTPException(
                status_code=400,
                detail="guest_session_id must belong to the order's table session.",
            )
    session = await db.get(TableSession, order.table_session_id)
    if session is None or session.status == "closed":
        raise HTTPException(status_code=409, detail="Payment requires an open table session")
    prices = await _prices_for(db, {item.menu_item_id for item in order.items})
    order_total = sum(
        (prices.get(item.menu_item_id, (None, Decimal("0")))[1] or Decimal("0")) * item.quantity
        for item in order.items
    )
    already_recorded = sum(
        (
            payment.amount
            for payment in order.payments
            if payment.status not in {PaymentStatus.FAILED.value, PaymentStatus.REFUNDED.value}
        ),
        Decimal("0"),
    )
    outstanding = max(Decimal("0"), order_total - already_recorded)
    if payload.amount > outstanding:
        raise HTTPException(
            status_code=400,
            detail=f"Payment exceeds the outstanding order balance. Remaining: {outstanding:.2f}",
        )
    payment = Payment(
        order_id=order.id,
        guest_session_id=payload.guest_session_id,
        amount=payload.amount,
        status=PaymentStatus.UNPAID.value,
        method=payload.method,
    )
    db.add(payment)
    await db.flush()
    await record_audit(
        db,
        actor_role=actor.role,
        event_type="payment.created",
        payload={"payment_id": payment.id, "amount": str(payment.amount)},
        branch_id=session.branch_id,
    )
    await db.commit()
    await invalidate_operational_cache(branch_id=session.branch_id, table_session_id=order.table_session_id)
    await db.refresh(payment)
    return _serialize_payment(payment)


def _serialize_payment(payment: Payment) -> dict:
    return {
        "id": payment.id,
        "order_id": payment.order_id,
        "guest_session_id": payment.guest_session_id,
        "amount": payment.amount,
        "status": payment.status,
        "method": payment.method,
    }


@router.get("/payments", response_model=list[PaymentOut])
async def list_payments(
    table_session_id: int = Query(ge=1),
    db: AsyncSession = Depends(get_db),
    actor: ActorContext = Depends(get_actor_context),
) -> list[dict]:
    """All payments for one table session (split-bill view)."""
    session = await db.get(TableSession, table_session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Table session not found")
    require_branch_access(actor, session.branch_id)
    require_permission(actor, "payments.read")
    rows = (
        await db.execute(
            select(Payment)
            .join(Order, Payment.order_id == Order.id)
            .where(Order.table_session_id == table_session_id)
            .order_by(Payment.id.asc())
        )
    ).scalars().all()
    return [_serialize_payment(row) for row in rows]


@router.patch("/payments/{payment_id}/status", response_model=PaymentOut)
async def update_payment_status(
    payment_id: int,
    payload: StatusUpdate,
    db: AsyncSession = Depends(get_db),
    actor: ActorContext = Depends(get_actor_context),
) -> dict:
    """Settle (or refund) a payment; illegal jumps return 409."""
    payment = (
        await db.execute(
            select(Payment).where(Payment.id == payment_id).with_for_update()
        )
    ).scalar_one_or_none()
    if payment is None:
        raise HTTPException(status_code=404, detail=f"Payment {payment_id} not found")
    order = await _order_for_actor(db, payment.order_id, actor, "payments.create")
    try:
        target = PaymentStatus(payload.status)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if target is PaymentStatus.REFUNDED:
        require_permission(actor, "payments.refund")
    try:
        apply_transition(PaymentStatus, PaymentStatus(payment.status), target)
    except (InvalidStateTransition, ValueError) as exc:
        raise _conflict(exc, PaymentStatus, payment.status) from exc

    payment.status = target.value
    session = await db.get(TableSession, order.table_session_id)
    await record_audit(
        db,
        actor_role=actor.role,
        event_type=f"payment.{target.value}",
        payload={"payment_id": payment.id, "amount": str(payment.amount)},
        branch_id=session.branch_id if session else None,
    )
    await db.commit()
    if session:
        await invalidate_operational_cache(branch_id=session.branch_id, table_session_id=order.table_session_id)
    await db.refresh(payment)
    return _serialize_payment(payment)
