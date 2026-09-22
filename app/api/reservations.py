"""Reservation REST endpoints — Swagger-testable CRUD with state-machine enforcement."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.tools import get_reservations
from app.core.database import get_db
from app.domain.state_machines import InvalidStateTransition, ReservationStatus, apply_transition
from app.models import Branch, DiningTable, Reservation

router = APIRouter(prefix="/api/v1/reservations", tags=["reservations"])

ReservationStatusValue = Literal[
    "requested", "confirmed", "seated", "no_show", "cancelled", "completed"
]


class ReservationCreate(BaseModel):
    branch_id: int = Field(ge=1, description="Tenant branch this booking belongs to")
    guest_name: str = Field(min_length=1, max_length=255, examples=["Amina Yusuf"])
    party_size: int = Field(default=2, ge=1, le=50, examples=[4])
    start_at: datetime = Field(examples=["2026-09-22T19:00:00"])
    table_id: int | None = Field(default=None, description="Optional pre-assigned dining table")


class ReservationStatusUpdate(BaseModel):
    status: ReservationStatusValue


class ReservationOut(BaseModel):
    id: int
    branch_id: int
    table_id: int | None
    guest_name: str
    party_size: int
    status: ReservationStatusValue
    start_at: datetime


def _serialize(row: Reservation) -> dict:
    return {
        "id": row.id,
        "branch_id": row.branch_id,
        "table_id": row.table_id,
        "guest_name": row.guest_name,
        "party_size": row.party_size,
        "status": row.status,
        "start_at": row.start_at,
    }


def _from_tool(item: dict) -> dict:
    """The shared query tool uses descriptive keys for JARVIS; REST wants id/status."""
    return {
        "id": item["reservation_id"],
        "branch_id": item["branch_id"],
        "table_id": item["table_id"],
        "guest_name": item["guest_name"],
        "party_size": item["party_size"],
        "status": item["reservation_status"],
        "start_at": item["start_at"],
    }


@router.get("", response_model=list[ReservationOut])
async def list_reservations(
    branch_id: int = Query(ge=1, description="Branch to scope the query to"),
    status: ReservationStatusValue | None = Query(default=None, description="Filter by lifecycle state"),
    limit: int = Query(default=50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """List reservations for a branch — uses the same query tool JARVIS reads."""
    rows = await get_reservations(db, branch_id, status=status, limit=limit)
    return [_from_tool(item) for item in rows]


@router.post("", response_model=ReservationOut, status_code=201)
async def create_reservation(
    payload: ReservationCreate,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Create a reservation. Status always starts at `requested` per the state machine."""
    branch_exists = await db.scalar(select(Branch.id).where(Branch.id == payload.branch_id))
    if branch_exists is None:
        raise HTTPException(
            status_code=400,
            detail=f"branch_id {payload.branch_id} does not exist. "
            "Run POST /api/v1/demo/seed first, or create the Branch row.",
        )
    if payload.table_id is not None:
        table_exists = await db.scalar(select(DiningTable.id).where(DiningTable.id == payload.table_id))
        if table_exists is None:
            raise HTTPException(
                status_code=400,
                detail=f"table_id {payload.table_id} does not exist for branch {payload.branch_id}.",
            )

    row = Reservation(
        branch_id=payload.branch_id,
        table_id=payload.table_id,
        guest_name=payload.guest_name,
        party_size=payload.party_size,
        status=ReservationStatus.REQUESTED.value,
        start_at=payload.start_at,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return _serialize(row)


@router.patch("/{reservation_id}/status", response_model=ReservationOut)
async def update_reservation_status(
    reservation_id: int,
    payload: ReservationStatusUpdate,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Move a reservation through its lifecycle.

    Illegal jumps return **409** — this is `apply_transition(ReservationStatus, ...)`
    from `app/domain/state_machines.py` enforced on a real request path.
    """
    row = await db.get(Reservation, reservation_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Reservation {reservation_id} not found")

    try:
        current = ReservationStatus(row.status)
        target = ReservationStatus(payload.status)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    try:
        apply_transition(ReservationStatus, current, target)
    except InvalidStateTransition as exc:
        allowed = sorted(s.value for s in _allowed_from(current))
        raise HTTPException(
            status_code=409,
            detail=f"{exc} — allowed from '{current.value}': {allowed}",
        ) from exc

    row.status = target.value
    await db.commit()
    await db.refresh(row)
    return _serialize(row)


def _allowed_from(current: ReservationStatus) -> set:
    from app.domain.state_machines import TRANSITIONS

    return TRANSITIONS[ReservationStatus].get(current, set())
