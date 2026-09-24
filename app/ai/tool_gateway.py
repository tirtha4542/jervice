"""Secured Tool Gateway & Scope Injection Layer.

Validates actor permissions, injects scope boundaries (branch_id, org_id),
and executes domain tools safely with audit logging.
Strictly read-only for AI tools: AI agents inspect state but do not mutate database tables directly.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.tools import (
    check_recipe_bom_inventory,
    get_active_orders,
    get_branch_inventory,
    get_payment_split_view,
    get_reservations,
    get_station_queues,
    get_table_session_state,
)
from app.core.audit import record_audit
from app.core.auth import ActorContext
from app.core.permissions import can_execute_tool, prune_tools_for_actor

logger = logging.getLogger(__name__)

# Registry of executable tool functions
TOOL_EXECUTORS: dict[str, Callable[..., Any]] = {
    "get_table_session_state": get_table_session_state,
    "get_active_orders": get_active_orders,
    "get_station_queues": get_station_queues,
    "check_recipe_bom_inventory": check_recipe_bom_inventory,
    "get_branch_inventory": get_branch_inventory,
    "get_payment_split_view": get_payment_split_view,
    "get_reservations": get_reservations,
}

# Declarative metadata schemas for tool pruning & LLM binding
AVAILABLE_TOOLS_SCHEMA: list[dict[str, Any]] = [
    {
        "name": "get_table_session_state",
        "description": "Fetch status, guests, and orders for a specific table session.",
        "parameters": {
            "type": "object",
            "properties": {"table_session_id": {"type": "integer"}},
            "required": ["table_session_id"],
        },
    },
    {
        "name": "get_active_orders",
        "description": "Get active orders and order items for the branch or table session.",
        "parameters": {
            "type": "object",
            "properties": {
                "branch_id": {"type": "integer"},
                "table_session_id": {"type": "integer"},
            },
            "required": [],
        },
    },
    {
        "name": "get_station_queues",
        "description": "Retrieve active kitchen station queues and ready tickets.",
        "parameters": {
            "type": "object",
            "properties": {"branch_id": {"type": "integer"}},
            "required": [],
        },
    },
    {
        "name": "check_recipe_bom_inventory",
        "description": "Check recipe components and inventory SKU par levels for bottlenecks.",
        "parameters": {
            "type": "object",
            "properties": {"branch_id": {"type": "integer"}},
            "required": [],
        },
    },
    {
        "name": "get_branch_inventory",
        "description": "Fetch all inventory SKUs, codes, and on-hand quantities for the branch.",
        "parameters": {
            "type": "object",
            "properties": {"branch_id": {"type": "integer"}},
            "required": [],
        },
    },
    {
        "name": "get_payment_split_view",
        "description": "View guest payment splits and settlement status for a table session.",
        "parameters": {
            "type": "object",
            "properties": {"table_session_id": {"type": "integer"}},
            "required": ["table_session_id"],
        },
    },
    {
        "name": "get_reservations",
        "description": "List upcoming table reservations for the branch.",
        "parameters": {
            "type": "object",
            "properties": {"branch_id": {"type": "integer"}},
            "required": [],
        },
    },
]


def get_pruned_tool_schemas(actor: ActorContext) -> list[dict[str, Any]]:
    """Return tool schemas filtered dynamically for the actor's authorized permissions."""
    allowed = []
    for schema in AVAILABLE_TOOLS_SCHEMA:
        tool_name = schema["name"]
        if can_execute_tool(actor, tool_name):
            allowed.append(schema)
    return allowed


async def execute_tool_secured(
    db: AsyncSession,
    actor: ActorContext,
    tool_name: str,
    raw_args: dict[str, Any],
) -> dict[str, Any]:
    """Validate permissions, inject branch/table scopes, execute tool, and record audit log."""
    # 1. Permission check
    if not can_execute_tool(actor, tool_name):
        error_msg = f"Actor role '{actor.role}' lacks permission to execute tool '{tool_name}'."
        logger.warning(error_msg)
        await record_audit(
            db,
            actor_role=actor.role,
            event_type="AI_TOOL_UNAUTHORIZED_BLOCKED",
            payload={"tool_name": tool_name, "raw_args": raw_args, "error": error_msg},
            branch_id=actor.branch_id,
        )
        return {"error": error_msg, "status": "denied"}

    executor = TOOL_EXECUTORS.get(tool_name)
    if not executor:
        return {"error": f"Unknown tool '{tool_name}'", "status": "failed"}

    # 2. Scope Injection (Override arguments with actor's enforced branch_id & org_id)
    injected_args = dict(raw_args)
    if "branch_id" in injected_args or tool_name in ("get_active_orders", "get_station_queues", "check_recipe_bom_inventory", "get_branch_inventory", "get_reservations"):
        injected_args["branch_id"] = actor.branch_id

    if actor.table_session_id and "table_session_id" not in injected_args:
        injected_args["table_session_id"] = actor.table_session_id

    # 3. Execution & Audit
    try:
        if tool_name in ("get_table_session_state", "get_payment_split_view"):
            ts_id = injected_args.get("table_session_id") or actor.table_session_id or 1
            result = await executor(db, ts_id)
        elif tool_name == "get_active_orders":
            result = await executor(
                db,
                branch_id=actor.branch_id,
                table_session_id=injected_args.get("table_session_id") or actor.table_session_id,
            )
        elif tool_name in ("get_station_queues", "check_recipe_bom_inventory", "get_branch_inventory"):
            result = await executor(db, branch_id=actor.branch_id)
        elif tool_name == "get_reservations":
            result = await executor(db, branch_id=actor.branch_id)
        else:
            result = await executor(db, **injected_args)

        await record_audit(
            db,
            actor_role=actor.role,
            event_type="AI_TOOL_EXECUTION",
            payload={"tool_name": tool_name, "injected_args": injected_args, "success": True},
            branch_id=actor.branch_id,
        )
        return {"status": "success", "data": result}
    except Exception as exc:
        logger.error("Tool execution failed for %s: %s", tool_name, exc, exc_info=True)
        await record_audit(
            db,
            actor_role=actor.role,
            event_type="AI_TOOL_EXECUTION_ERROR",
            payload={"tool_name": tool_name, "error": str(exc)},
            branch_id=actor.branch_id,
        )
        return {"error": str(exc), "status": "error"}
