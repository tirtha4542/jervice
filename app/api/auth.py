"""Local development authentication helpers.

The dev-token endpoint is intentionally disabled in production and requires a
configured bootstrap secret. It exists so the Node console can test the same
bearer-authenticated routes without shipping a public token-minting UI.
"""

from __future__ import annotations

import hmac
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app.core.auth import create_access_token
from app.core.config import settings

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class DevTokenRequest(BaseModel):
    user_id: int = Field(default=1, ge=1)
    org_id: int = Field(default=1, ge=1)
    branch_id: int = Field(default=8, ge=1)
    role: Literal["customer", "waiter", "kitchen", "cashier", "manager", "owner"] = "manager"
    table_session_id: int | None = Field(default=None, ge=1)


@router.post("/dev-token")
async def issue_dev_token(
    request: Request,
    payload: DevTokenRequest,
    x_dev_bootstrap: str | None = Header(default=None),
) -> dict[str, object]:
    client_host = request.client.host if request.client else ""
    if client_host not in {"127.0.0.1", "::1", "localhost"}:
        raise HTTPException(status_code=403, detail="Development auth is loopback-only")
    if settings.is_production:
        raise HTTPException(status_code=404, detail="Not found")
    if not settings.dev_auth_token:
        raise HTTPException(
            status_code=503,
            detail="Local auth is not configured; set DEV_AUTH_TOKEN in .env",
        )
    if not x_dev_bootstrap or not hmac.compare_digest(x_dev_bootstrap, settings.dev_auth_token):
        raise HTTPException(status_code=403, detail="Invalid development bootstrap token")

    token = create_access_token(
        {
            "sub": payload.user_id,
            "org_id": payload.org_id,
            "branch_id": payload.branch_id,
            "role": payload.role,
            "table_session_id": payload.table_session_id,
        }
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "role": payload.role,
        "branch_id": payload.branch_id,
        "user_id": payload.user_id,
    }
