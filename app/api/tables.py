"""Dining tables, table sessions, and guest sessions.

These are three *independent* state machines. This router never derives one
from another's occupancy flag — it validates each transition explicitly.
"""

from __future__ import annotations

from datetime import datetime
import secrets
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.services.operations import (
    get_payment_split_view,
    get_table_session_state,
    invalidate_operational_cache,
)
from app.core.audit import record_audit
from app.core.auth import (
    ActorContext,
    get_actor_context,
    require_branch_access,
    require_permission,
)
from app.core.config import settings
from app.core.database import get_db
from app.domain.state_machines import (
    GuestSessionStatus,
    InvalidStateTransition,
    TableSessionStatus,
    TableStatus,
    TRANSITIONS,
    apply_transition,
)
from app.models import Branch, DiningTable, GuestSession, Order, Payment, TableSession

router = APIRouter(prefix="/api/v1", tags=["tables"])

TableStatusValue = Literal["available", "reserved", "seated", "dirty", "blocked"]
TableSessionStatusValue = Literal["open", "ordering", "dining", "settling", "closed"]
GuestSessionStatusValue = Literal[
    "authenticating", "joined", "ordering", "dining", "settling", "left"
]


class DiningTableOut(BaseModel):
    id: int
    branch_id: int
    code: str
    qr_token: str
    status: TableStatusValue
    capacity: int
    active_session_id: int | None = None


class DiningTableCreate(BaseModel):
    branch_id: int = Field(ge=1)
    code: str = Field(min_length=1, max_length=32, examples=["T4"])
    capacity: int = Field(default=4, ge=1, le=50)


class TableStatusUpdate(BaseModel):
    status: TableStatusValue


class TableSessionOut(BaseModel):
    id: int
    table_id: int
    branch_id: int
    status: TableSessionStatusValue
    opened_at: datetime
    closed_at: datetime | None


class GuestJoinRequest(BaseModel):
    display_name: str = Field(default="Guest", min_length=1, max_length=128, examples=["Nadia"])
    phone: str | None = Field(default=None, max_length=32)
    otp: str = Field(min_length=4, max_length=8, description="OTP shown on the table QR card")
    qr_token: str | None = Field(default=None, min_length=8, max_length=256)
    test_qr_verified: bool = Field(
        default=False,
        description="Local-only QR checkbox; rejected unless ALLOW_TEST_QR=true",
    )
    dietary_preferences: list[str] = Field(default_factory=list, max_length=20)


class GuestSessionOut(BaseModel):
    id: int
    table_session_id: int
    display_name: str
    phone: str | None
    otp_verified: bool
    status: GuestSessionStatusValue
    dietary_preferences: list[str]


class StatusUpdate(BaseModel):
    status: str


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


async def _require_branch(db: AsyncSession, branch_id: int) -> None:
    if await db.scalar(select(Branch.id).where(Branch.id == branch_id)) is None:
        raise HTTPException(
            status_code=400,
            detail=f"branch_id {branch_id} does not exist. Run POST /api/v1/demo/seed first.",
        )


async def _table_for_actor(
    db: AsyncSession,
    table_id: int,
    actor: ActorContext,
    permission: str,
) -> DiningTable:
    table = (
        await db.execute(
            select(DiningTable).where(DiningTable.id == table_id).with_for_update()
        )
    ).scalar_one_or_none()
    if table is None:
        raise HTTPException(status_code=404, detail=f"Table {table_id} not found")
    require_branch_access(actor, table.branch_id)
    require_permission(actor, permission)
    return table


async def _session_for_actor(
    db: AsyncSession,
    table_session_id: int,
    actor: ActorContext,
    permission: str = "session.read",
) -> TableSession:
    session = await db.get(TableSession, table_session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Table session {table_session_id} not found")
    require_branch_access(actor, session.branch_id)
    require_permission(actor, permission)
    return session


async def _verify_qr(
    db: AsyncSession,
    session: TableSession,
    payload: GuestJoinRequest,
) -> str:
    """Verify the local checkbox or a real QR token.

    The checkbox is intentionally gated by an explicit development setting.
    A production deployment must use a signed QR/OTP provider instead.
    """
    if payload.test_qr_verified:
        if not settings.allow_test_qr:
            raise HTTPException(
                status_code=403,
                detail="The local QR checkbox is disabled",
            )
        return "test_checkbox"
    if payload.qr_token:
        if not settings.allow_real_qr:
            raise HTTPException(status_code=403, detail="Real QR verification is not enabled")
        table = (
            await db.execute(
                select(DiningTable).where(DiningTable.qr_token == payload.qr_token)
            )
        ).scalar_one_or_none()
        if table is None or table.id != session.table_id:
            raise HTTPException(status_code=403, detail="QR token does not match this table")
        return "qr_token"
    raise HTTPException(
        status_code=403,
        detail="A valid QR token or the local QR test checkbox is required",
    )


@router.get("/tables", response_model=list[DiningTableOut])
async def list_tables(
    branch_id: int = Query(ge=1),
    status: TableStatusValue | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    actor: ActorContext = Depends(get_actor_context),
) -> list[dict]:
    """Physical tables for a branch, annotated with their active session id."""
    require_branch_access(actor, branch_id)
    require_permission(actor, "tables.read")
    stmt = (
        select(DiningTable)
        .options(selectinload(DiningTable.sessions))
        .where(DiningTable.branch_id == branch_id)
    )
    if status:
        stmt = stmt.where(DiningTable.status == status)
    rows = (await db.execute(stmt.order_by(DiningTable.code.asc()))).scalars().unique().all()
    active = {t.id: None for t in rows}
    for table in rows:
        for session in table.sessions:
            if session.status != TableSessionStatus.CLOSED.value:
                active[table.id] = session.id
                break
    return [
        {
            "id": t.id,
            "branch_id": t.branch_id,
            "code": t.code,
            "qr_token": t.qr_token,
            "status": t.status,
            "capacity": t.capacity,
            "active_session_id": active[t.id],
        }
        for t in rows
    ]


@router.post("/tables", response_model=DiningTableOut, status_code=201)
async def create_table(
    payload: DiningTableCreate,
    db: AsyncSession = Depends(get_db),
    actor: ActorContext = Depends(get_actor_context),
) -> dict:
    require_branch_access(actor, payload.branch_id)
    require_permission(actor, "tables.manage")
    await _require_branch(db, payload.branch_id)
    duplicate = (
        await db.execute(
            select(DiningTable).where(
                DiningTable.branch_id == payload.branch_id, DiningTable.code == payload.code
            )
        )
    ).scalar_one_or_none()
    if duplicate is not None:
        raise HTTPException(
            status_code=409, detail=f"Table code '{payload.code}' already exists on this branch."
        )
    table = DiningTable(
        branch_id=payload.branch_id,
        code=payload.code,
        qr_token=f"qr_{secrets.token_urlsafe(24)}",
        status=TableStatus.AVAILABLE.value,
        capacity=payload.capacity,
    )
    db.add(table)
    await db.flush()
    await record_audit(
        db,
        actor_role=actor.role,
        event_type="table.created",
        payload={"table_id": table.id, "code": table.code},
        branch_id=table.branch_id,
    )
    await db.commit()
    await invalidate_operational_cache(branch_id=table.branch_id)
    await db.refresh(table)
    return {
        "id": table.id,
        "branch_id": table.branch_id,
        "code": table.code,
        "qr_token": table.qr_token,
        "status": table.status,
        "capacity": table.capacity,
        "active_session_id": None,
    }


@router.patch("/tables/{table_id}/status", response_model=DiningTableOut)
async def update_table_status(
    table_id: int,
    payload: TableStatusUpdate,
    db: AsyncSession = Depends(get_db),
    actor: ActorContext = Depends(get_actor_context),
) -> dict:
    """Move the *physical* table through its lifecycle (available → seated → dirty…)."""
    table = await _table_for_actor(db, table_id, actor, "tables.update")
    if payload.status == TableStatus.AVAILABLE.value:
        active = await db.scalar(
            select(TableSession.id).where(
                TableSession.table_id == table_id,
                TableSession.status != TableSessionStatus.CLOSED.value,
            )
        )
        if active is not None:
            raise HTTPException(status_code=409, detail="Close the active service session first")
    try:
        apply_transition(TableStatus, TableStatus(table.status), TableStatus(payload.status))
    except (InvalidStateTransition, ValueError) as exc:
        raise _conflict(exc, TableStatus, table.status) from exc
    table.status = payload.status
    await record_audit(
        db,
        actor_role=actor.role,
        event_type="table.status_changed",
        payload={"table_id": table.id, "status": table.status},
        branch_id=table.branch_id,
    )
    await db.commit()
    await invalidate_operational_cache(branch_id=table.branch_id)
    await db.refresh(table)
    return {
        "id": table.id,
        "branch_id": table.branch_id,
        "code": table.code,
        "qr_token": table.qr_token,
        "status": table.status,
        "capacity": table.capacity,
        "active_session_id": None,
    }


@router.post("/tables/{table_id}/open-session", response_model=TableSessionOut, status_code=201)
async def open_table_session(
    table_id: int,
    db: AsyncSession = Depends(get_db),
    actor: ActorContext = Depends(get_actor_context),
) -> dict:
    """Seat a party: opens a table session and marks the table seated.

    Multiple guests then join this *one* session via QR — see
    ``POST /api/v1/table-sessions/{id}/guests``.
    """
    table = await _table_for_actor(db, table_id, actor, "tables.update")

    existing = (
        await db.execute(
            select(TableSession).where(
                TableSession.table_id == table_id,
                TableSession.status != TableSessionStatus.CLOSED.value,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Table {table.code} already has open session {existing.id} "
            "— additional guests should join it, not open a new one.",
        )

    session = TableSession(
        table_id=table_id,
        branch_id=table.branch_id,
        status=TableSessionStatus.OPEN.value,
        opened_at=datetime.utcnow(),
    )
    db.add(session)
    try:
        apply_transition(TableStatus, TableStatus(table.status), TableStatus.SEATED)
    except (InvalidStateTransition, ValueError) as exc:
        raise _conflict(exc, TableStatus, table.status) from exc
    table.status = TableStatus.SEATED.value
    await db.flush()
    await record_audit(
        db,
        actor_role=actor.role,
        event_type="table_session.opened",
        payload={"table_id": table.id, "table_code": table.code, "session_id": session.id},
        branch_id=table.branch_id,
    )
    await db.commit()
    await invalidate_operational_cache(branch_id=table.branch_id, table_session_id=session.id)
    await db.refresh(session)
    return {
        "id": session.id,
        "table_id": session.table_id,
        "branch_id": session.branch_id,
        "status": session.status,
        "opened_at": session.opened_at,
        "closed_at": session.closed_at,
    }


@router.get("/table-sessions", response_model=list[TableSessionOut])
async def list_table_sessions(
    branch_id: int = Query(ge=1),
    status: TableSessionStatusValue | None = Query(default=None),
    open_only: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    actor: ActorContext = Depends(get_actor_context),
) -> list[dict]:
    require_branch_access(actor, branch_id)
    require_permission(actor, "session.read")
    stmt = select(TableSession).where(TableSession.branch_id == branch_id)
    if status:
        stmt = stmt.where(TableSession.status == status)
    if open_only:
        stmt = stmt.where(TableSession.status != TableSessionStatus.CLOSED.value)
    rows = (await db.execute(stmt.order_by(TableSession.opened_at.desc()))).scalars().all()
    return [
        {
            "id": r.id,
            "table_id": r.table_id,
            "branch_id": r.branch_id,
            "status": r.status,
            "opened_at": r.opened_at,
            "closed_at": r.closed_at,
        }
        for r in rows
    ]


@router.get("/table-sessions/{table_session_id}")
async def get_table_session_detail(
    table_session_id: int,
    db: AsyncSession = Depends(get_db),
    actor: ActorContext = Depends(get_actor_context),
) -> dict[str, Any]:
    """Live service state: table status, joined guests, and payment splits."""
    await _session_for_actor(db, table_session_id, actor)
    state = await get_table_session_state(db, table_session_id)
    if not state.get("found"):
        raise HTTPException(status_code=404, detail=f"Table session {table_session_id} not found")
    splits = (
        await get_payment_split_view(db, table_session_id)
        if actor.has_permission("payments.read")
        else None
    )
    return {**state, "payment_splits": splits}


@router.post(
    "/table-sessions/{table_session_id}/guests",
    response_model=GuestSessionOut,
    status_code=201,
)
async def join_table_session(
    table_session_id: int,
    payload: GuestJoinRequest,
    db: AsyncSession = Depends(get_db),
    actor: ActorContext = Depends(get_actor_context),
) -> dict:
    """A guest scans the QR, submits the OTP, and joins the shared session.

    Extra joins are valid — a table session is not "occupied twice".
    """
    session = await _session_for_actor(db, table_session_id, actor, "session.read")
    qr_mode = await _verify_qr(db, session, payload)
    if session.status == TableSessionStatus.CLOSED.value:
        raise HTTPException(
            status_code=409,
            detail=f"Table session {table_session_id} is closed — no new joins.",
        )

    guest = GuestSession(
        table_session_id=table_session_id,
        display_name=payload.display_name,
        phone=payload.phone,
        otp_verified=True,  # local QR mode is explicitly gated by ALLOW_TEST_QR
        status=GuestSessionStatus.AUTHENTICATING.value,
        dietary_preferences=payload.dietary_preferences,
    )
    db.add(guest)
    await db.flush()
    apply_transition(
        GuestSessionStatus, GuestSessionStatus.AUTHENTICATING, GuestSessionStatus.JOINED
    )
    guest.status = GuestSessionStatus.JOINED.value
    await record_audit(
        db,
        actor_role=actor.role,
        event_type="guest_session.joined",
        payload={
            "guest_session_id": guest.id,
            "name": guest.display_name,
            "verification_mode": qr_mode,
        },
        branch_id=session.branch_id,
    )
    await db.commit()
    await invalidate_operational_cache(branch_id=session.branch_id, table_session_id=table_session_id)
    await db.refresh(guest)
    return {
        "id": guest.id,
        "table_session_id": guest.table_session_id,
        "display_name": guest.display_name,
        "phone": guest.phone,
        "otp_verified": guest.otp_verified,
        "status": guest.status,
        "dietary_preferences": guest.dietary_preferences,
    }


@router.get("/guest-sessions", response_model=list[GuestSessionOut])
async def list_guest_sessions(
    table_session_id: int = Query(ge=1),
    db: AsyncSession = Depends(get_db),
    actor: ActorContext = Depends(get_actor_context),
) -> list[dict]:
    await _session_for_actor(db, table_session_id, actor)
    rows = (
        await db.execute(
            select(GuestSession)
            .where(GuestSession.table_session_id == table_session_id)
            .order_by(GuestSession.id.asc())
        )
    ).scalars().all()
    return [
        {
            "id": g.id,
            "table_session_id": g.table_session_id,
            "display_name": g.display_name,
            "phone": g.phone,
            "otp_verified": g.otp_verified,
            "status": g.status,
            "dietary_preferences": g.dietary_preferences,
        }
        for g in rows
    ]


@router.patch("/guest-sessions/{guest_id}/status", response_model=GuestSessionOut)
async def update_guest_status(
    guest_id: int,
    payload: StatusUpdate,
    db: AsyncSession = Depends(get_db),
    actor: ActorContext = Depends(get_actor_context),
) -> dict:
    guest = await db.get(GuestSession, guest_id)
    if guest is None:
        raise HTTPException(status_code=404, detail=f"Guest session {guest_id} not found")
    session = await _session_for_actor(db, guest.table_session_id, actor, "session.read")
    try:
        apply_transition(
            GuestSessionStatus, GuestSessionStatus(guest.status), GuestSessionStatus(payload.status)
        )
    except (InvalidStateTransition, ValueError) as exc:
        raise _conflict(exc, GuestSessionStatus, guest.status) from exc
    guest.status = payload.status
    await record_audit(
        db,
        actor_role=actor.role,
        event_type="guest_session.status_changed",
        payload={"guest_session_id": guest.id, "status": guest.status},
        branch_id=session.branch_id,
    )
    await db.commit()
    await invalidate_operational_cache(table_session_id=guest.table_session_id)
    await db.refresh(guest)
    return {
        "id": guest.id,
        "table_session_id": guest.table_session_id,
        "display_name": guest.display_name,
        "phone": guest.phone,
        "otp_verified": guest.otp_verified,
        "status": guest.status,
        "dietary_preferences": guest.dietary_preferences,
    }


@router.patch("/table-sessions/{table_session_id}/status", response_model=TableSessionOut)
async def update_table_session_status(
    table_session_id: int,
    payload: StatusUpdate,
    db: AsyncSession = Depends(get_db),
    actor: ActorContext = Depends(get_actor_context),
) -> dict:
    """Move the *service visit* (open → ordering → dining → settling → closed).

    Independent of the physical table status; closing releases the table.
    """
    session = await _session_for_actor(db, table_session_id, actor, "tables.update")
    try:
        target = TableSessionStatus(payload.status)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    try:
        apply_transition(TableSessionStatus, TableSessionStatus(session.status), target)
    except (InvalidStateTransition, ValueError) as exc:
        raise _conflict(exc, TableSessionStatus, session.status) from exc

    if target is TableSessionStatus.CLOSED:
        unsettled = await db.scalar(
            select(func.count(Payment.id))
            .join(Order, Payment.order_id == Order.id)
            .where(
                Order.table_session_id == table_session_id,
                Payment.status.notin_(["refunded", "failed"]),
            )
        )
        if int(unsettled or 0) > 0:
            raise HTTPException(status_code=409, detail="Settle or refund payments before closing the session")

    session.status = target.value
    if target is TableSessionStatus.CLOSED:
        session.closed_at = datetime.utcnow()
        table = await db.get(DiningTable, session.table_id)
        if table is not None and table.status == TableStatus.SEATED.value:
            apply_transition(TableStatus, TableStatus.SEATED, TableStatus.DIRTY)
            table.status = TableStatus.DIRTY.value

    await record_audit(
        db,
        actor_role=actor.role,
        event_type="table_session.status_changed",
        payload={"session_id": session.id, "status": session.status},
        branch_id=session.branch_id,
    )
    await db.commit()
    await invalidate_operational_cache(branch_id=session.branch_id, table_session_id=session.id)
    await db.refresh(session)
    return {
        "id": session.id,
        "table_id": session.table_id,
        "branch_id": session.branch_id,
        "status": session.status,
        "opened_at": session.opened_at,
        "closed_at": session.closed_at,
    }
