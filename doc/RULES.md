# RULES.md

> Project rules for the multi-tenant restaurant operations platform.
> This file is authoritative. If code, a PR, or an AI agent's suggestion
> conflicts with this file, this file wins. Update this file deliberately,
> not by accident.

Companion files: `ARCHITECTURE.md` (the system design these rules protect),
`STATUS.md` (what's actually built right now).

---

## 0. The one sentence version

> **AI is another client of the platform, never a privileged path around it.**

Every rule below is a consequence of that sentence. Humans, AI agents, and
future integrations are all **actors** that call the same commands, pass
through the same authorization engine, and hit the same domain services.
There is no back door.

---

## 1. Non-negotiable architecture decisions

These are locked. Changing any of them is an architecture decision, not a
code change — it needs a written note in `ARCHITECTURE.md` and a reason.

| Principle | Decision |
|---|---|
| SaaS model | Multi-tenant |
| Tenant boundary | Organization |
| Business hierarchy | Organization → Restaurant → Branch |
| Operational boundary | **Branch** (where real-time ops happen) |
| Authorization model | Permission + Scope (not role-only) |
| Roles | Permission *bundles*, UI convenience only — never authoritative |
| Source of truth | PostgreSQL |
| Real-time | WebSocket + domain events |
| Cache | Redis (never source of truth) |
| Async | Outbox pattern first; Kafka/NATS only if scale demands it |
| Ordering flow | Table Session → Order → Order Items |
| Kitchen state | Item-level state machine, not order-level only |
| Payments | Separate domain, never bolted onto Orders |
| Audit | Every mutating action recorded with actor, before/after |
| AI database access | **Never, under any circumstance** |
| AI access path | Tools → Commands/APIs → Authorization → Domain Services |
| AI authorization | Same engine as humans, no shortcuts |
| AI identity | Distinct actor type, always attributable to a human owner |
| Frontend authorization | Capability-driven UI (hide, don't rely on hiding) |
| Backend authorization | Always authoritative, always re-checked server-side |

---

## 2. Hard rules — never break these

1. **Never let AI touch the database directly.** No raw SQL from an agent,
   no ORM access, no "just this once for a read query." AI calls tools;
   tools call the same commands/services humans call.
2. **Never authorize by role name.** Always check `permission + scope`
   against the resource. A role is a label for a bundle of permissions,
   nothing more — it must never appear in an `if (role === 'WAITER')` check.
3. **Never trust the frontend's permission check.** The UI may hide a
   button; the backend must independently verify the actor has the
   permission and scope before executing. Treat every request as if it
   came from a modified client.
4. **Never let scope be inferred from the org tree alone.** A record's
   tenant ownership (`organization_id`, and where relevant `restaurant_id`,
   `branch_id`) must be stored directly on the record, not derived only by
   walking relationships at query time.
5. **Never perform a high-risk mutation without confirmation**, whether the
   actor is human or AI. Refunds, cancellations, large discounts, menu
   deletions, and permission changes require an explicit confirmation step.
6. **Never record an AI action as if a human performed it.** Every audit
   entry must carry `actor_type` (`USER` / `AI_AGENT` / `SYSTEM` /
   `INTEGRATION`), and every AI action must carry the human it acts on
   behalf of (`acting_user_id` / `ai_agent_id`).
7. **Never skip the state machine.** Order and Order Item statuses only
   move through their defined transitions. No endpoint sets an arbitrary
   status directly.
8. **Never treat a QR code as an order.** A QR code resolves to
   `Branch + Table`, which resolves to an active (or newly created) Table
   Session. Orders live inside a session, not inside the QR code.
9. **Never dump full platform data into an AI context.** Context is built
   per actor, scoped to what that actor's permissions allow — a Context
   Builder step, not a database export.
10. **Never build a second architecture for AI.** If a new AI capability
    needs a new code path that bypasses Authorization → Domain Services,
    that's a sign the design is wrong — stop and redesign the tool, not the
    boundary.

---

## 3. Working rules for day-to-day development

- **Organize backend code by domain, not technical layer.** See
  `ARCHITECTURE.md §7` for the module list
  (`orders/`, `kitchen/`, `payments/`, `ai/`, etc.). Don't create a global
  `controllers/`, `services/`, `models/` split across the whole app.
- **New permissions get added explicitly.** Don't overload an existing
  permission's meaning to cover a new action — add `resource.action` and
  wire it into the relevant role bundles.
- **New AI tools go through the Tool Registry**, get a risk tier
  (read-only / low-risk mutation / high-risk mutation), and get an
  explicit permission check before they can be called — never call a
  domain service directly from an agent's tool-handling code.
- **Every domain event that matters gets emitted**, even if nothing
  consumes it yet (`OrderSubmitted`, `OrderItemReady`, `PaymentCompleted`,
  etc.). It's cheap now and expensive to retrofit once WebSockets,
  notifications, analytics, and AI context all depend on the event stream.
- **Don't introduce Kafka, a second database, or a new multi-tenancy
  strategy speculatively.** Start with shared PostgreSQL + `organization_id`
  + Outbox events. Evolve only when a real scale problem shows up — see
  `ARCHITECTURE.md §10` for the upgrade path.
- **Staff-facing frontend is one app, capability-rendered** —
  don't fork separate codebases per role (waiter app, kitchen app,
  cashier app). Permissions decide what renders.

---

## 4. Rules specifically for AI agents working in this codebase

If you are an AI assistant (coding agent or in-product AI) operating on
this repository:

- Treat this file as binding. If a request conflicts with it, say so and
  propose the compliant version instead of quietly complying.
- Any code you write that lets an "AI agent" actor call a domain service
  must go through the same authorization checks as a human actor's code
  path — do not add an `isAI` bypass anywhere, ever.
- When adding a new AI tool, always specify: the tool's risk tier, the
  permission(s) it requires, its scope resolution, and whether it needs
  confirmation before executing.
- When in doubt about which module a change belongs in, check
  `ARCHITECTURE.md §7` (module structure) and `§8` (domain hierarchy)
  before creating a new top-level directory.
- Update `STATUS.md` when you complete, start, or block on a piece of work
  — it's the fast-read source of "what's actually true right now."

---

## 5. Change control

- Changes to **§1 and §2** of this file require a written rationale in the
  PR description and, ideally, a short note added to `ARCHITECTURE.md`
  explaining what changed and why.
- Changes to **§3 and §4** can be made more freely as the team learns —
  these are working conventions, not architectural boundaries.
