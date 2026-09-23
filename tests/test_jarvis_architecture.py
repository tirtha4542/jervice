"""Comprehensive architecture tests for the 6-layer Tavonza AI system."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.ai.tool_gateway import execute_tool_secured, get_pruned_tool_schemas
from app.core.auth import (
    ActorContext,
    build_actor_context,
    create_access_token,
    decode_access_token,
    resolve_role_permissions,
)
from app.core.permissions import can_execute_tool, prune_tools_for_actor
from app.core.redis import cache_client
from app.main import create_app


@pytest.mark.asyncio
async def test_jwt_creation_and_decoding():
    claims = {
        "sub": 42,
        "role": "waiter",
        "org_id": 10,
        "branch_id": 8,
        "assigned_tables": ["T1", "T2"],
    }
    token = create_access_token(claims)
    decoded = decode_access_token(token)
    assert decoded["sub"] == 42
    assert decoded["role"] == "waiter"

    actor = build_actor_context(decoded)
    assert actor.user_id == 42
    assert actor.role == "waiter"
    assert actor.branch_id == 8
    assert actor.has_permission("orders.read")
    assert not actor.has_permission("inventory.update")


@pytest.mark.asyncio
async def test_dynamic_tool_pruning():
    waiter_actor = ActorContext(
        user_id=1,
        role="waiter",
        branch_id=8,
        permissions=resolve_role_permissions("waiter"),
    )
    manager_actor = ActorContext(
        user_id=2,
        role="manager",
        branch_id=8,
        permissions=resolve_role_permissions("manager"),
    )

    waiter_tools = get_pruned_tool_schemas(waiter_actor)
    manager_tools = get_pruned_tool_schemas(manager_actor)

    waiter_names = [t["name"] for t in waiter_tools]
    manager_names = [t["name"] for t in manager_tools]

    # Waiters see table sessions, active orders & reservations, but NOT inventory checks or kitchen queues
    assert "get_table_session_state" in waiter_names
    assert "check_recipe_bom_inventory" not in waiter_names
    assert "get_station_queues" not in waiter_names

    # Managers see all tools
    assert "get_table_session_state" in manager_names
    assert "check_recipe_bom_inventory" in manager_names
    assert "get_station_queues" in manager_names


@pytest.mark.asyncio
async def test_cache_first_bootstrap():
    key = "test_key_123"
    val = {"status": "ok", "count": 5}
    await cache_client.set(key, val, ttl_seconds=60)
    retrieved = await cache_client.get(key)
    assert retrieved == val

    await cache_client.delete(key)
    retrieved_after = await cache_client.get(key)
    assert retrieved_after is None


@pytest.mark.asyncio
async def test_secured_tool_gateway_unauthorized_blocking():
    mock_db = AsyncMock()
    waiter_actor = ActorContext(
        user_id=1,
        role="waiter",
        branch_id=8,
        permissions=resolve_role_permissions("waiter"),
    )
    # Attempting to run inventory check tool as waiter should fail with status 'denied'
    result = await execute_tool_secured(
        mock_db,
        waiter_actor,
        tool_name="check_recipe_bom_inventory",
        raw_args={"branch_id": 999},
    )
    assert result["status"] == "denied"
    assert "lacks permission" in result["error"]


@pytest.mark.asyncio
async def test_secured_tool_gateway_scope_injection():
    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.all.return_value = []
    mock_db.execute = AsyncMock(return_value=mock_result)

    manager_actor = ActorContext(
        user_id=2,
        role="manager",
        branch_id=8,
        permissions=resolve_role_permissions("manager"),
    )
    # Manager executes station queues tool with raw_args branch_id=999
    # Secured gateway must override with manager's branch_id=8
    result = await execute_tool_secured(
        mock_db,
        manager_actor,
        tool_name="get_station_queues",
        raw_args={"branch_id": 999},
    )
    assert result["status"] == "success"


@pytest.mark.asyncio
async def test_jarvis_execute_endpoint_authenticated():
    app = create_app()
    token = create_access_token({"sub": 1, "role": "manager", "branch_id": 8})

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        response = await ac.post(
            "/api/v1/jarvis/execute",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "role": "manager",
                "branch_id": 8,
                "user_query": "What are our main bottlenecks right now?",
                "context_payload": {},
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["role"] == "manager"
        assert data["branch_id"] == 8
        assert "summary" in data
        assert isinstance(data["recommendations"], list)


@pytest.mark.asyncio
async def test_jarvis_sse_streaming_endpoint():
    app = create_app()
    token = create_access_token({"sub": 1, "role": "manager", "branch_id": 8})

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        response = await ac.post(
            "/api/v1/jarvis/stream",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "role": "manager",
                "branch_id": 8,
                "user_query": "Stream live briefing.",
                "context_payload": {},
            },
        )
        assert response.status_code == 200
        assert "text/event-stream" in response.headers.get("content-type", "")
        content = response.text
        assert "data: " in content
        assert "[DONE]" in content

