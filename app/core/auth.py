"""Authentication and ActorContext resolution module.

Provides JWT decoding, token issuance, and FastAPI dependency injection for ActorContext.
Uses standard library HMAC-SHA256 for zero-dependency JWT encoding/decoding.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from typing import Any

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import settings

logger = logging.getLogger(__name__)

security = HTTPBearer(auto_error=False)


class ActorContext(BaseModel):
    """Immutable operational context resolved per request from JWT / Session."""

    model_config = ConfigDict(frozen=True)

    user_id: int = Field(default=1, description="Authenticated user ID")
    role: str = Field(default="manager", description="Active role (waiter, kitchen, cashier, manager, owner, customer)")
    org_id: int = Field(default=1, description="Organization ID")
    branch_id: int = Field(default=8, description="Branch ID (operational boundary)")
    table_session_id: int | None = Field(default=None, description="Active table session ID if scanned via QR")
    permissions: set[str] = Field(default_factory=set, description="Resolved granular permissions")
    assigned_tables: list[str] = Field(default_factory=list, description="Assigned table codes (e.g. ['T1', 'T2'])")

    def has_permission(self, permission: str) -> bool:
        """Check if actor context possesses a specific permission."""
        if "*" in self.permissions or "admin.all" in self.permissions:
            return True
        return permission in self.permissions


def _b64_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("utf-8")


def _b64_decode(data: str) -> bytes:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded)


def create_access_token(
    payload: dict[str, Any],
    secret: str | None = None,
    algorithm: str = "HS256",
) -> str:
    """Create a signed JWT token."""
    key = (secret or settings.jwt_secret).encode("utf-8")
    header = {"alg": algorithm, "typ": "JWT"}

    header_b64 = _b64_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    payload_b64 = _b64_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))

    signing_input = f"{header_b64}.{payload_b64}".encode("utf-8")
    signature = hmac.new(key, signing_input, hashlib.sha256).digest()
    sig_b64 = _b64_encode(signature)

    return f"{header_b64}.{payload_b64}.{sig_b64}"


def decode_access_token(token: str, secret: str | None = None) -> dict[str, Any]:
    """Decode and verify a signed JWT token."""
    key = (secret or settings.jwt_secret).encode("utf-8")
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Invalid JWT format")

    header_b64, payload_b64, sig_b64 = parts
    signing_input = f"{header_b64}.{payload_b64}".encode("utf-8")

    expected_sig = hmac.new(key, signing_input, hashlib.sha256).digest()
    actual_sig = _b64_decode(sig_b64)

    if not hmac.compare_digest(expected_sig, actual_sig):
        raise ValueError("Invalid JWT signature")

    payload_bytes = _b64_decode(payload_b64)
    return json.loads(payload_bytes.decode("utf-8"))


def resolve_role_permissions(role: str) -> set[str]:
    """Default permissions mapping by role according to ARCHITECTURE.md."""
    role = role.lower()
    if role == "owner":
        return {"*"}
    if role == "manager":
        return {
            "orders.read", "orders.accept", "orders.reject", "orders.update", "orders.serve", "orders.cancel",
            "tables.read", "tables.update", "tables.manage", "session.read",
            "kitchen.queue.read", "kitchen.queue.update",
            "inventory.read", "inventory.update",
            "payments.read", "payments.create", "payments.refund",
            "menu.read", "menu.update", "staff.read", "reports.read",
        }
    if role == "waiter":
        return {
            "orders.read", "orders.create", "orders.accept", "orders.reject", "orders.serve",
            "tables.read", "tables.update", "session.read",
        }
    if role == "kitchen":
        return {
            "orders.read", "kitchen.queue.read", "order_items.read",
            "order_items.start", "order_items.complete", "inventory.read",
        }
    if role == "cashier":
        return {
            "orders.read", "tables.read", "session.read",
            "payments.read", "payments.create", "payments.refund",
        }
    if role == "customer":
        return {
            "orders.read", "orders.create", "menu.read", "session.read",
        }
    return {"menu.read", "orders.read"}


def build_actor_context(claims: dict[str, Any]) -> ActorContext:
    """Translate decoded JWT claims into an ActorContext."""
    role = claims.get("role", "manager")
    perms = set(claims.get("permissions") or [])
    if not perms:
        perms = resolve_role_permissions(role)

    return ActorContext(
        user_id=int(claims.get("sub", claims.get("user_id", 1))),
        role=role,
        org_id=int(claims.get("org_id", 1)),
        branch_id=int(claims.get("branch_id", 8)),
        table_session_id=claims.get("table_session_id"),
        permissions=perms,
        assigned_tables=claims.get("assigned_tables") or [],
    )


async def get_actor_context(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> ActorContext:
    """FastAPI dependency to extract and decode ActorContext from Bearer JWT."""
    if credentials and credentials.credentials:
        try:
            claims = decode_access_token(credentials.credentials)
            return build_actor_context(claims)
        except Exception as exc:
            logger.warning("JWT validation failed: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid authentication token: {exc}",
            )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication token required. Please provide a valid Bearer JWT token.",
    )
