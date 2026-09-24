"""Rotate predictable local/demo QR tokens.

Real QR deployments should issue signed, rotatable tokens from the QR
provider instead. This helper is only for the local demo database.
"""

from __future__ import annotations

import argparse
import asyncio
import secrets

import asyncpg
from sqlalchemy.engine import make_url

from app.core.config import settings


async def rotate(apply: bool, include_legacy: bool = False) -> int:
    url = make_url(settings.database_url)
    connection = await asyncpg.connect(
        host=url.host,
        port=url.port or 5432,
        user=url.username,
        password=url.password,
        database=url.database,
    )
    try:
        async with connection.transaction():
            if include_legacy:
                rows = await connection.fetch(
                    "SELECT id FROM dining_tables WHERE qr_token LIKE 'demo-%' OR qr_token LIKE 'qr-%'"
                )
            else:
                rows = await connection.fetch(
                    "SELECT id FROM dining_tables WHERE qr_token LIKE 'demo-%'"
                )
            if apply:
                for row in rows:
                    await connection.execute(
                        "UPDATE dining_tables SET qr_token = $1 WHERE id = $2",
                        f"qr_{secrets.token_urlsafe(24)}",
                        row["id"],
                    )
        return len(rows)
    finally:
        await connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Rotate local demo QR tokens")
    parser.add_argument("--apply", action="store_true", help="write the new random tokens")
    parser.add_argument(
        "--include-legacy-qr",
        action="store_true",
        help="also rotate old qr-* tokens; review before using",
    )
    args = parser.parse_args()
    count = asyncio.run(rotate(args.apply, args.include_legacy_qr))
    print(f"{'rotated' if args.apply else 'found'} {count} local QR token(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
