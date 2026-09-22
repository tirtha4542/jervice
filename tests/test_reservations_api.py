from fastapi.testclient import TestClient

from app.core.config import settings
from app.api.reservations import ReservationOut
from app.domain.state_machines import (
    InvalidStateTransition,
    ReservationStatus,
    apply_transition,
)
from app.main import app

client = TestClient(app)


def test_openapi_lists_reservation_routes():
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/v1/reservations" in paths
    assert set(paths["/api/v1/reservations"]) == {"get", "post"}
    assert "/api/v1/reservations/{reservation_id}/status" in paths
    assert set(paths["/api/v1/reservations/{reservation_id}/status"]) == {"patch"}
    assert "/api/v1/demo/seed" in paths


def test_create_reservation_validates_payload_before_touching_db():
    # Missing branch_id / guest_name / start_at → 422 from pydantic, no DB needed.
    response = client.post("/api/v1/reservations", json={"party_size": 2})
    assert response.status_code == 422
    fields = {err["loc"][-1] for err in response.json()["detail"]}
    assert {"branch_id", "guest_name", "start_at"} <= fields


def test_list_reservations_requires_branch_id():
    assert client.get("/api/v1/reservations").status_code == 422


def test_list_rejects_unknown_status():
    response = client.get("/api/v1/reservations", params={"branch_id": 1, "status": "bogus"})
    assert response.status_code == 422


def test_from_tool_maps_jarvis_keys_to_rest_shape():
    """Guards the id/status mismatch that made GET /api/v1/reservations return 500."""
    from app.api.reservations import _from_tool

    mapped = _from_tool(
        {
            "reservation_id": 5,
            "branch_id": 2,
            "table_id": None,
            "guest_name": "Amina",
            "party_size": 2,
            "reservation_status": "requested",
            "start_at": "2026-09-25T19:00:00",
        }
    )
    assert mapped["id"] == 5
    assert mapped["status"] == "requested"
    assert "reservation_id" not in mapped
    assert ReservationOut.model_validate(mapped).id == 5


def test_demo_seed_blocked_in_production(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    response = client.post("/api/v1/demo/seed")
    assert response.status_code == 403
    assert "production" in response.json()["detail"].lower()


def test_reservation_lifecycle_follows_state_machine():
    assert (
        apply_transition(ReservationStatus, ReservationStatus.REQUESTED, ReservationStatus.CONFIRMED)
        == ReservationStatus.CONFIRMED
    )
    assert (
        apply_transition(
            ReservationStatus, ReservationStatus.CONFIRMED, ReservationStatus.SEATED
        )
        == ReservationStatus.SEATED
    )


def test_terminal_and_illegal_reservation_transitions_rejected():
    for current, target in [
        (ReservationStatus.REQUESTED, ReservationStatus.SEATED),  # skips confirm
        (ReservationStatus.CONFIRMED, ReservationStatus.REQUESTED),  # backwards
        (ReservationStatus.CANCELLED, ReservationStatus.CONFIRMED),  # terminal
        (ReservationStatus.COMPLETED, ReservationStatus.SEATED),  # terminal
    ]:
        try:
            apply_transition(ReservationStatus, current, target)
            raise AssertionError(f"expected {current} -> {target} to be rejected")
        except InvalidStateTransition:
            pass
