"""Route-backed AI tool gateway.

The gateway deliberately contains no SQLAlchemy session. It calls the
backend's authenticated operations route and lets the backend apply the
resource/tenant policy.
"""

from __future__ import annotations

import logging
from typing import Any

from app.ai.backend_client import BackendRouteClient, BackendRouteError
from app.core.auth import ActorContext
from app.core.permissions import can_execute_tool, prune_tools_for_actor

logger = logging.getLogger(__name__)

TOOL_PERMISSION_MAP: dict[str, str] = {
    "get_table_session_state": "session.read",
    "get_active_orders": "orders.read",
    "get_station_queues": "kitchen.queue.read",
    "check_recipe_bom_inventory": "inventory.read",
    "get_branch_inventory": "inventory.read",
    "get_payment_split_view": "payments.read",
    "get_reservations": "tables.read",
    "get_branch_summary": "reports.read",
    "get_sales_summary": "reports.read",
    "update_order_item_status": "kitchen.queue.update",
    "mark_order_item_ready": "kitchen.queue.update",
    "serve_order": "orders.serve",
    "cancel_order": "orders.cancel",
    "refund_payment": "payments.refund",
}

AVAILABLE_TOOLS_SCHEMA: list[dict[str, Any]] = [
    {
        "name": "get_table_session_state",
        "description": "Fetch status and guests for a specific table session.",
        "parameters": {
            "type": "object",
            "properties": {"table_session_id": {"type": "integer"}},
            "required": ["table_session_id"],
        },
    },
    {
        "name": "get_active_orders",
        "description": "Get active orders and line items for the actor's branch.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "get_station_queues",
        "description": "Retrieve active kitchen station queues.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "check_recipe_bom_inventory",
        "description": "Check recipe components and inventory par levels.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "get_branch_inventory",
        "description": "Fetch inventory SKUs and on-hand quantities.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "get_payment_split_view",
        "description": "View payment splits for a table session.",
        "parameters": {
            "type": "object",
            "properties": {"table_session_id": {"type": "integer"}},
            "required": ["table_session_id"],
        },
    },
    {
        "name": "get_reservations",
        "description": "List upcoming reservations for the actor's branch.",
        "parameters": {"type": "object", "properties": {}},
    },
]


def get_pruned_tool_schemas(actor: ActorContext) -> list[dict[str, Any]]:
    return [schema for schema in AVAILABLE_TOOLS_SCHEMA if can_execute_tool(actor, schema["name"])]


async def execute_tool_secured(
    client: BackendRouteClient,
    actor: ActorContext,
    tool_name: str,
    raw_args: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute a read tool through the backend route boundary."""
    args = dict(raw_args or {})
    if not can_execute_tool(actor, tool_name):
        return {"error": f"Permission denied for tool '{tool_name}'.", "status": "denied"}
    if tool_name not in {schema["name"] for schema in AVAILABLE_TOOLS_SCHEMA}:
        return {"error": f"Unknown tool '{tool_name}'.", "status": "failed"}

    branch_id = actor.branch_id
    table_session_id = args.get("table_session_id")
    if tool_name in {"get_table_session_state", "get_payment_split_view"} and not table_session_id:
        table_session_id = actor.table_session_id
    if tool_name in {"get_table_session_state", "get_payment_split_view"} and not table_session_id:
        return {"error": "table_session_id is required", "status": "failed"}

    try:
        context = await client.operational_context(
            branch_id=branch_id,
            table_session_id=int(table_session_id) if table_session_id else None,
        )
    except (BackendRouteError, TypeError, ValueError) as exc:
        logger.warning("Route tool failed: %s", exc.__class__.__name__)
        return {"error": "Backend tool request failed", "status": "error"}

    key_by_tool = {
        "get_table_session_state": "live_table_session",
        "get_active_orders": "active_orders",
        "get_station_queues": "station_queues",
        "check_recipe_bom_inventory": "inventory_alerts",
        "get_branch_inventory": "inventory_skus",
        "get_payment_split_view": "payment_splits",
        "get_reservations": "reservations",
    }
    return {"status": "success", "data": context.get(key_by_tool[tool_name])}


__all__ = [
    "AVAILABLE_TOOLS_SCHEMA",
    "TOOL_PERMISSION_MAP",
    "execute_tool_secured",
    "get_pruned_tool_schemas",
    "prune_tools_for_actor",
]
