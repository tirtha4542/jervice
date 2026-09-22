# Tavonza AI

Multi-tenant hospitality operating system. JARVIS is the intelligence and orchestration layer: it consumes live operational events and emits role-specific next-best-actions.

## Hierarchy

Organization → Brand → Branch → Department/Station → Employees / Roles / Permissions

## Independent state machines

Do not store a single "status" for a table visit. These cycles are independent:

| Machine | Purpose |
| --- | --- |
| Table Status | Physical table: available, reserved, seated, dirty, blocked |
| Table Session Status | Shared service visit that many guests can join |
| Guest Session Status | Per-guest QR + OTP join, order, settle, leave |
| Order Status | Ticket lifecycle |
| Order Item Status | Station-level prep |
| Payment Status | Per-guest or shared settlement |
| Reservation Status | Booking lifecycle |

Multiple guests may scan the same QR, authenticate, join one table session, order individually or together, and split bills. Extra joins are **not** a "table occupied" error.

## Stack

- FastAPI
- PostgreSQL (SQLAlchemy async)
- LangGraph + Groq (`openai/gpt-oss-120b`, set via `GROQ_MODEL`)

## Database

All 17 tables in the `public` schema are mapped by `app/models.py` and used by the API:

| Domain | Tables | Router |
| --- | --- | --- |
| Tenant hierarchy | `organizations`, `brands`, `branches`, `departments`, `roles`, `employees` | `app/api/orgchart.py` |
| Menu & stock | `menu_items`, `inventory_skus`, `recipe_components` | `app/api/catalog.py` |
| Floor & service | `dining_tables`, `table_sessions`, `guest_sessions` | `app/api/tables.py` |
| Ticketing & money | `orders`, `order_items`, `payments` | `app/api/orders.py` |
| Bookings | `reservations` | `app/api/reservations.py` |
| Audit trail | `audit_logs` | `app/core/audit.py` (written by mutating flows) |

### Connect and verify

```bash
python check_db.py     # parses DATABASE_URL, opens a real connection, lists tables
python inspect_db.py   # dumps row counts + sample rows from every table
```

### Seed the data (posts into the database)

```bash
python seed_db.py      # idempotent — safe to re-run, never duplicates or rewinds rows
```

Equivalent over HTTP: `POST /api/v1/demo/seed` (403 in production).

Seeded state for branch **8 (HQ)**:

- 4 departments, 6 roles, 7 employees
- 15 inventory SKUs, 6 menu items, 16 recipe components (TRUFFLE-OIL at zero → critical alert, PARMESAN below par → warning)
- Table session 1 on T1: 3 guests joined, order in the kitchen, $68.75 unpaid
- Table session 2 on T2: closed, settled ($38.75) — proves the full lifecycle
- 5 reservations, 3 audit log entries

### Verify every endpoint end-to-end

```bash
uvicorn app.main:app --reload   # terminal 1
python smoke.py                 # terminal 2 — 21 checks against live data
```

## API surface (Swagger: http://localhost:8000/docs)

### Tenant & catalog

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/v1/org/hierarchy` | Full org → brand → branch → department tree |
| `GET` | `/api/v1/org/branches` | Branches with departments |
| `GET` | `/api/v1/org/roles` | Roles + permission lists |
| `GET` | `/api/v1/org/employees?branch_id=8` | Employees with role/department names |
| `GET` | `/api/v1/menu?branch_id=8` | Menu + recipe BOM + stock coverage |
| `POST` | `/api/v1/menu` | Create menu item with recipe lines |
| `GET` | `/api/v1/inventory?branch_id=8` | SKUs with par-level variance flags |
| `PATCH` | `/api/v1/inventory/{sku_id}/on-hand` | Set absolute on-hand (goods-in/waste) |
| `GET` | `/api/v1/recipes/{menu_item_id}` | BOM annotated with `can_make_one` |

### Floor & service (three independent machines)

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/v1/tables?branch_id=8` | Physical tables + active session id |
| `POST` | `/api/v1/tables` | Add a table |
| `PATCH` | `/api/v1/tables/{id}/status` | Physical lifecycle; illegal jumps → **409** |
| `POST` | `/api/v1/tables/{id}/open-session` | Seat a party; 409 if a session is already open |
| `GET` | `/api/v1/table-sessions?branch_id=8` | Service visits |
| `GET` | `/api/v1/table-sessions/{id}` | Live state + guests + payment splits |
| `POST` | `/api/v1/table-sessions/{id}/guests` | QR + OTP join — extra joins always allowed |
| `PATCH` | `/api/v1/table-sessions/{id}/status` | Service lifecycle; closing marks the table dirty |
| `GET` | `/api/v1/guest-sessions?table_session_id=1` | Per-guest sessions |
| `PATCH` | `/api/v1/guest-sessions/{id}/status` | Guest lifecycle; illegal jumps → **409** |

### Orders, kitchen & payments

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/v1/orders?branch_id=8` | Active orders with items and totals |
| `POST` | `/api/v1/orders` | Draft order (shared or per-guest) with line items |
| `GET` | `/api/v1/orders/{id}` | One order |
| `POST` | `/api/v1/orders/{id}/items` | Add a line; 409 once served/cancelled |
| `PATCH` | `/api/v1/orders/{id}/status` | Ticket lifecycle; illegal jumps → **409** |
| `PATCH` | `/api/v1/order-items/{id}/status` | Station prep: queued → prepping → fired → ready → picked_up |
| `GET` | `/api/v1/kitchen/queues?branch_id=8` | Live tickets grouped by station |
| `POST` | `/api/v1/payments` | Post a payment (per-guest or shared) |
| `GET` | `/api/v1/payments?table_session_id=1` | Split-bill view |
| `PATCH` | `/api/v1/payments/{id}/status` | Settle/refund; unpaid → settled is **409** |

### Bookings, dashboard & AI

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/v1/reservations?branch_id=8` | List, optional `?status=` filter |
| `POST` | `/api/v1/reservations` | Create — status always starts `requested` |
| `PATCH` | `/api/v1/reservations/{id}/status` | Lifecycle move; illegal jumps → **409** |
| `GET` | `/api/v1/dashboard/{branch_id}` | Floor, kitchen, money, stock, audit — one payload |
| `POST` | `/api/v1/jarvis/execute` | JARVIS briefing with live tool context |
| `GET` | `/v1/models` / `POST` | `/v1/chat/completions` | OpenAI-compatible facade |
| `POST` | `/api/v1/demo/seed` | Dev-only full seed (403 in production) |

Try-it-out order: `POST /api/v1/demo/seed` → `GET /api/v1/dashboard/8` → `GET /api/v1/menu?branch_id=8` → `POST /api/v1/jarvis/execute`. Example 409:

```
OrderStatus: draft -> ready is not allowed
- allowed from 'draft': ['cancelled', 'submitted']
```

`GET` reads share the same query tools that feed JARVIS, so the AI and the REST API always see identical data.

## Run

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
# set DATABASE_URL and GROQ_API_KEY
python seed_db.py                # post the demo dataset
uvicorn app.main:app --reload
```

Health: `GET /health`

## Tests

```bash
pytest                           # 32 tests — state machines + route contracts, no DB needed
```

With the API running, `python smoke.py` adds 21 live checks that read and write the real database.

## Test UI (Streamlit)

```bash
uvicorn app.main:app --reload          # terminal 1 — API on :8000
streamlit run ui.py                    # terminal 2 — UI on :8501
```

Open http://localhost:8501 — three tabs:

| Tab | Exercises |
| --- | --- |
| ⚡ JARVIS Execute | `POST /api/v1/jarvis/execute` with role / branch / context JSON |
| 💬 OpenAI Chat | `GET /v1/models` + `POST /v1/chat/completions` |
| 🧭 Endpoints | live OpenAPI route list pulled from `/openapi.json` |

The sidebar polls `/health` and shows the route table, so a dead API is obvious immediately.

## Scripts

| Script | Purpose |
| --- | --- |
| `python check_db.py` | Validate `DATABASE_URL` and confirm the schema exists |
| `python inspect_db.py` | Print row counts + sample rows for all 17 tables |
| `python seed_db.py` | Post the idempotent demo dataset |
| `python smoke.py` | 21 end-to-end checks against a running API |
| `pytest` | Unit + contract tests |
