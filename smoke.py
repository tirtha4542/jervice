"""End-to-end smoke test against the running API (uvicorn on :8000).

Usage:
    python smoke.py

Exercises every router against the real seeded database and prints the data
each endpoint returns, so a broken route or an empty table is obvious.
"""

from __future__ import annotations

import json
import sys

import httpx

from app.core.auth import create_access_token

BASE = "http://127.0.0.1:8000"
BRANCH = 8
TOKEN = create_access_token({"sub": 1, "org_id": 1, "branch_id": BRANCH, "role": "manager"})

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, response: httpx.Response, expect: int = 200) -> object | None:
    ok = response.status_code == expect
    results.append((PASS if ok else FAIL, name, f"HTTP {response.status_code}"))
    if not ok:
        print(f"[FAIL] {name}: expected {expect}, got {response.status_code}")
        print("       " + response.text[:500])
        return None
    try:
        return response.json()
    except ValueError:
        return None


def show(name: str, data, limit: int = 3) -> None:
    rendered = json.dumps(data, default=str)
    if len(rendered) > 400:
        rendered = rendered[:400] + " ..."
    print(f"  {name}: {rendered}")


def main() -> int:
    with httpx.Client(
        base_url=BASE,
        timeout=30.0,
        headers={"Authorization": f"Bearer {TOKEN}"},
    ) as client:
        check("GET /health", client.get("/health"))
        check("GET /", client.get("/"))

        # Org chart
        hierarchy = check("GET /api/v1/org/hierarchy", client.get("/api/v1/org/hierarchy"))
        if hierarchy:
            show("hierarchy", hierarchy)
        branches = check("GET /api/v1/org/branches", client.get("/api/v1/org/branches"))
        if branches:
            show("branches", branches)
        roles = check("GET /api/v1/org/roles", client.get("/api/v1/org/roles"))
        if roles:
            show("roles", [r["name"] for r in roles])
        employees = check(
            "GET /api/v1/org/employees",
            client.get("/api/v1/org/employees", params={"branch_id": BRANCH}),
        )
        if employees:
            show("employees", [e["full_name"] for e in employees])

        # Catalog
        menu = check("GET /api/v1/menu", client.get("/api/v1/menu", params={"branch_id": BRANCH}))
        if menu:
            show("menu", [f"{m['name']} {m['price']}" for m in menu])
        inventory = check(
            "GET /api/v1/inventory",
            client.get("/api/v1/inventory", params={"branch_id": BRANCH}),
        )
        if inventory:
            low = [s["sku_code"] for s in inventory if s["below_par"]]
            show("inventory below par", low)
        if menu:
            recipe = check(
                "GET /api/v1/recipes/{id}",
                client.get(f"/api/v1/recipes/{menu[0]['id']}"),
            )
            if recipe:
                show("recipe", recipe)

        # Tables / sessions / guests
        tables = check("GET /api/v1/tables", client.get("/api/v1/tables", params={"branch_id": BRANCH}))
        if tables:
            show("tables", [(t["code"], t["status"], t["active_session_id"]) for t in tables])
        sessions = check(
            "GET /api/v1/table-sessions",
            client.get("/api/v1/table-sessions", params={"branch_id": BRANCH}),
        )
        if sessions:
            show("sessions", [(s["id"], s["status"]) for s in sessions])

        session_id = sessions[0]["id"] if sessions else 1
        detail = check(
            "GET /api/v1/table-sessions/{id}",
            client.get(f"/api/v1/table-sessions/{session_id}"),
        )
        if detail:
            show("session detail", detail)

        guests = check(
            "GET /api/v1/guest-sessions",
            client.get("/api/v1/guest-sessions", params={"table_session_id": session_id}),
        )
        if guests:
            show("guests", [(g["display_name"], g["status"]) for g in guests])

        # Multi-guest QR join (the "extra join is not an error" rule)
        join = check(
            "POST /api/v1/table-sessions/{id}/guests",
            client.post(
                f"/api/v1/table-sessions/{session_id}/guests",
                json={
                    "display_name": "Smoke Tester",
                    "otp": "123456",
                    "test_qr_verified": True,
                },
            ),
            expect=201,
        )
        if join:
            show("joined guest", join)

        # Orders / kitchen / payments
        orders = check("GET /api/v1/orders", client.get("/api/v1/orders", params={"branch_id": BRANCH}))
        if orders:
            show("orders", [(o["id"], o["status"], str(o["total"])) for o in orders])
        queues = check(
            "GET /api/v1/kitchen/queues",
            client.get("/api/v1/kitchen/queues", params={"branch_id": BRANCH}),
        )
        if queues:
            show("queues", queues)
        if orders:
            order_id = orders[0]["id"]
            payments = check(
                "GET /api/v1/payments",
                client.get("/api/v1/payments", params={"table_session_id": session_id}),
            )
            if payments:
                show("payments", [(p["id"], p["status"], str(p["amount"])) for p in payments])

            # Illegal transition must 409
            check(
                "PATCH /orders/{id}/status (illegal → 409)",
                client.patch(f"/api/v1/orders/{order_id}/status", json={"status": "served"}),
                expect=409,
            )

        # Reservations
        reservations = check(
            "GET /api/v1/reservations",
            client.get("/api/v1/reservations", params={"branch_id": BRANCH}),
        )
        if reservations:
            show("reservations", [(r["id"], r["guest_name"], r["status"]) for r in reservations])

        # Dashboard
        dashboard = check(
            "GET /api/v1/dashboard/{branch_id}",
            client.get(f"/api/v1/dashboard/{BRANCH}"),
        )
        if dashboard:
            show("dashboard.floor", dashboard.get("floor"))
            show("dashboard.kitchen", dashboard.get("kitchen"))
            show("dashboard.money", dashboard.get("money"))
            show("dashboard.inventory", dashboard.get("inventory"))

        # JARVIS (falls back to heuristic if Groq is unreachable)
        jarvis = check(
            "POST /api/v1/jarvis/execute",
            client.post(
                "/api/v1/jarvis/execute",
                json={
                    "role": "manager",
                    "branch_id": BRANCH,
                    "table_session_id": session_id,
                    "user_query": "Analyze current operational state and give next-best-actions.",
                },
            ),
        )
        if jarvis:
            show("jarvis.summary", jarvis.get("summary"))
            show("jarvis.recommendations", [r["title"] for r in jarvis.get("recommendations", [])])

    print("\n" + "=" * 64)
    passed = sum(1 for status, _, _ in results if status == PASS)
    failed = len(results) - passed
    for status, name, detail in results:
        print(f"[{status}] {name} -> {detail}")
    print("=" * 64)
    print(f"{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
