from __future__ import annotations

import pytest

from app.ai.memory import assert_session_access, claim_session
from app.ai.router import build_operational_context
from app.ai.schemas import ContextPayload, JarvisExecuteRequest
from app.core.auth import ActorContext, AuthError, create_access_token, decode_access_token
from app.main import app


def test_operational_routes_have_bearer_security():
    spec = app.openapi()
    assert spec["paths"]["/api/v1/operations/context"]["get"]["security"]
    assert spec["paths"]["/v1/chat/completions"]["post"]["security"]


def test_access_tokens_require_lifecycle_claims():
    token = create_access_token({"sub": 1, "role": "manager", "branch_id": 8}, expires_in=-1)
    with pytest.raises(AuthError):
        decode_access_token(token)


def test_memory_session_is_bound_to_actor():
    owner = ActorContext(
        user_id=1,
        role="manager",
        org_id=1,
        branch_id=8,
        permissions={"*"},
    )
    other = ActorContext(
        user_id=2,
        role="manager",
        org_id=1,
        branch_id=8,
        permissions={"*"},
    )
    claim_session("local-owner-thread", owner)
    assert_session_access("local-owner-thread", owner)
    with pytest.raises(PermissionError):
        assert_session_access("local-owner-thread", other)


@pytest.mark.asyncio
async def test_ai_context_uses_route_client_and_filters_payments():
    class FakeRouteClient:
        async def operational_context(self, *, branch_id, table_session_id=None):
            assert branch_id == 8
            return {
                "source": "backend_routes",
                "active_orders": [{"order_id": 1, "payments": [{"amount": "10"}]}],
                "payment_splits": {"splits": [{"amount": "10"}]},
                "station_queues": {},
                "inventory_alerts": [],
                "inventory_skus": [],
                "reservations": [],
            }

    actor = ActorContext(
        user_id=3,
        role="kitchen",
        org_id=1,
        branch_id=8,
        permissions={"orders.read", "kitchen.queue.read"},
    )
    request = JarvisExecuteRequest(
        role="kitchen",
        branch_id=8,
        user_query="What should I prepare?",
        context_payload=ContextPayload(),
    )
    context = await build_operational_context(
        request,
        actor,
        "test-token",
        client=FakeRouteClient(),
    )
    assert context["source"] == "backend_routes"
    assert "payment_splits" not in context
    assert context["active_orders"][0]["payments"] == []
