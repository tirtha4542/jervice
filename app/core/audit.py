"""Audit trail writer.

Every mutating flow records an ``audit_logs`` row inside the caller's
transaction, so the log commits (or rolls back) together with the change it
describes. Callers own the commit.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog


async def record_audit(
    db: AsyncSession,
    *,
    actor_role: str,
    event_type: str,
    payload: dict[str, Any] | None = None,
    branch_id: int | None = None,
) -> AuditLog:
    """Stage an audit_logs row. The caller commits."""
    row = AuditLog(
        branch_id=branch_id,
        actor_role=actor_role,
        event_type=event_type,
        payload=payload or {},
    )
    db.add(row)
    return row
