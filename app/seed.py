"""Idempotent seed that fills every table in the public schema.

Shared by ``POST /api/v1/demo/seed`` and ``seed_db.py`` so the API route and
the standalone script always write identical rows. Re-running never duplicates
data: each row is looked up by its natural key first, and already-advanced
rows are never moved backwards.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.state_machines import (
    GuestSessionStatus,
    OrderItemStatus,
    OrderStatus,
    PaymentStatus,
    ReservationStatus,
    TableSessionStatus,
    TableStatus,
    apply_transition,
)
from app.models import (
    AuditLog,
    Branch,
    Brand,
    Department,
    DiningTable,
    Employee,
    GuestSession,
    InventorySku,
    MenuItem,
    Organization,
    Order,
    OrderItem,
    Payment,
    RecipeComponent,
    Reservation,
    Role,
    TableSession,
)

ORG_NAME = "Tavonza Demo"
BRAND_NAME = "Tavonza"
BRANCH_NAME = "HQ"

TABLES = [("T1", 4), ("T2", 4), ("T3", 6)]

DEPARTMENTS = [
    ("Floor Service", "floor"),
    ("Kitchen", "kitchen"),
    ("Bar", "bar"),
    ("Cashier", "counter"),
]

ROLES = [
    ("manager", ["orders.read", "orders.write", "payments.write", "inventory.read", "reports.read"]),
    ("waiter", ["orders.read", "orders.write", "tables.write"]),
    ("kitchen", ["orders.read", "items.write"]),
    ("cashier", ["payments.read", "payments.write"]),
    ("host", ["reservations.read", "reservations.write", "tables.read"]),
    ("bartender", ["orders.read", "items.write"]),
]

EMPLOYEES = [
    ("Sofia Alvarez", "Floor Service", "manager"),
    ("Asha Mehta", "Floor Service", "waiter"),
    ("Daniel Okoro", "Floor Service", "host"),
    ("Priya Nair", "Kitchen", "kitchen"),
    ("Marco Bianchi", "Kitchen", "kitchen"),
    ("Leila Haddad", "Bar", "bartender"),
    ("Tom Becker", "Cashier", "cashier"),
]

# code, name, on_hand, par_level, unit — TRUFFLE-OIL sits at zero on purpose so
# the recipe-BOM check has a real critical alert to surface.
INVENTORY_SKUS = [
    ("BEEF-PATTY", "Beef patty 150g", "18.000", "10.000", "kg"),
    ("BUN-BRIOCHE", "Brioche bun", "46.000", "40.000", "ea"),
    ("CHEESE-CHEDDAR", "Cheddar slice", "60.000", "50.000", "ea"),
    ("LETTUCE-ROME", "Romaine lettuce", "6.500", "4.000", "kg"),
    ("SALMON-FILLET", "Salmon fillet", "7.200", "6.000", "kg"),
    ("BUTTER-UNSALT", "Unsalted butter", "3.000", "2.000", "kg"),
    ("LEMON", "Lemon", "22.000", "15.000", "ea"),
    ("PARMESAN", "Parmesan grated", "1.800", "2.000", "kg"),
    ("CROUTONS", "Croutons", "2.400", "2.000", "kg"),
    ("POTATO-FRIES", "Frozen fries", "14.000", "10.000", "kg"),
    ("TRUFFLE-OIL", "Truffle oil", "0.000", "1.500", "bottle"),
    ("COFFEE-BEANS", "Espresso beans", "4.200", "3.000", "kg"),
    ("LIME", "Lime", "30.000", "20.000", "ea"),
    ("SUGAR-SYRUP", "Sugar syrup", "2.500", "2.000", "l"),
    ("MINT", "Fresh mint", "0.600", "0.500", "kg"),
]

# name, description, price, station, allergens, recipe [(sku_code, qty_per_portion)]
MENU_ITEMS = [
    (
        "Classic Cheeseburger",
        "150g beef patty, cheddar, brioche bun, romaine.",
        "12.50",
        "grill",
        ["gluten", "dairy"],
        [("BEEF-PATTY", "0.150"), ("BUN-BRIOCHE", "1"), ("CHEESE-CHEDDAR", "1"), ("LETTUCE-ROME", "0.020")],
    ),
    (
        "Grilled Salmon",
        "Salmon fillet, butter baste, lemon.",
        "21.00",
        "grill",
        ["fish", "dairy"],
        [("SALMON-FILLET", "0.220"), ("BUTTER-UNSALT", "0.020"), ("LEMON", "1")],
    ),
    (
        "Caesar Salad",
        "Romaine, parmesan, croutons, caesar dressing.",
        "10.75",
        "cold",
        ["dairy", "gluten"],
        [("LETTUCE-ROME", "0.150"), ("PARMESAN", "0.030"), ("CROUTONS", "0.040")],
    ),
    (
        "Truffle Fries",
        "Shoestring fries finished with truffle oil.",
        "8.00",
        "fryer",
        [],
        [("POTATO-FRIES", "0.250"), ("TRUFFLE-OIL", "0.020")],
    ),
    (
        "Espresso",
        "Double shot, house blend.",
        "3.50",
        "coffee",
        [],
        [("COFFEE-BEANS", "0.018")],
    ),
    (
        "Fresh Lime Mojito",
        "Lime, mint, sugar syrup, soda.",
        "9.00",
        "bar",
        [],
        [("LIME", "1"), ("SUGAR-SYRUP", "0.020"), ("MINT", "0.010")],
    ),
]

# Legal linear spines — consecutive steps are all real state-machine edges.
SESSION_PATH = [
    TableSessionStatus.OPEN,
    TableSessionStatus.ORDERING,
    TableSessionStatus.DINING,
    TableSessionStatus.SETTLING,
    TableSessionStatus.CLOSED,
]
GUEST_PATH = [
    GuestSessionStatus.AUTHENTICATING,
    GuestSessionStatus.JOINED,
    GuestSessionStatus.ORDERING,
    GuestSessionStatus.DINING,
    GuestSessionStatus.SETTLING,
    GuestSessionStatus.LEFT,
]
ORDER_PATH = [
    OrderStatus.DRAFT,
    OrderStatus.SUBMITTED,
    OrderStatus.IN_KITCHEN,
    OrderStatus.READY,
    OrderStatus.SERVED,
]
ITEM_PATH = [
    OrderItemStatus.QUEUED,
    OrderItemStatus.PREPPING,
    OrderItemStatus.FIRED,
    OrderItemStatus.READY,
    OrderItemStatus.PICKED_UP,
]
PAYMENT_PATH = [
    PaymentStatus.UNPAID,
    PaymentStatus.PARTIAL,
    PaymentStatus.AUTHORIZED,
    PaymentStatus.SETTLED,
]
RESERVATION_PATH = [
    ReservationStatus.REQUESTED,
    ReservationStatus.CONFIRMED,
    ReservationStatus.SEATED,
    ReservationStatus.COMPLETED,
]


async def _upsert(
    db: AsyncSession,
    model: type,
    lookup: dict[str, Any],
    defaults: dict[str, Any] | None = None,
):
    """Find a row by natural key or insert it. Returns (row, created)."""
    stmt = select(model)
    for key, value in lookup.items():
        stmt = stmt.where(getattr(model, key) == value)
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is not None:
        return row, False
    row = model(**lookup, **(defaults or {}))
    db.add(row)
    await db.flush()
    return row, True


def _advance_to(row: Any, enum_cls: type, path: list, target: Any) -> bool:
    """Walk ``row.status`` toward ``target`` along ``path`` via apply_transition.

    Never moves a row backwards. Targets outside the linear spine (cancelled,
    no_show, voided) are applied as a single validated edge.
    """
    if row.status == target.value:
        return False
    values = [step.value for step in path]
    if target.value in values and row.status in values:
        current, goal = values.index(row.status), values.index(target.value)
        for step in path[current + 1 : goal + 1]:
            apply_transition(enum_cls, enum_cls(row.status), step)
            row.status = step.value
        return row.status == target.value
    apply_transition(enum_cls, enum_cls(row.status), target)
    row.status = target.value
    return True


async def _seed_tenant(db: AsyncSession):
    org, _ = await _upsert(db, Organization, {"name": ORG_NAME})
    brand, _ = await _upsert(db, Brand, {"organization_id": org.id, "name": BRAND_NAME})
    branch, _ = await _upsert(
        db, Branch, {"brand_id": brand.id, "name": BRANCH_NAME}, {"timezone": "UTC"}
    )

    table_ids: list[int] = []
    for code, capacity in TABLES:
        table, _ = await _upsert(
            db,
            DiningTable,
            {"branch_id": branch.id, "code": code},
            {
                "qr_token": f"demo-{branch.id}-{code.lower()}",
                "status": TableStatus.AVAILABLE.value,
                "capacity": capacity,
            },
        )
        table_ids.append(table.id)

    return org, brand, branch, table_ids


async def _seed_org_chart(db: AsyncSession, branch) -> dict[str, Any]:
    department_ids: dict[str, int] = {}
    for name, station in DEPARTMENTS:
        row, _ = await _upsert(
            db, Department, {"branch_id": branch.id, "name": name}, {"station_type": station}
        )
        department_ids[name] = row.id

    role_ids: dict[str, int] = {}
    for name, permissions in ROLES:
        row, _ = await _upsert(db, Role, {"name": name}, {"permissions": permissions})
        role_ids[name] = row.id

    employee_ids: list[int] = []
    for full_name, department, role in EMPLOYEES:
        row, _ = await _upsert(
            db,
            Employee,
            {
                "department_id": department_ids[department],
                "role_id": role_ids[role],
                "full_name": full_name,
            },
        )
        employee_ids.append(row.id)

    return {
        "department_ids": department_ids,
        "role_ids": role_ids,
        "employee_ids": employee_ids,
    }


async def _seed_catalog(db: AsyncSession, branch) -> dict[str, Any]:
    sku_ids: dict[str, int] = {}
    for code, name, on_hand, par, unit in INVENTORY_SKUS:
        row, _ = await _upsert(
            db,
            InventorySku,
            {"branch_id": branch.id, "sku_code": code},
            {
                "name": name,
                "on_hand": Decimal(on_hand),
                "par_level": Decimal(par),
                "unit": unit,
            },
        )
        sku_ids[code] = row.id

    menu_ids: dict[str, int] = {}
    recipe_count = 0
    for name, description, price, station, allergens, recipe in MENU_ITEMS:
        row, _ = await _upsert(
            db,
            MenuItem,
            {"branch_id": branch.id, "name": name},
            {
                "description": description,
                "price": Decimal(price),
                "station": station,
                "allergens": allergens,
            },
        )
        menu_ids[name] = row.id
        for sku_code, quantity in recipe:
            _, created = await _upsert(
                db,
                RecipeComponent,
                {"menu_item_id": row.id, "sku_id": sku_ids[sku_code]},
                {"quantity": Decimal(quantity)},
            )
            recipe_count += int(created)

    return {"sku_ids": sku_ids, "menu_ids": menu_ids, "recipe_components_created": recipe_count}


async def _seed_reservations(db: AsyncSession, branch) -> list[dict[str, Any]]:
    """Only inserts when the branch has no reservations yet, so the original
    demo rows (and anything the user created) are left untouched."""
    existing = (
        await db.execute(select(Reservation).where(Reservation.branch_id == branch.id))
    ).scalars().all()
    if existing:
        return []

    created: list[dict[str, Any]] = []
    now = datetime.utcnow()
    specs = [
        ("Amina Yusuf", 4, 1, ReservationStatus.REQUESTED),
        ("Ravi Patel", 2, 3, ReservationStatus.CONFIRMED),
        ("Sofia Rossi", 6, 5, ReservationStatus.CANCELLED),
        ("Kenji Watanabe", 2, 26, ReservationStatus.CONFIRMED),
        ("Fatima Al-Sayed", 5, 27, ReservationStatus.REQUESTED),
    ]
    for name, party, hours, target in specs:
        row = Reservation(
            branch_id=branch.id,
            table_id=None,
            guest_name=name,
            party_size=party,
            status=ReservationStatus.REQUESTED.value,
            start_at=now + timedelta(hours=hours),
        )
        db.add(row)
        await db.flush()
        _advance_to(row, ReservationStatus, RESERVATION_PATH, target)
        created.append({"id": row.id, "guest_name": name, "status": row.status})
    return created


async def _seed_live_service(db: AsyncSession, branch, table_ids, menu_ids) -> dict[str, Any]:
    """One in-flight visit on T1 (ordering, unpaid) and one closed visit on T2
    (settled) so orders, queues, splits, and revenue all have real rows."""
    result: dict[str, Any] = {"table_session_id": None, "order_id": None, "payment_id": None}

    session, created = await _upsert(
        db,
        TableSession,
        {"table_id": table_ids[0]},
        {
            "branch_id": branch.id,
            "status": TableSessionStatus.OPEN.value,
            "opened_at": datetime.utcnow() - timedelta(minutes=45),
        },
    )
    if created:
        _advance_to(session, TableSessionStatus, SESSION_PATH, TableSessionStatus.ORDERING)
        table = await db.get(DiningTable, table_ids[0])
        apply_transition(TableStatus, TableStatus(table.status), TableStatus.SEATED)
        table.status = TableStatus.SEATED.value
    result["table_session_id"] = session.id

    guests_spec = [
        ("Nadia Karim", GuestSessionStatus.ORDERING, ["halal", "no-nuts"]),
        ("Karim Yusuf", GuestSessionStatus.JOINED, []),
    ]
    for display_name, target, prefs in guests_spec:
        guest, guest_created = await _upsert(
            db,
            GuestSession,
            {"table_session_id": session.id, "display_name": display_name},
            {
                "phone": "+10000000001",
                "otp_verified": True,
                "status": GuestSessionStatus.AUTHENTICATING.value,
                "dietary_preferences": prefs,
            },
        )
        if guest_created:
            _advance_to(guest, GuestSessionStatus, GUEST_PATH, target)

    order, order_created = await _upsert(
        db,
        Order,
        {"table_session_id": session.id},
        {
            "guest_session_id": None,
            "is_shared": True,
            "status": OrderStatus.DRAFT.value,
        },
    )
    if order_created:
        _advance_to(order, OrderStatus, ORDER_PATH, OrderStatus.IN_KITCHEN)
    result["order_id"] = order.id

    items_spec = [
        ("Classic Cheeseburger", 2, OrderItemStatus.READY, "grill", ["no pickles"]),
        ("Caesar Salad", 1, OrderItemStatus.PREPPING, "cold", []),
        ("Truffle Fries", 1, OrderItemStatus.FIRED, "fryer", []),
        ("Espresso", 2, OrderItemStatus.QUEUED, "coffee", []),
        ("Fresh Lime Mojito", 2, OrderItemStatus.QUEUED, "bar", ["no alcohol"]),
    ]
    total = Decimal("0.00")
    for menu_name, quantity, item_target, station, modifiers in items_spec:
        price = (
            await db.execute(select(MenuItem.price).where(MenuItem.id == menu_ids[menu_name]))
        ).scalar_one()
        total += price * quantity
        _, item_created = await _upsert(
            db,
            OrderItem,
            {"order_id": order.id, "menu_item_id": menu_ids[menu_name]},
            {
                "quantity": quantity,
                "modifiers": modifiers,
                "status": OrderItemStatus.QUEUED.value,
                "station": station,
            },
        )
        if item_created:
            item = (
                await db.execute(
                    select(OrderItem).where(
                        OrderItem.order_id == order.id,
                        OrderItem.menu_item_id == menu_ids[menu_name],
                    )
                )
            ).scalar_one()
            _advance_to(item, OrderItemStatus, ITEM_PATH, item_target)

    payment, payment_created = await _upsert(
        db,
        Payment,
        {"order_id": order.id},
        {"guest_session_id": None, "amount": total, "status": PaymentStatus.UNPAID.value, "method": "card"},
    )
    if payment_created:
        result["payment_id"] = payment.id
        result["payment_amount"] = str(total)

    closed = await _seed_closed_visit(db, branch, table_ids[1], menu_ids)
    result["closed_table_session_id"] = closed
    return result


async def _seed_closed_visit(db: AsyncSession, branch, table_id, menu_ids) -> int | None:
    """A finished, settled visit on T2 — walked through every lifecycle."""
    session, created = await _upsert(
        db,
        TableSession,
        {"table_id": table_id},
        {
            "branch_id": branch.id,
            "status": TableSessionStatus.OPEN.value,
            "opened_at": datetime.utcnow() - timedelta(hours=4),
            "closed_at": datetime.utcnow() - timedelta(hours=2),
        },
    )
    if not created:
        return session.id

    _advance_to(session, TableSessionStatus, SESSION_PATH, TableSessionStatus.CLOSED)

    guest, guest_created = await _upsert(
        db,
        GuestSession,
        {"table_session_id": session.id, "display_name": "Olivia Chen"},
        {
            "phone": "+10000000002",
            "otp_verified": True,
            "status": GuestSessionStatus.AUTHENTICATING.value,
            "dietary_preferences": ["vegetarian"],
        },
    )
    if guest_created:
        _advance_to(guest, GuestSessionStatus, GUEST_PATH, GuestSessionStatus.LEFT)

    order, order_created = await _upsert(
        db,
        Order,
        {"table_session_id": session.id},
        {"guest_session_id": guest.id, "is_shared": False, "status": OrderStatus.DRAFT.value},
    )
    if not order_created:
        return session.id
    _advance_to(order, OrderStatus, ORDER_PATH, OrderStatus.SERVED)

    total = Decimal("0.00")
    for menu_name, quantity in [("Grilled Salmon", 1), ("Caesar Salad", 1), ("Espresso", 2)]:
        price = (
            await db.execute(select(MenuItem.price).where(MenuItem.id == menu_ids[menu_name]))
        ).scalar_one()
        total += price * quantity
        item, item_created = await _upsert(
            db,
            OrderItem,
            {"order_id": order.id, "menu_item_id": menu_ids[menu_name]},
            {
                "quantity": quantity,
                "modifiers": [],
                "status": OrderItemStatus.QUEUED.value,
                "station": "grill" if menu_name != "Espresso" else "coffee",
            },
        )
        if item_created:
            _advance_to(item, OrderItemStatus, ITEM_PATH, OrderItemStatus.PICKED_UP)

    payment, payment_created = await _upsert(
        db,
        Payment,
        {"order_id": order.id},
        {
            "guest_session_id": guest.id,
            "amount": total,
            "status": PaymentStatus.UNPAID.value,
            "method": "card",
        },
    )
    if payment_created:
        _advance_to(payment, PaymentStatus, PAYMENT_PATH, PaymentStatus.SETTLED)

    # Table went seated → dirty → available when the visit closed.
    table = await db.get(DiningTable, table_id)
    if table.status == TableStatus.SEATED.value:
        apply_transition(TableStatus, TableStatus.SEATED, TableStatus.DIRTY)
        table.status = TableStatus.DIRTY.value
        apply_transition(TableStatus, TableStatus.DIRTY, TableStatus.AVAILABLE)
        table.status = TableStatus.AVAILABLE.value
    return session.id


async def _seed_audit(db: AsyncSession, branch) -> int:
    existing = (
        await db.execute(
            select(AuditLog).where(AuditLog.branch_id == branch.id).limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return 0
    entries = [
        ("system", "seed.completed", {"branch": BRANCH_NAME}),
        ("waiter", "table_session.opened", {"table": "T1"}),
        ("cashier", "payment.settled", {"table": "T2", "method": "card"}),
    ]
    for actor, event_type, payload in entries:
        db.add(
            AuditLog(
                branch_id=branch.id,
                actor_role=actor,
                event_type=event_type,
                payload=payload,
            )
        )
    return len(entries)


async def seed_all(db: AsyncSession) -> dict[str, Any]:
    """Fill every table. Safe to re-run: existing rows are reused, never
    duplicated, and never rewound to an earlier status."""
    org, brand, branch, table_ids = await _seed_tenant(db)
    chart = await _seed_org_chart(db, branch)
    catalog = await _seed_catalog(db, branch)
    reservations = await _seed_reservations(db, branch)
    service = await _seed_live_service(db, branch, table_ids, catalog["menu_ids"])
    audit_count = await _seed_audit(db, branch)
    reservation_total = len(
        (
            await db.execute(
                select(Reservation).where(Reservation.branch_id == branch.id)
            )
        ).scalars().all()
    )

    await db.commit()

    return {
        "organization_id": org.id,
        "brand_id": brand.id,
        "branch_id": branch.id,
        "table_ids": table_ids,
        "departments": chart["department_ids"],
        "roles": chart["role_ids"],
        "employees": len(chart["employee_ids"]),
        "inventory_skus": len(INVENTORY_SKUS),
        "menu_items": len(MENU_ITEMS),
        "recipe_components_created": catalog["recipe_components_created"],
        "reservations_created": reservations,
        "reservations_total": reservation_total,
        "table_session_id": service["table_session_id"],
        "order_id": service["order_id"],
        "payment_id": service["payment_id"],
        "audit_logs": audit_count,
        "next": [
            f"GET  /api/v1/dashboard/{branch.id}",
            f"GET  /api/v1/data",
            f"GET  /api/v1/menu?branch_id={branch.id}",
            f"GET  /api/v1/orders?branch_id={branch.id}",
            f"GET  /api/v1/table-sessions/{service['table_session_id']}",
            f"GET  /api/v1/reservations?branch_id={branch.id}",
        ],
    }
