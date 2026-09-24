"""Authentication and request-scope resolution.

This module intentionally keeps token handling small and explicit for the local
prototype, but it no longer accepts privileged/default claims silently. Tokens
must be signed, unexpired, issued for this service, and contain a subject.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
import uuid
from typing import Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import settings

logger = logging.getLogger(__name__)
security = HTTPBearer(auto_error=False)

_DEV_JWT_SECRET = "tavonza-local-development-only-change-me"


class ActorContext(BaseModel):
    """Resolved identity and scope for one request."""

    model_config = ConfigDict(frozen=True)

    user_id: int
    role: str
    org_id: int = 1
    branch_id: int
    table_session_id: int | None = None
    permissions: set[str] = Field(default_factory=set)
    assigned_tables: list[str] = Field(default_factory=list)

    def has_permission(self, permission: str) -> bool:
        if "*" in self.permissions or "admin.all" in self.permissions:
            return True
        return permission in self.permissions

    def can_access_branch(self, branch_id: int) -> bool:
        return self.has_permission("admin.all") or self.branch_id == branch_id


class AuthError(ValueError):
    """Raised when a bearer token is invalid or incomplete."""


def _b64_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("utf-8")


def _b64_decode(data: str) -> bytes:
    if not data or len(data) > 16_384:
        raise AuthError("Invalid token encoding")
    padded = data + "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(padded.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise AuthError("Invalid token encoding") from exc


def _json_segment(data: bytes) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AuthError("Invalid token JSON") from exc
    if not isinstance(value, dict):
        raise AuthError("Invalid token structure")
    return value


def create_access_token(
    payload: dict[str, Any],
    secret: str | None = None,
    algorithm: str = "HS256",
    *,
    expires_in: int | None = None,
) -> str:
    """Create a signed, expiring access token.

    ``payload`` is copied; caller-owned dictionaries are never mutated.
    """
    if algorithm != "HS256":
        raise ValueError("Only HS256 is supported")
    key_value = secret or settings.jwt_secret
    if not key_value:
        raise ValueError("JWT secret is not configured")

    now = int(time.time())
    claims = dict(payload)
    claims.setdefault("iat", now)
    claims.setdefault("exp", now + (expires_in or settings.jwt_ttl_seconds))
    claims.setdefault("iss", settings.jwt_issuer)
    claims.setdefault("aud", settings.jwt_audience)
    claims.setdefault("jti", uuid.uuid4().hex)
    claims.setdefault("token_type", "access")

    header = {"alg": "HS256", "typ": "JWT"}
    header_b64 = _b64_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    payload_b64 = _b64_encode(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    signature = hmac.new(key_value.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{header_b64}.{payload_b64}.{_b64_encode(signature)}"


def decode_access_token(token: str, secret: str | None = None) -> dict[str, Any]:
    """Verify and decode an access token with strict lifecycle claims."""
    key_value = secret or settings.jwt_secret
    if not key_value:
        raise AuthError("JWT secret is not configured")
    parts = token.split(".")
    if len(parts) != 3:
        raise AuthError("Invalid token format")

    header_b64, payload_b64, signature_b64 = parts
    header = _json_segment(_b64_decode(header_b64))
    if header.get("alg") != "HS256" or header.get("typ") != "JWT":
        raise AuthError("Unsupported token header")

    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    expected = hmac.new(key_value.encode("utf-8"), signing_input, hashlib.sha256).digest()
    actual = _b64_decode(signature_b64)
    if not hmac.compare_digest(expected, actual):
        raise AuthError("Invalid token signature")

    claims = _json_segment(_b64_decode(payload_b64))
    now = int(time.time())
    try:
        exp = int(claims["exp"])
        nbf = int(claims.get("nbf", claims.get("iat", now)))
    except (KeyError, TypeError, ValueError) as exc:
        raise AuthError("Missing or invalid token lifetime") from exc
    if exp <= now:
        raise AuthError("Token has expired")
    if nbf > now + 5:
        raise AuthError("Token is not active")
    if claims.get("token_type", "access") != "access":
        raise AuthError("Invalid token type")
    if claims.get("iss") != settings.jwt_issuer:
        raise AuthError("Invalid token issuer")
    if claims.get("aud") != settings.jwt_audience:
        raise AuthError("Invalid token audience")
    if not claims.get("sub"):
        raise AuthError("Token subject is required")
    return claims


def resolve_role_permissions(role: str) -> set[str]:
    """Default local role bundles.

    Production authorization should resolve these from trusted membership data;
    this mapping exists only to make the local test console usable.
    """
    role = role.lower()
    if role == "owner":
        return {"*"}
    if role == "manager":
        return {
            "orders.read", "orders.create", "orders.accept", "orders.reject", "orders.update", "orders.serve", "orders.cancel",
            "tables.read", "tables.update", "tables.manage", "session.read",
            "kitchen.queue.read", "kitchen.queue.update", "inventory.read", "inventory.update",
            "payments.read", "payments.create", "payments.refund", "menu.read", "menu.update",
            "staff.read", "reports.read", "reservations.read", "reservations.write", "demo.seed",
        }
    if role == "waiter":
        return {
            "orders.read", "orders.create", "orders.accept", "orders.reject", "orders.serve",
            "tables.read", "tables.update", "session.read", "reservations.read",
        }
    if role == "kitchen":
        return {
            "orders.read", "kitchen.queue.read", "kitchen.queue.update", "order_items.read",
            "order_items.start", "order_items.complete", "inventory.read",
        }
    if role == "cashier":
        return {
            "orders.read", "tables.read", "session.read", "payments.read",
            "payments.create", "payments.refund",
        }
    if role == "customer":
        return {"orders.read", "orders.create", "menu.read", "session.read"}
    return set()


def build_actor_context(claims: dict[str, Any]) -> ActorContext:
    """Build a deny-by-default context from verified claims."""
    if not claims.get("sub"):
        raise AuthError("Token subject is required")
    try:
        user_id = int(claims["sub"])
        org_id = int(claims.get("org_id", 1))
        branch_id = int(claims.get("branch_id", 1))
    except (TypeError, ValueError) as exc:
        raise AuthError("Invalid identity claims") from exc
    if user_id <= 0 or org_id <= 0 or branch_id <= 0:
        raise AuthError("Invalid identity scope")

    role = str(claims.get("role", "customer")).lower()
    if "permissions" in claims:
        permissions = {str(value) for value in (claims.get("permissions") or [])}
    else:
        permissions = resolve_role_permissions(role)

    assigned_tables = claims.get("assigned_tables") or []
    if not isinstance(assigned_tables, list):
        raise AuthError("Invalid assigned_tables claim")

    return ActorContext(
        user_id=user_id,
        role=role,
        org_id=org_id,
        branch_id=branch_id,
        table_session_id=claims.get("table_session_id"),
        permissions=permissions,
        assigned_tables=[str(value) for value in assigned_tables],
    )


async def get_raw_bearer_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> str:
    if not credentials or not credentials.credentials:
        raise HTTPException(status_code=401, detail="Authentication token required")
    return credentials.credentials


async def get_actor_context(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> ActorContext:
    if not credentials or not credentials.credentials:
        raise HTTPException(status_code=401, detail="Authentication token required")
    try:
        claims = decode_access_token(credentials.credentials)
        return build_actor_context(claims)
    except (AuthError, TypeError, ValueError) as exc:
        logger.info("JWT validation failed: %s", exc.__class__.__name__)
        raise HTTPException(status_code=401, detail="Invalid or expired access token") from exc


async def require_authenticated_scope(
    request: Request,
    actor: ActorContext = Depends(get_actor_context),
) -> ActorContext:
    """Fast router-level guard for routes with a branch query parameter."""
    raw_branch = request.query_params.get("branch_id")
    if raw_branch is not None:
        try:
            branch_id = int(raw_branch)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="branch_id must be an integer") from exc
        if not actor.can_access_branch(branch_id):
            raise HTTPException(status_code=403, detail="Branch is outside your scope")
    return actor


def require_branch_access(actor: ActorContext, branch_id: int) -> None:
    if not actor.can_access_branch(branch_id):
        raise HTTPException(status_code=403, detail="Resource is outside your branch scope")


def require_permission(actor: ActorContext, permission: str) -> None:
    if not actor.has_permission(permission):
        raise HTTPException(status_code=403, detail=f"Permission denied: {permission}")


def validate_production_settings() -> None:
    """Fail fast when a deployment is obviously using development defaults."""
    if not settings.is_production:
        return
    if (
        not settings.jwt_secret
        or settings.jwt_secret == _DEV_JWT_SECRET
        or "replace-with" in settings.jwt_secret.lower()
    ):
        raise RuntimeError("JWT_SECRET must be changed in production")
    local_environment = settings.app_env.strip().lower() in {"development", "dev", "test", "local"}
    if settings.allow_test_qr and not local_environment:
        raise RuntimeError("ALLOW_TEST_QR is allowed only in a local/test environment")
    if settings.allow_test_context and not local_environment:
        raise RuntimeError("ALLOW_TEST_CONTEXT is allowed only in a local/test environment")
    if settings.dev_auth_token and not local_environment:
        raise RuntimeError("DEV_AUTH_TOKEN is allowed only in a local/test environment")
