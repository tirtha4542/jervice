"""LangGraph JARVIS orchestrator with Groq (llama-3.3-70b-versatile)."""

from __future__ import annotations

import json
import logging
from typing import Any, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq
from langgraph.graph import END, START, StateGraph

from app.ai.prompts import system_prompt_for
from app.ai.schemas import JarvisExecuteRequest, JarvisExecuteResponse, JarvisRecommendation, JarvisRole
from app.core.config import settings

logger = logging.getLogger(__name__)


class JarvisState(TypedDict):
    role: JarvisRole
    branch_id: int
    table_session_id: int | None
    user_query: str
    operational_context: dict[str, Any]
    system_prompt: str
    raw_model_output: str
    summary: str
    recommendations: list[dict[str, Any]]


def _llm() -> ChatGroq:
    if not settings.groq_api_key:
        raise RuntimeError("GROQ_API_KEY is not configured")
    return ChatGroq(
        api_key=settings.groq_api_key,
        model=settings.groq_model,
        temperature=settings.jarvis_temperature,
    )


def bind_role(state: JarvisState) -> JarvisState:
    return {**state, "system_prompt": system_prompt_for(state["role"])}


def infer(state: JarvisState) -> JarvisState:
    schema_hint = {
        "summary": "one paragraph operational brief",
        "recommendations": [
            {
                "title": "short title",
                "priority": "low|medium|high|critical",
                "audience_role": state["role"],
                "action": "concrete next action",
                "rationale": "why, citing independent statuses",
                "related_entities": {"order_id": 0},
            }
        ],
    }
    human = (
        f"Branch ID: {state['branch_id']}\n"
        f"Table session ID: {state['table_session_id']}\n"
        f"Role: {state['role']}\n"
        f"User query: {state['user_query']}\n\n"
        f"Operational context JSON:\n{json.dumps(state['operational_context'], default=str)}\n\n"
        f"Respond with JSON only matching:\n{json.dumps(schema_hint)}"
    )
    llm = _llm()
    response = llm.invoke(
        [SystemMessage(content=state["system_prompt"]), HumanMessage(content=human)]
    )
    raw = response.content if isinstance(response.content, str) else json.dumps(response.content)
    parsed = _parse_json(raw)
    return {
        **state,
        "raw_model_output": raw,
        "summary": parsed.get("summary") or "No summary produced.",
        "recommendations": parsed.get("recommendations") or [],
    }


def _parse_json(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(text[start : end + 1])
                return data if isinstance(data, dict) else {}
            except json.JSONDecodeError:
                return {}
        return {}


def build_graph():
    graph = StateGraph(JarvisState)
    graph.add_node("bind_role", bind_role)
    graph.add_node("infer", infer)
    graph.add_edge(START, "bind_role")
    graph.add_edge("bind_role", "infer")
    graph.add_edge("infer", END)
    return graph.compile()


_GRAPH = None


def get_graph():
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_graph()
    return _GRAPH


def heuristic_fallback(request: JarvisExecuteRequest, context: dict[str, Any]) -> JarvisExecuteResponse:
    """Used when Groq is unavailable so the orchestration contract still works."""
    recs: list[JarvisRecommendation] = []
    queues = context.get("station_queues") or {}
    for station, tickets in queues.items():
        ready = [t for t in tickets if t.get("order_item_status") == "ready"]
        if ready and request.role in ("waiter", "kitchen", "manager"):
            recs.append(
                JarvisRecommendation(
                    title=f"{len(ready)} ready item(s) on {station}",
                    priority="high",
                    audience_role=request.role,
                    action=f"Pick up or expedite ready tickets on station '{station}'.",
                    rationale="Order item status is independent of table occupancy; ready items wait for handoff.",
                    related_entities={"station": station, "order_item_ids": [t["order_item_id"] for t in ready]},
                )
            )
    alerts = context.get("inventory_alerts") or []
    critical = [a for a in alerts if a.get("severity") == "critical"]
    if critical and request.role in ("kitchen", "manager"):
        recs.append(
            JarvisRecommendation(
                title="Recipe BOM inventory bottleneck",
                priority="critical",
                audience_role=request.role,
                action="86 or 86-soon items whose BOM SKUs cannot cover one more portion.",
                rationale="Inventory is evaluated at recipe component level, not menu-item guesswork.",
                related_entities={"sku_codes": [a.get("sku_code") for a in critical]},
            )
        )
    table = context.get("live_table_session") or {}
    if table.get("found") and request.role in ("waiter", "customer", "cashier"):
        recs.append(
            JarvisRecommendation(
                title=f"{table.get('active_guest_count', 0)} guest(s) on shared table session",
                priority="medium",
                audience_role=request.role,
                action="Allow additional QR joins; settle per guest session, not per physical table.",
                rationale="Table status and table-session status are decoupled; multi-guest join is valid.",
                related_entities={"table_session_id": table.get("table_session_id")},
            )
        )
    if not recs:
        recs.append(
            JarvisRecommendation(
                title="No blocking anomalies",
                priority="low",
                audience_role=request.role,
                action="Continue monitoring independent state cycles for this branch.",
                rationale="Live tools returned no high-priority queue, inventory, or settlement exceptions.",
                related_entities={"branch_id": request.branch_id},
            )
        )
    return JarvisExecuteResponse(
        role=request.role,
        branch_id=request.branch_id,
        table_session_id=request.table_session_id,
        summary="Heuristic JARVIS briefing (Groq not configured). Live tools were still applied.",
        recommendations=recs,
        tool_observations=context,
        model="heuristic-fallback",
    )


async def run_jarvis(request: JarvisExecuteRequest, operational_context: dict[str, Any]) -> JarvisExecuteResponse:
    if not settings.groq_api_key:
        return heuristic_fallback(request, operational_context)

    graph = get_graph()
    try:
        result = await graph.ainvoke(
            {
                "role": request.role,
                "branch_id": request.branch_id,
                "table_session_id": request.table_session_id,
                "user_query": request.user_query,
                "operational_context": operational_context,
                "system_prompt": "",
                "raw_model_output": "",
                "summary": "",
                "recommendations": [],
            }
        )
    except Exception as exc:  # noqa: BLE001 - a Groq outage must not 500 the route
        logger.warning("Groq inference failed (%s); serving heuristic fallback.", exc)
        return heuristic_fallback(request, operational_context)
    recs = []
    for item in result.get("recommendations") or []:
        try:
            recs.append(JarvisRecommendation.model_validate(item))
        except Exception:
            continue
    if not recs:
        fallback = heuristic_fallback(request, operational_context)
        recs = fallback.recommendations
    return JarvisExecuteResponse(
        role=request.role,
        branch_id=request.branch_id,
        table_session_id=request.table_session_id,
        summary=result.get("summary") or "",
        recommendations=recs,
        tool_observations=operational_context,
        model=settings.groq_model,
    )
