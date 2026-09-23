"""Permission registry & scope enforcement engine.

Defines granular permission policies per role and maps AI tools to required permissions.
"""

from __future__ import annotations

from typing import Any

from app.core.auth import ActorContext

# Mapping of AI tool name -> required permission string
TOOL_PERMISSION_MAP: dict[str, str] = {
    "get_table_session_state": "session.read",
    "get_active_orders": "orders.read",
    "get_station_queues": "kitchen.queue.read",
    "check_recipe_bom_inventory": "inventory.read",
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


def can_execute_tool(actor: ActorContext, tool_name: str) -> bool:
    """Determine whether an ActorContext is authorized to invoke a specific tool."""
    required_perm = TOOL_PERMISSION_MAP.get(tool_name)
    if not required_perm:
        # If tool has no permission mapping, allow by default
        return True
    return actor.has_permission(required_perm)


def prune_tools_for_actor(actor: ActorContext, all_tool_names: list[str]) -> list[str]:
    """Filter list of tool names down to only those authorized for the given actor."""
    return [t for t in all_tool_names if can_execute_tool(actor, t)]


def validate_scope_access(actor: ActorContext, requested_branch_id: int) -> bool:
    """Verify that an actor is accessing data within their authorized branch scope."""
    if "*" in actor.permissions or "admin.all" in actor.permissions:
        return True
    # Branch scope match check
    return actor.branch_id == requested_branch_id

