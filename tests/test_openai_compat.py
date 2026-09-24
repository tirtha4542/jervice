from fastapi.testclient import TestClient

from app.ai import openai_compat
from app.ai.schemas import JarvisExecuteResponse
from app.core.auth import create_access_token
from app.main import app

client = TestClient(
    app,
    headers={"Authorization": f"Bearer {create_access_token({'sub': 1, 'role': 'manager', 'branch_id': 8})}"},
)


def test_root_route_no_longer_404s():
    response = client.get("/")
    assert response.status_code == 200
    body = response.json()
    assert body["docs"] == "/docs"
    assert body["health"] == "/health"


def test_health_still_ok():
    assert client.get("/health").status_code == 200


def test_openai_models_listed():
    response = client.get("/v1/models")
    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "list"
    assert body["data"]
    assert all(item["object"] == "model" for item in body["data"])


def test_chat_completions_openai_shape(monkeypatch):
    async def fake_context(payload, actor, access_token, **kwargs):
        return {"station_queues": {}}

    async def fake_run(request, operational_context, actor=None, **kwargs):
        return JarvisExecuteResponse(
            role=request.role,
            branch_id=request.branch_id,
            table_session_id=request.table_session_id,
            summary="All clear on the floor.",
            recommendations=[],
            model="test-model",
        )

    monkeypatch.setattr(openai_compat, "build_operational_context", fake_context)
    monkeypatch.setattr(openai_compat, "run_jarvis", fake_run)

    response = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "status?"}]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "chat.completion"
    assert body["model"] == "test-model"
    choice = body["choices"][0]
    assert choice["message"]["role"] == "assistant"
    assert choice["finish_reason"] == "stop"
    assert "All clear on the floor." in choice["message"]["content"]
    assert body["usage"]["total_tokens"] > 0
