"""Contract tests for the new domain routers.

Validation and state-machine tests run without a database (consistent with the
existing suite); the DB-dependent paths are exercised against the live server
via smoke.py.
"""

from fastapi.testclient import TestClient

from app.domain.state_machines import (
    GuestSessionStatus,
    InvalidStateTransition,
    OrderItemStatus,
    OrderStatus,
    PaymentStatus,
    TableSessionStatus,
    TableStatus,
    apply_transition,
)
from app.core.auth import create_access_token
from app.main import app

client = TestClient(
    app,
    headers={"Authorization": f"Bearer {create_access_token({'sub': 1, 'role': 'manager', 'branch_id': 8})}"},
)


# --------------------------------------------------------------------------- #
# OpenAPI surface
# --------------------------------------------------------------------------- #
def test_openapi_lists_all_new_routers():
    paths = client.get("/openapi.json").json()["paths"]

    for path in [
        "/api/v1/org/hierarchy",
        "/api/v1/org/branches",
        "/api/v1/org/roles",
        "/api/v1/org/employees",
        "/api/v1/menu",
        "/api/v1/inventory",
        "/api/v1/inventory/{sku_id}/on-hand",
        "/api/v1/recipes/{menu_item_id}",
        "/api/v1/tables",
        "/api/v1/tables/{table_id}/status",
        "/api/v1/tables/{table_id}/open-session",
        "/api/v1/table-sessions",
        "/api/v1/table-sessions/{table_session_id}/guests",
        "/api/v1/table-sessions/{table_session_id}/status",
        "/api/v1/guest-sessions",
        "/api/v1/guest-sessions/{guest_id}/status",
        "/api/v1/orders",
        "/api/v1/orders/{order_id}",
        "/api/v1/orders/{order_id}/items",
        "/api/v1/orders/{order_id}/status",
        "/api/v1/order-items/{item_id}/status",
        "/api/v1/kitchen/queues",
        "/api/v1/payments",
        "/api/v1/payments/{payment_id}/status",
        "/api/v1/dashboard/{branch_id}",
    ]:
        assert path in paths, f"missing route {path}"


def test_root_advertises_every_route_group():
    body = client.get("/").json()
    for key in ["org_routes", "catalog_routes", "table_routes", "order_routes", "dashboard_routes"]:
        assert key in body, f"root payload missing {key}"


# --------------------------------------------------------------------------- #
# Pydantic validation happens before any DB touch
# --------------------------------------------------------------------------- #
def test_menu_requires_branch_and_price():
    response = client.post("/api/v1/menu", json={"name": "X"})
    assert response.status_code == 422
    fields = {err["loc"][-1] for err in response.json()["detail"]}
    assert {"branch_id", "price"} <= fields


def test_menu_rejects_non_positive_price():
    response = client.post(
        "/api/v1/menu",
        json={"branch_id": 8, "name": "X", "price": "-1.00"},
    )
    assert response.status_code == 422


def test_table_requires_branch_and_code():
    response = client.post("/api/v1/tables", json={"capacity": 4})
    assert response.status_code == 422
    fields = {err["loc"][-1] for err in response.json()["detail"]}
    assert {"branch_id", "code"} <= fields


def test_order_requires_table_session():
    response = client.post("/api/v1/orders", json={"is_shared": True})
    assert response.status_code == 422
    assert "table_session_id" in {err["loc"][-1] for err in response.json()["detail"]}


def test_payment_amount_must_be_positive():
    response = client.post(
        "/api/v1/payments", json={"order_id": 1, "amount": "0.00"}
    )
    assert response.status_code == 422


def test_guest_join_requires_otp():
    response = client.post(
        "/api/v1/table-sessions/1/guests", json={"display_name": "Nadia"}
    )
    assert response.status_code == 422
    assert "otp" in {err["loc"][-1] for err in response.json()["detail"]}


def test_list_routes_require_branch_or_session_scope():
    assert client.get("/api/v1/menu").status_code == 422
    assert client.get("/api/v1/tables").status_code == 422
    assert client.get("/api/v1/orders").status_code == 422
    assert client.get("/api/v1/payments").status_code == 422
    assert client.get("/api/v1/guest-sessions").status_code == 422


def test_status_endpoints_reject_unknown_states():
    assert client.patch("/api/v1/orders/1/status", json={"status": "bogus"}).status_code in (404, 409)
    # Table status is a Literal union, so pydantic rejects it earlier (422).
    assert (
        client.patch("/api/v1/tables/1/status", json={"status": "bogus"}).status_code == 422
    )


# --------------------------------------------------------------------------- #
# State machines — the real rules, no DB
# --------------------------------------------------------------------------- #
def test_order_lifecycle_follows_state_machine():
    assert (
        apply_transition(OrderStatus, OrderStatus.DRAFT, OrderStatus.SUBMITTED)
        == OrderStatus.SUBMITTED
    )
    assert (
        apply_transition(OrderStatus, OrderStatus.SUBMITTED, OrderStatus.IN_KITCHEN)
        == OrderStatus.IN_KITCHEN
    )
    assert (
        apply_transition(OrderStatus, OrderStatus.IN_KITCHEN, OrderStatus.READY)
        == OrderStatus.READY
    )
    assert apply_transition(OrderStatus, OrderStatus.READY, OrderStatus.SERVED) == OrderStatus.SERVED


def test_order_illegal_jumps_rejected():
    for current, target in [
        (OrderStatus.DRAFT, OrderStatus.READY),        # skips submitted + kitchen
        (OrderStatus.SERVED, OrderStatus.CANCELLED),   # terminal
        (OrderStatus.CANCELLED, OrderStatus.SUBMITTED),  # terminal
    ]:
        try:
            apply_transition(OrderStatus, current, target)
            raise AssertionError(f"expected {current} -> {target} to be rejected")
        except InvalidStateTransition:
            pass


def test_order_item_terminal_states():
    assert (
        apply_transition(OrderItemStatus, OrderItemStatus.PREPPING, OrderItemStatus.VOIDED)
        == OrderItemStatus.VOIDED
    )
    for current, target in [
        (OrderItemStatus.QUEUED, OrderItemStatus.READY),      # skips prepping + fired
        (OrderItemStatus.PICKED_UP, OrderItemStatus.QUEUED),  # terminal
    ]:
        try:
            apply_transition(OrderItemStatus, current, target)
            raise AssertionError(f"expected {current} -> {target} to be rejected")
        except InvalidStateTransition:
            pass


def test_payment_settlement_rules():
    assert (
        apply_transition(PaymentStatus, PaymentStatus.UNPAID, PaymentStatus.AUTHORIZED)
        == PaymentStatus.AUTHORIZED
    )
    assert (
        apply_transition(PaymentStatus, PaymentStatus.AUTHORIZED, PaymentStatus.SETTLED)
        == PaymentStatus.SETTLED
    )
    # unpaid cannot jump straight to settled
    try:
        apply_transition(PaymentStatus, PaymentStatus.UNPAID, PaymentStatus.SETTLED)
        raise AssertionError("unpaid -> settled must be rejected")
    except InvalidStateTransition:
        pass


def test_table_session_close_is_terminal_and_releases_table():
    assert (
        apply_transition(
            TableSessionStatus, TableSessionStatus.SETTLING, TableSessionStatus.CLOSED
        )
        == TableSessionStatus.CLOSED
    )
    try:
        apply_transition(TableSessionStatus, TableSessionStatus.CLOSED, TableSessionStatus.OPEN)
        raise AssertionError("closed -> open must be rejected")
    except InvalidStateTransition:
        pass
    # physical table goes seated -> dirty when the visit closes
    assert apply_transition(TableStatus, TableStatus.SEATED, TableStatus.DIRTY) == TableStatus.DIRTY


def test_multi_guest_join_is_not_an_error_in_the_model():
    """Guest sessions are per-guest; joining an open session is always allowed
    at the model level (the router enforces only that the session isn't closed)."""
    assert (
        apply_transition(
            GuestSessionStatus,
            GuestSessionStatus.AUTHENTICATING,
            GuestSessionStatus.JOINED,
        )
        == GuestSessionStatus.JOINED
    )
