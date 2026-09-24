# ARCHITECTURE.md

> Multi-tenant, scope-based restaurant operations platform.
> API-first, with an AI tool layer sitting above the same
> authorization/business layer as every other client.

See `RULES.md` for the non-negotiable constraints this design protects,
and `STATUS.md` for what is actually implemented today.

---

## 1. Mental model

```
                         PLATFORM OWNER
                               │
                       Platform Control
                               │
                    ┌──────────▼──────────┐
                    │     SAAS PLATFORM   │
                    │                     │
                    │ Tenant Management   │
                    │ Billing/Subscription│
                    │ Global Configuration│
                    │ Platform Admin      │
                    └──────────┬──────────┘
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
       Organization A   Organization B   Organization C
              │
       ┌──────┼──────┐
       ▼      ▼      ▼
  Restaurant1 Restaurant2 Restaurant3
       │
   ┌───┼────────┐
   ▼            ▼
 Branch1      Branch2
   │
   ├── Manager
   ├── Waiter
   ├── Cashier
   ├── Bartender
   ├── Kitchen Staff
   └── Other Staff
```

**Key distinction:** Organization → Restaurant → Branch is the *business*
hierarchy. Permission/scope is an *independent* authorization hierarchy.
The authorization system does not depend directly on the org tree.

---

## 2. Domain hierarchy

```
Platform
│
├── Organization
│    ├── Restaurant
│    │    ├── Branch
│    │    │    ├── Areas
│    │    │    ├── Tables
│    │    │    ├── Menus
│    │    │    ├── Orders
│    │    │    ├── Kitchen
│    │    │    ├── Payments
│    │    │    └── Staff
│    │    └── ...
│    └── ...
│
├── Users
├── Subscriptions
├── Billing
└── Platform Configuration
```

**Branch is the operational boundary.** A Restaurant is a business
grouping; a Branch is where operations actually happen.

```
Restaurant → Branch → Tables → Sessions → Orders → Order Items → Kitchen → Payment
```

### Organization
Represents the customer's company/business account (e.g. *ABC Hospitality
Ltd.*). Owns one or more Restaurants. Org-level actors: Owner, Org Admin,
Finance, Operations, Organization AI.

### Restaurant
Represents a brand (e.g. *Burger House*), which can have several Branches
(*Dhaka*, *Chittagong*, *Sylhet*).

### Branch
An actual physical location — tables, QR codes, waiters, kitchen, cashier,
orders, payments, inventory (later), real-time activity.

---

## 3. Authorization model

### 3.1 Users are not their role

Don't model authorization as:

```
User → role = WAITER
```

Model it as:

```
User → Membership/Assignment → Scope + Permissions + Responsibilities
```

Example:

```
Sabbir
 └── Assignment
      ├── Organization: ABC
      ├── Restaurant: Burger House
      ├── Branch: Dhanmondi
      ├── Permissions: orders.read, orders.accept, orders.reject,
      │                orders.serve, tables.read
      └── Assigned Tables: T1, T2, T5
```

The UI may still display "Waiter" — internally, permissions and scopes
are authoritative.

### 3.2 Permission + Scope, not Role alone

Permissions (examples):

```
orders.read / orders.accept / orders.reject / orders.update / orders.serve
tables.read / tables.update
payments.read / payments.create / payments.refund
menu.read / menu.update
staff.read / staff.manage
reports.read
```

Scope determines *where* a permission applies:

- `orders.read` @ scope `Organization ABC` → read orders across the org
- `orders.read` @ scope `Branch Dhanmondi` → read orders only in that branch
- `orders.read` @ scope `Tables [T1, T2, T5]` → read orders only for those
  tables (exactly what a waiter AI needs)

### 3.3 Roles are permission bundles

Roles still exist as convenience templates, e.g.:

```
Role: WAITER
  tables.read, orders.read, orders.accept, orders.reject,
  orders.serve, session.read

Role: KITCHEN_STAFF
  orders.read, kitchen.queue.read, order_items.read,
  order_items.start, order_items.complete
```

But the system always evaluates the general question:

> Does this actor have permission X, within scope Y, for resource Z?

### 3.4 Authorization engine flow

```
Request
  → Authentication
  → Identify Actor
  → Determine Resource
  → Check Permission
  → Check Scope
  → Check Resource Relationship
  → Allow / Deny
```

Example — `POST /orders/123/serve`:

1. Who is requesting? Do they have `orders.serve`?
2. Does their scope include `Branch #12`?
3. Does Order #123 belong to `Branch #12`?
4. Is the order currently `READY`?
5. Only then → `ALLOW`.

This one engine is used by **every** client type — web, mobile, and AI.

---

## 4. AI architecture

### 4.1 Same security boundary as humans

```
Human path:  Waiter → API → Authorization → Business Logic
AI path:     Waiter AI → Tool → API/Command → Authorization → Business Logic
```

### 4.2 AI never touches the database

Do **not** build:

```
AI → SQL → Database
```

Build:

```
AI → AI Tool Layer → Application Commands/APIs → Authorization
  → Domain Services → Database
```

Example: *"Mark T5 burger as ready"* resolves to tool
`complete_order_item({ orderItemId: "oi_123", status: "READY" })`, and the
application — not the AI — decides whether that's actually allowed.

### 4.3 AI Tool Layer

```
AI Gateway
 ├── Context Resolver
 ├── Tool Registry
 ├── Permission Resolver
 ├── Tool Executor
 └── Audit Logger
```

Example tools: `get_table_status`, `get_order`, `get_order_queue`,
`accept_order`, `reject_order`, `start_order_item`, `complete_order_item`,
`serve_order`, `get_customer_session`, `get_branch_summary`,
`get_sales_summary`, `get_staff_status`.

**Tools never decide authorization themselves** — they call through the
same engine as §3.4.

### 4.4 AI identity and scope

Every AI agent has an actor identity and a bounded scope, e.g.:

```
Waiter AI
  Actor: AI_AGENT
  Owner: User #123
  Identity: Waiter AI
  Organization: ABC / Restaurant: Burger House / Branch: Dhanmondi
  Assigned tables: T1, T2, T5
  Permissions: orders.read, orders.serve, tables.read
```

"What's happening at T5?" → allowed.
"Show me total revenue across all branches." → denied (out of scope).

Organization AI has a wider scope (e.g. `restaurants.read`,
`branches.read`, `reports.read`, `orders.read`, `staff.read`,
`analytics.read`) but a Branch Manager AI is still confined to its branch.

### 4.5 One runtime, many identities

Don't stand up separate servers per agent type. Use one Agent Runtime; the
**identity and scope** passed in determine what any given invocation can
see or do:

```
AI Platform → Agent Runtime → Context + Identity → Authorization → Tools
```

The same runtime can serve Organization AI, Branch AI, Waiter AI, and
Kitchen AI with different context and permissions per call.

### 4.6 AI context building

```
Raw Platform Data → Domain Events → Context Builder
  → Role/Scope Context → AI
```

Waiter context: current branch, assigned tables, active sessions, pending
orders, delayed orders, kitchen status, recent events.
Organization AI context: organizations, restaurants, branches, sales,
orders, staff, analytics, operational metrics. Different context, same
platform.

### 4.7 AI reasons over tool results, not raw data

User: *"Why is T5 taking so long?"*
AI calls `get_table_session(T5)` → `get_order_status(orderId)` →
`get_kitchen_queue(branchId)` → `get_order_timeline(orderId)`, then
reasons over the results — e.g. *"T5's fries are still preparing; 6 active
kitchen orders are ahead of it."* Never dump the database into the model.

### 4.8 AI action risk tiers

| Tier | Examples | Confirmation |
|---|---|---|
| Read-only | `get_table_status`, `get_order`, `get_queue`, `get_staff_status` | Automatic |
| Low-risk mutation | `mark_item_ready`, `update_preparation_status` | Potentially automatic |
| High-risk mutation | `refund_payment`, `cancel_order`, `apply_large_discount`, `delete_menu_item`, `change_staff_permissions` | Required |

```
AI → Action risk evaluation → Permission check → Confirmation (if required) → Execute
```

### 4.9 AI identity in audit

Never let an AI action appear as if a human performed it directly.

```
actor_type: USER | AI_AGENT | SYSTEM | INTEGRATION
acting_user_id / ai_agent_id (where applicable)
```

Audit example:

```
User: Sabbir
Agent: Kitchen Assistant
Action: order_item.complete
Resource: OI_123
```

### 4.10 The one boundary that matters

```
AI → Tool Call → Authorization → Business Rules → Domain Service → Database
```

**Never** `AI → Database`, and never `AI → privileged internal service`
without going through the same authorization model as everything else.

---

## 5. Unified actor model

The same permission system covers every kind of caller:

```
Actor
 ├── identity
 ├── permissions
 └── scopes

Human:    Web / Mobile / API
AI:       Chat / Voice / Automation
External: Integration
```

This is the foundation the entire product is built around.

---

## 6. Operational domain model

```
Branch
├── Floor → Table
├── Menu → Category, Item, Modifier, Pricing
├── Staff
├── Customer Session
├── Table Session
├── Order → Order Item
├── Kitchen → Kitchen Station, Queue
├── Payment
└── Events
```

### 6.1 Table session

A QR code represents `Branch + Table` — never an order directly.

```
QR → Table → Active Table Session
```

```
Table T5
 Session: TS_20260922_001
 Customer: Guest/Customer #123
 Orders: Order #1001, Order #1002
```

This allows multiple ordering events (food, then drinks, then dessert,
then bill) within one visit, rather than treating each order as a
separate visit.

### 6.2 Order lifecycle (state machine)

```
DRAFT → SUBMITTED → ACCEPTED → KITCHEN_QUEUE → PREPARING → READY → SERVED
                  └→ REJECTED
             (CANCELLED with controlled transitions)
```

Arbitrary endpoints must not set arbitrary statuses — only the defined
transitions are valid.

### 6.3 Order item state is independent

An order can contain items in different states simultaneously
(Burger → READY, Fries → PREPARING, Coke → SERVED). The order's aggregate
state is *derived* from its items, not tracked as a single field alone.

---

## 7. Backend module structure (NestJS-style, domain-organized)

```
src/
├── auth/
├── authorization/
├── organizations/
├── restaurants/
├── branches/
├── users/
├── staff/
├── menus/
├── tables/
├── sessions/
├── orders/
├── kitchen/
├── payments/
├── notifications/
├── realtime/
├── events/
├── audit/
├── analytics/
├── ai/
└── infrastructure/
```

Inside a domain, e.g. `orders/`:

```
orders/
├── application/
│   ├── commands/
│   ├── queries/
│   └── services/
├── domain/
│   ├── entities/
│   ├── value-objects/
│   ├── events/
│   └── rules/
└── infrastructure/
    ├── repositories/
    └── persistence/
```

Full DDD isn't required on day one, but the business boundaries between
domains must stay clean.

---

## 8. Database architecture

PostgreSQL as the single source of truth.

```
Organization
  └── Restaurant
        └── Branch
              ├── Table
              ├── Staff Assignment
              ├── Menu
              ├── Table Session
              │     └── Orders
              │           └── Order Items
              ├── Kitchen
              └── Payments
```

Tenant-owned records carry `organization_id` (and, where genuinely needed,
`restaurant_id` / `branch_id`) as **stored columns** — tenant ownership is
never established only by traversing relationships at query time.

### 8.1 Multi-tenancy strategy

Start with: **shared PostgreSQL database + shared schema**, tenant
boundary = `organization_id` on every relevant table (e.g. `orders.id`,
`orders.organization_id`, `orders.restaurant_id`, `orders.branch_id`,
`orders.table_session_id`, ...). Enforce isolation in the application
layer.

Upgrade path if/when needed for large enterprise customers:

```
shared DB → tenant partitioning → dedicated database (per big tenant)
```

...without changing the domain architecture itself.

### 8.2 Redis

Used for: WebSocket presence, active sessions, temporary OTPs, rate
limiting, distributed locks, caching, real-time coordination.
**Never** the source of truth — PostgreSQL remains authoritative.

---

## 9. Real-time & eventing

### 9.1 Real-time transport

```
Frontend
 ├── REST/HTTP  → Commands / Queries
 └── WebSocket  → Live updates
```

```
Customer places order → Backend → Database
                                 → Event → WebSocket → Waiter / Kitchen
```

Kitchen and waiter clients subscribe rather than poll (`GET /orders` in a
loop).

### 9.2 Domain events

Examples: `OrderSubmitted`, `OrderAccepted`, `OrderRejected`,
`OrderStarted`, `OrderItemReady`, `OrderReady`, `OrderServed`,
`PaymentStarted`, `PaymentCompleted`, `TableSessionClosed`.

These feed WebSockets, notifications, analytics, audit, AI context, and
reports.

### 9.3 Event bus

Start simple:

```
PostgreSQL → Outbox → Event Worker → WebSocket / Notifications / Analytics / AI event stream
```

Move to Kafka / Redpanda / NATS only when scale actually requires it.

---

## 10. Audit

Every meaningful action records:

```
Actor / Action / Resource / Before / After / Timestamp / Source
```

Example:

```
Actor: Kitchen AI
Acting on behalf of: User #123
Action: order_item.complete
Resource: OrderItem #456
Source: VOICE_AI
Result: SUCCESS
Timestamp: ...
```

This lets the platform answer "who marked this item ready?" and
distinguish Human / AI / API integration / System automation.

---

## 11. System architecture (high level)

```
                    Clients
        ┌───────────────────────────┐
        │ Customer Web · Staff Web  │
        │ Admin Web · Mobile (future)│
        │ AI Agents                 │
        └─────────────┬─────────────┘
                 HTTPS / WebSocket
                       │
                 API Gateway
                       │
     Authentication · Authorization
     Tenant Resolution · Rate Limiting
                       │
   ┌───────────────────┼───────────────────┐
   ▼                   ▼                   ▼
Organization       Restaurant          Operations
 Management        Management           Domain
   └───────────────────┼───────────────────┘
                       │
                Domain Services
     Tables · Sessions · Orders · Kitchen
     Payments · Staff
                       │
       ┌───────────────┼───────────────┐
       ▼               ▼               ▼
  PostgreSQL         Redis          Event Bus
                                        │
                          Notifications · Analytics
                          AI Context · Integrations
```

AI sits beside the normal clients, sharing the same downstream path:

```
AI Platform
  → Agent Runtime
  → Context Manager
  → Tool Registry
  → Authorization
  → Domain Services
```

---

## 12. Frontend architecture

Three experiences, one platform:

### Customer web
QR entry → table/session → menu → cart → order → order tracking →
payment → session status. No dashboard complexity.

### Staff web
One application, capability-rendered by permission — not one app per
role:

```
Current user → Permissions → UI capability map → Available screens/actions
```

Covers Manager, Waiter, Cashier, Kitchen, Bartender from the same
codebase. The frontend hides unauthorized actions; the **backend remains
authoritative**.

### Admin web
Organizations, restaurants, branches, staff, permissions, menus, reports,
configuration, billing.

### Workflow-first UX briefs

Give UI/UX designers workflows, not vague screens:

- **Waiter:** view assigned tables → view incoming order → accept/reject
  → see preparation status → serve ready order → request bill → view
  table session.
- **Kitchen:** view queue → accept/start preparation → view item details
  → mark item ready → view delayed orders.
- **Cashier:** view active sessions → generate bill → apply discount →
  select payment method → confirm payment → close session.

---

## 13. Full-picture diagram

```
                            PLATFORM OWNER
                                  │
                     ┌────────────────────────┐
                     │      SaaS PLATFORM     │
                     └────────────┬───────────┘
                                  │
                     ┌────────────▼───────────┐
                     │     ORGANIZATION       │
                     │ Users/Permissions      │
                     │ Restaurants · Billing  │
                     └────────────┬───────────┘
                                  │
                    ┌─────────────▼─────────────┐
                    │        RESTAURANT         │
                    └─────────────┬─────────────┘
                                  │
                    ┌─────────────▼─────────────┐
                    │          BRANCH           │
                    └─────────────┬─────────────┘
                                  │
             ┌────────────────────┼────────────────────┐
             ▼                    ▼                    ▼
          TABLES                STAFF                MENU
             │                    │
             ▼                    ▼
      TABLE SESSION        ASSIGNMENTS
             │
             ▼
           ORDERS → ORDER ITEMS → KITCHEN → READY → SERVED → PAYMENT → SESSION CLOSED

──────────────────────────────────────────────────────────

                       AUTHORIZATION
                    Permission + Scope
            ┌─────────────────┼─────────────────┐
          HUMAN               AI               SYSTEM
            │                 │                 │
        Web/Mobile        AI Tools          Automation
            └─────────────────┼─────────────────┘
                              ▼
                       DOMAIN SERVICES
                              ▼
                         PostgreSQL
```

And the AI-specific view:

```
                         AI PLATFORM
                    ┌─────────▼─────────┐
                    │   Agent Runtime   │
                    └─────────┬─────────┘
                ┌─────────────▼─────────────┐
                │ Identity + Context        │
                │ Who am I? Acting for who? │
                │ Org/Branch? Permissions?  │
                └─────────────┬─────────────┘
                    ┌─────────▼─────────┐
                    │   Tool Registry   │
                    └─────────┬─────────┘
                 ┌────────────┼────────────┐
             Read Tools   Action Tools  Analytics
                 └────────────┼────────────┘
                          Authorization
                          Business Rules
                          Domain Services
                             PostgreSQL
```

The engineering contract this gives AI developers is short:

```
Agent → Context → Tool → Permission → Command → Result
```

They don't need to understand the whole database — just this chain.

---

## 14. Future AI roles this design already supports

Because AI is just another scoped actor, these can be added later without
a second architecture: **Waiter AI, Kitchen AI, Cashier AI, Branch Manager
AI, Organization AI** — each is the same Agent Runtime with a different
identity, scope, and permission set.
