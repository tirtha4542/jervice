from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.config import settings

JarvisRole = Literal["waiter", "kitchen", "manager", "customer", "cashier", "owner"]


class ContextPayload(BaseModel):
    """Optional local fixture context.

    It is ignored unless ALLOW_TEST_CONTEXT is explicitly enabled; normal AI
    requests obtain context only from the backend operations route.
    """

    model_config = ConfigDict(extra="forbid")

    active_orders: list[dict[str, Any]] = Field(default_factory=list, max_length=settings.ai_max_context_items)
    station_queues: dict[str, Any] = Field(default_factory=dict, max_length=settings.ai_max_context_items)
    inventory_alerts: list[dict[str, Any]] = Field(default_factory=list, max_length=settings.ai_max_context_items)


class JarvisExecuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: JarvisRole
    branch_id: int = Field(ge=1)
    table_session_id: int | None = Field(default=None, ge=1)
    user_query: str = Field(min_length=1, max_length=settings.ai_max_query_length)
    context_payload: ContextPayload = Field(default_factory=ContextPayload)
    session_id: str | None = Field(
        default=None,
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
        description="Development conversation ID. Ownership is checked server-side.",
    )


class JarvisRecommendation(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    priority: Literal["low", "medium", "high", "critical"]
    audience_role: JarvisRole
    action: str = Field(min_length=1, max_length=500)
    rationale: str = Field(min_length=1, max_length=1000)
    related_entities: dict[str, Any] = Field(default_factory=dict)


class JarvisExecuteResponse(BaseModel):
    role: JarvisRole
    branch_id: int
    table_session_id: int | None
    summary: str
    recommendations: list[JarvisRecommendation]
    tool_observations: dict[str, Any] = Field(default_factory=dict)
    model: str
    session_id: str = Field(default="", description="Active conversation ID.")


class MemoryHistoryEntry(BaseModel):
    turn: int
    user_query: str
    summary: str
    role: str
    branch_id: int | None
    recommendations: list[dict[str, Any]] = Field(default_factory=list)


class MemoryHistoryResponse(BaseModel):
    session_id: str
    total_turns: int
    history: list[MemoryHistoryEntry]
