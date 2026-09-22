from app.ai.prompts import system_prompt_for
from app.ai.schemas import ContextPayload, JarvisExecuteRequest
from app.ai.agent import heuristic_fallback


def test_role_prompts_are_distinct():
    waiter = system_prompt_for("waiter")
    kitchen = system_prompt_for("kitchen")
    assert "Waiter JARVIS" in waiter
    assert "Kitchen/Bar JARVIS" in kitchen
    assert "Organization → Brand → Branch" in waiter


def test_heuristic_emits_ready_item_action():
    req = JarvisExecuteRequest(
        role="waiter",
        branch_id=1,
        table_session_id=42,
        user_query="Analyze current operational state...",
        context_payload=ContextPayload(),
    )
    context = {
        "station_queues": {
            "kitchen": [{"order_item_id": 9, "order_item_status": "ready"}],
        },
        "inventory_alerts": [],
        "live_table_session": {
            "found": True,
            "table_session_id": 42,
            "active_guest_count": 3,
        },
    }
    result = heuristic_fallback(req, context)
    assert result.role == "waiter"
    titles = [r.title for r in result.recommendations]
    assert any("ready" in t.lower() for t in titles)
