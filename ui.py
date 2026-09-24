"""Streamlit test console for the Tavonza AI / JARVIS API.

Run the API first, then this UI:

    uvicorn app.main:app --reload
    streamlit run ui.py
"""

from __future__ import annotations

import html
import json
from typing import Any

import httpx
import streamlit as st

from app.core.auth import create_access_token

st.set_page_config(
    page_title="Tavonza AI — JARVIS Console",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

ROLES = ["waiter", "kitchen", "manager", "customer", "cashier", "owner"]
PRIORITY_COLORS = {
    "critical": "#e74c3c",
    "high": "#e67e22",
    "medium": "#f1c40f",
    "low": "#27ae60",
}
DEFAULT_CONTEXT = {
    "active_orders": [],
    "station_queues": {
        "kitchen": [{"order_item_id": 9, "order_item_status": "ready"}],
    },
    "inventory_alerts": [],
}


# --------------------------------------------------------------------------- #
# HTTP helpers
# --------------------------------------------------------------------------- #
def base_url() -> str:
    return st.session_state.get("api_base", "http://127.0.0.1:8000").rstrip("/")


def get_auth_headers() -> dict[str, str]:
    headers = {}
    token = st.session_state.get("jwt_token")
    if token and token.strip():
        headers["Authorization"] = f"Bearer {token.strip()}"
    return headers


def call(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    timeout: float = 90.0,
):
    """Return (status_code | None, parsed_json_or_error_text)."""
    url = f"{base_url()}{path}"
    headers = get_auth_headers()
    try:
        if method == "GET":
            response = httpx.get(url, headers=headers, timeout=timeout)
        else:
            response = httpx.post(url, headers=headers, json=body, timeout=timeout)
    except httpx.HTTPError as exc:
        return None, str(exc)
    try:
        return response.status_code, response.json()
    except ValueError:
        return response.status_code, response.text


def parse_context(raw: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        st.error(f"Invalid context JSON: {exc}")
        return None
    if not isinstance(parsed, dict):
        st.error("Context JSON must be an object, e.g. {...}")
        return None
    return parsed


def render_recommendations(recs: list[dict[str, Any]]) -> None:
    for index, rec in enumerate(recs, start=1):
        priority = str(rec.get("priority", "")).lower()
        color = PRIORITY_COLORS.get(priority, "#7f8c8d")
        st.markdown(
            f"""
<div style="border-left:5px solid {color};padding:10px 14px;margin:10px 0;
            border-radius:6px;background:rgba(128,128,128,0.08)">
  <div style="font-weight:600">#{index} {html.escape(str(rec.get("title", "")))}</div>
  <div style="font-size:0.78rem;font-weight:700;color:{color};letter-spacing:0.5px">
    {html.escape(priority.upper())}
  </div>
  <div style="margin-top:4px"><b>Action:</b> {html.escape(str(rec.get("action", "")))}</div>
  <div><b>Why:</b> {html.escape(str(rec.get("rationale", "")))}</div>
</div>
""",
            unsafe_allow_html=True,
        )


# --------------------------------------------------------------------------- #
# Sidebar
# --------------------------------------------------------------------------- #
with st.sidebar:
    st.markdown("## 🤖 Tavonza AI")
    st.caption("JARVIS test console")
    st.text_input("API base URL", value="http://127.0.0.1:8000", key="api_base")
    st.divider()

    st.markdown("### 🔑 Authentication (JWT Layer)")
    auth_role = st.selectbox("Auth Role", ROLES, index=2, key="auth_role")
    auth_branch = st.number_input("Branch Scope ID", min_value=1, value=8, step=1, key="auth_branch")

    if st.button("Generate Test Bearer JWT"):
        generated_token = create_access_token({"sub": 1, "role": auth_role, "branch_id": int(auth_branch)})
        st.session_state["jwt_token"] = generated_token
        st.success(f"JWT Token generated for {auth_role} (Branch #{auth_branch})")

    st.text_area("Bearer JWT Token", key="jwt_token", value="", height=70)
    st.divider()

    health_status, health = call("GET", "/health", timeout=5)
    if health_status == 200:
        st.success(f"API online — {health.get('service', 'ok')}")
    elif health_status is None:
        st.error("API unreachable")
        st.code(str(health))
        st.caption("Start it with: `uvicorn app.main:app --reload`")
    else:
        st.error(f"API returned {health_status}")

    st.divider()
    st.markdown("**Endpoints**")
    spec_status, spec = call("GET", "/openapi.json", timeout=10)
    if spec_status == 200 and isinstance(spec, dict):
        for path, operations in spec.get("paths", {}).items():
            for method in operations:
                st.code(f"{method.upper()} {path}", language=None)
    else:
        st.caption("Could not load /openapi.json")

    st.divider()
    st.caption("Docs: `/docs` on the API host")


tab_execute, tab_stream, tab_chat, tab_endpoints = st.tabs(
    ["⚡ JARVIS Execute", "🌊 SSE Stream", "💬 OpenAI Chat", "🧭 Endpoints"]
)


# --------------------------------------------------------------------------- #
# Tab 1 — native JARVIS route
# --------------------------------------------------------------------------- #
with tab_execute:
    st.markdown("`POST /api/v1/jarvis/execute`")

    with st.form("execute_form"):
        col1, col2, col3 = st.columns(3)
        with col1:
            role = st.selectbox("Role", ROLES, index=2)
        with col2:
            branch_id = st.number_input("Branch ID", min_value=0, value=8, step=1)
        with col3:
            use_table = st.checkbox("Send table_session_id", value=False)
            table_session_id = (
                int(st.number_input("Table session ID", min_value=0, value=42, step=1))
                if use_table
                else None
            )

        user_query = st.text_area(
            "User query",
            value="Analyze current operational state and give me next-best-actions.",
            height=80,
        )
        context_raw = st.text_area(
            "Context payload (JSON)",
            value=json.dumps(DEFAULT_CONTEXT, indent=2),
            height=200,
        )
        submitted = st.form_submit_button("Run JARVIS", type="primary", width="stretch")

    if submitted:
        context_payload = parse_context(context_raw)
        if context_payload is not None:
            payload = {
                "role": role,
                "branch_id": int(branch_id),
                "table_session_id": table_session_id,
                "user_query": user_query,
                "context_payload": context_payload,
            }
            with st.spinner("Calling JARVIS..."):
                status, body = call("POST", "/api/v1/jarvis/execute", payload)

            st.session_state["last_execute"] = (status, payload, body)

    if "last_execute" in st.session_state:
        status, sent, body = st.session_state["last_execute"]
        if status is None:
            st.error(f"Request failed: {body}")
        elif status != 200:
            st.error(f"HTTP {status}")
            st.json(body)
        else:
            left, right = st.columns([3, 1])
            with left:
                st.info(body.get("summary", ""))
            with right:
                st.metric("Model", body.get("model", "-"))
                st.metric("Recommendations", len(body.get("recommendations", [])))

            render_recommendations(body.get("recommendations", []))

            with st.expander("Raw tool observations"):
                st.json(body.get("tool_observations", {}))
            with st.expander("Request sent"):
                st.json(sent)


# --------------------------------------------------------------------------- #
# Tab 2 — SSE Stream route
# --------------------------------------------------------------------------- #
with tab_stream:
    st.markdown("`POST /api/v1/jarvis/stream` (Server-Sent Events)")

    col1, col2 = st.columns(2)
    with col1:
        s_role = st.selectbox("Role", ROLES, index=2, key="s_role")
    with col2:
        s_branch = st.number_input("Branch ID", min_value=1, value=8, step=1, key="s_branch")

    s_query = st.text_input("User query", value="Stream live operational brief.", key="s_query")

    if st.button("Start SSE Stream", type="primary"):
        url = f"{base_url()}/api/v1/jarvis/stream"
        headers = get_auth_headers()
        payload = {
            "role": s_role,
            "branch_id": int(s_branch),
            "user_query": s_query,
            "context_payload": {},
        }

        container = st.empty()
        events_log = []
        try:
            with httpx.stream("POST", url, headers=headers, json=payload, timeout=30.0) as response:
                if response.status_code != 200:
                    st.error(f"HTTP {response.status_code}: {response.text}")
                else:
                    for line in response.iter_lines():
                        if line.startswith("data: "):
                            raw_data = line[6:]
                            if raw_data == "[DONE]":
                                events_log.append("✅ Stream Finished ([DONE])")
                                break
                            try:
                                evt = json.loads(raw_data)
                                events_log.append(f"Event `{evt.get('event')}`: {json.dumps(evt)}")
                            except Exception:
                                events_log.append(raw_data)
                            container.code("\n".join(events_log), language="json")
        except Exception as exc:
            st.error(f"Stream error: {exc}")


# --------------------------------------------------------------------------- #
# Tab 3 — OpenAI-compatible chat
# --------------------------------------------------------------------------- #
with tab_chat:
    st.markdown("`POST /v1/chat/completions`")

    model_status, models = call("GET", "/v1/models", timeout=10)
    model_options = (
        [m.get("id") for m in models.get("data", [])]
        if model_status == 200 and isinstance(models, dict)
        else []
    )

    col1, col2, col3 = st.columns(3)
    with col1:
        chat_model = st.selectbox("Model", model_options or ["(default)"])
    with col2:
        chat_role = st.selectbox("JARVIS role", ROLES, index=0, key="chat_role")
    with col3:
        chat_branch = st.number_input("Branch ID", min_value=0, value=8, step=1, key="chat_branch")

    with st.expander("Context payload (JSON)"):
        chat_context_raw = st.text_area(
            "context_payload",
            value=json.dumps(DEFAULT_CONTEXT, indent=2),
            height=200,
            key="chat_context",
        )

    if st.button("Clear conversation"):
        st.session_state["chat"] = []
    st.session_state.setdefault("chat", [])

    for message in st.session_state["chat"]:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    if user_input := st.chat_input("Ask JARVIS..."):
        context_payload = parse_context(chat_context_raw) or {}
        st.session_state["chat"].append({"role": "user", "content": user_input})
        with st.chat_message("user"):
            st.markdown(user_input)

        request_body = {
            "messages": st.session_state["chat"],
            "role": chat_role,
            "branch_id": int(chat_branch),
            "context_payload": context_payload,
        }
        if model_options:
            request_body["model"] = chat_model

        with st.spinner("Thinking..."):
            status, body = call("POST", "/v1/chat/completions", request_body)

        if status == 200:
            content = body["choices"][0]["message"]["content"]
            st.session_state["chat"].append({"role": "assistant", "content": content})
            with st.chat_message("assistant"):
                st.markdown(content)
                st.caption(
                    f"model: {body.get('model', '-')} · "
                    f"tokens: {body.get('usage', {}).get('total_tokens', '-')}"
                )
        else:
            st.error(f"HTTP {status}")
            st.json(body)


# --------------------------------------------------------------------------- #
# Tab 4 — route reference
# --------------------------------------------------------------------------- #
with tab_endpoints:
    if spec_status == 200 and isinstance(spec, dict):
        rows = [
            {"method": method.upper(), "path": path, "summary": operations[method].get("summary", "")}
            for path, operations in spec.get("paths", {}).items()
            for method in operations
        ]
        st.dataframe(rows, width="stretch", hide_index=True)
        with st.expander("OpenAPI schema (raw)"):
            st.json(spec)
    else:
        st.warning("Could not load the OpenAPI schema from the API host.")

    st.divider()
    st.markdown("`GET /v1/models`")
    st.json(models if model_status == 200 else {"status": model_status, "body": models})
