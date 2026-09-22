"""Dump row counts and sample rows from every table in the public schema."""

from __future__ import annotations

import asyncio
import json

from sqlalchemy import text

from app.core.database import get_engine

TABLES = [
    "organizations",
    "brands",
    "branches",
    "departments",
    "roles",
    "employees",
    "dining_tables",
    "table_sessions",
    "guest_sessions",
    "menu_items",
    "inventory_skus",
    "recipe_components",
    "orders",
    "order_items",
    "payments",
    "reservations",
    "audit_logs",
]


def _render(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


async def main() -> None:
    engine = get_engine()
    try:
        async with engine.connect() as conn:
            for table in TABLES:
                count = (await conn.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()
                print(f"\n=== {table} : {count} rows ===")
                if count == 0:
                    continue
                rows = (
                    await conn.execute(text(f"SELECT * FROM {table} ORDER BY id LIMIT 5"))
                ).mappings().all()
                for row in rows:
                    payload = {k: _render(v) for k, v in dict(row).items()}
                    print("  " + json.dumps(payload, ensure_ascii=False))
    finally:
        if get_engine.cache_info().currsize:
            await get_engine().dispose()


if __name__ == "__main__":
    asyncio.run(main())
