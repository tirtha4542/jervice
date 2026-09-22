"""Post the full demo dataset into the connected database.

Usage:
    python seed_db.py

Runs the same idempotent routine as POST /api/v1/demo/seed, so it is safe to
re-run: existing rows are reused, never duplicated, never rewound.
"""

from __future__ import annotations

import asyncio
import json
import sys

from sqlalchemy import text

from app.core.database import get_engine
from app.seed import seed_all
from app.core.database import get_sessionmaker

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


async def main() -> int:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as db:
        result = await seed_all(db)

    print("=" * 64)
    print("SEED COMPLETE")
    print("=" * 64)
    print(json.dumps(result, indent=2, default=str))

    engine = get_engine()
    try:
        async with engine.connect() as conn:
            print("\nRow counts:")
            for table in TABLES:
                count = (
                    await conn.execute(text(f"SELECT count(*) FROM {table}"))
                ).scalar_one()
                print(f"  {table:<22} {count}")
    finally:
        if get_engine.cache_info().currsize:
            await get_engine().dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
