import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException

from app.ai.openai_compat import router as openai_router
from app.ai.router import router as jarvis_router
from app.api.auth import router as auth_router
from app.api.catalog import router as catalog_router
from app.api.dashboard import router as dashboard_router
from app.api.demo import router as demo_router
from app.api.operations import router as operations_router
from app.api.orgchart import router as org_router
from app.api.orders import router as orders_router
from app.api.reservations import router as reservations_router
from app.api.tables import router as tables_router
from app.core.auth import require_authenticated_scope, validate_production_settings
from app.core.config import settings
from app.core.database import Base, get_engine
from app import models as _models  # noqa: F401  register metadata

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    validate_production_settings()
    try:
        async with get_engine().begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    except Exception as exc:  # noqa: BLE001 - startup must survive a missing database
        logger.warning("Skipping database schema creation: %s", exc)
    try:
        yield
    finally:
        if get_engine.cache_info().currsize:
            await get_engine().dispose()


def create_app() -> FastAPI:
    validate_production_settings()
    app = FastAPI(
        title=settings.app_name,
        version="0.2.0",
        description="Multi-tenant hospitality OS with route-only JARVIS orchestration.",
        lifespan=lifespan,
    )
    # Health, root, docs, and the local dev-token endpoint are intentionally
    # separate. Every operational router is protected by default.
    app.include_router(auth_router)
    app.include_router(jarvis_router)
    app.include_router(openai_router)
    app.include_router(operations_router, dependencies=[Depends(require_authenticated_scope)])
    app.include_router(reservations_router, dependencies=[Depends(require_authenticated_scope)])
    app.include_router(demo_router, dependencies=[Depends(require_authenticated_scope)])
    app.include_router(org_router, dependencies=[Depends(require_authenticated_scope)])
    app.include_router(catalog_router, dependencies=[Depends(require_authenticated_scope)])
    app.include_router(tables_router, dependencies=[Depends(require_authenticated_scope)])
    app.include_router(orders_router, dependencies=[Depends(require_authenticated_scope)])
    app.include_router(dashboard_router, dependencies=[Depends(require_authenticated_scope)])

    @app.get("/")
    async def root() -> dict[str, Any]:
        return {
            "service": settings.app_name,
            "status": "ok",
            "docs": "/docs",
            "health": "/health",
            "jarvis_route": "POST /api/v1/jarvis/execute",
            "auth_routes": ["POST /api/v1/auth/dev-token (local development only)"],
            "operations_routes": ["GET /api/v1/operations/context"],
            "openai_routes": ["GET /v1/models", "POST /v1/chat/completions"],
            "reservation_routes": [
                "GET /api/v1/reservations",
                "POST /api/v1/reservations",
                "PATCH /api/v1/reservations/{id}/status",
            ],
            "org_routes": [
                "GET /api/v1/org/hierarchy",
                "GET /api/v1/org/branches",
                "GET /api/v1/org/roles",
                "GET /api/v1/org/employees",
            ],
            "catalog_routes": [
                "GET /api/v1/menu?branch_id=8",
                "POST /api/v1/menu",
                "GET /api/v1/inventory?branch_id=8",
                "PATCH /api/v1/inventory/{sku_id}/on-hand",
                "GET /api/v1/recipes/{menu_item_id}",
            ],
            "table_routes": [
                "GET /api/v1/tables?branch_id=8",
                "POST /api/v1/tables/{id}/open-session",
                "GET /api/v1/table-sessions/{id}",
                "POST /api/v1/table-sessions/{id}/guests",
                "PATCH /api/v1/table-sessions/{id}/status",
                "PATCH /api/v1/tables/{id}/status",
            ],
            "order_routes": [
                "GET /api/v1/orders?branch_id=8",
                "POST /api/v1/orders",
                "PATCH /api/v1/orders/{id}/status",
                "PATCH /api/v1/order-items/{id}/status",
                "GET /api/v1/kitchen/queues?branch_id=8",
                "POST /api/v1/payments",
                "PATCH /api/v1/payments/{id}/status",
            ],
            "dashboard_routes": ["GET /api/v1/dashboard/{branch_id}"],
            "demo_routes": ["POST /api/v1/demo/seed"],
        }

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": settings.app_name, "environment": settings.app_env}

    @app.get("/ready")
    async def ready() -> dict[str, str]:
        try:
            async with get_engine().connect() as conn:
                await conn.exec_driver_sql("SELECT 1")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Readiness check failed: %s", exc.__class__.__name__)
            raise HTTPException(status_code=503, detail="Database is not ready")
        return {"status": "ready", "service": settings.app_name}

    return app


app = create_app()
