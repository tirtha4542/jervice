"""Development-only seed so Swagger isn't pointing at empty tables.

Returns 403 whenever APP_ENV is production.

Delegates to ``app.seed.seed_all`` — the same idempotent routine the
``seed_db.py`` script runs — so the API route and the script always write
identical rows across all 17 tables.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.seed import seed_all

router = APIRouter(prefix="/api/v1/demo", tags=["demo"])


@router.post("/seed")
async def seed_demo_data(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Create the tenant, org chart, catalog, live service, and reservations.

    Idempotent: re-running reuses existing rows instead of duplicating them.
    """
    if settings.app_env.strip().lower() in {"production", "prod"}:
        raise HTTPException(status_code=403, detail="Demo seeding is disabled in production.")
    return await seed_all(db)
