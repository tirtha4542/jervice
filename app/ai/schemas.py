from typing import Any, Literal

from pydantic import BaseModel, Field

JarvisRole = Literal["waiter", "kitchen", "manager", "customer", "cashier"]


class ContextPayload(BaseModel):
    active_orders: list[dict[str, Any]] = Field(default_factory=list)
    station_queues: dict[str, Any] = Field(default_factory=dict)
    inventory_alerts: list[dict[str, Any]] = Field(default_factory=list)


class JarvisExecuteRequest(BaseModel):
    role: JarvisRole
    branch_id: int
    table_session_id: int | None = None
    user_query: str
    context_payload: ContextPayload = Field(default_factory=ContextPayload)


class JarvisRecommendation(BaseModel):
    title: str
    priority: Literal["low", "medium", "high", "critical"]
    audience_role: JarvisRole
    action: str
    rationale: str
    related_entities: dict[str, Any] = Field(default_factory=dict)


class JarvisExecuteResponse(BaseModel):
    role: JarvisRole
    branch_id: int
    table_session_id: int | None
    summary: str
    recommendations: list[JarvisRecommendation]
    tool_observations: dict[str, Any] = Field(default_factory=dict)
    model: str
