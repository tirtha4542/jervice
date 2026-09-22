"""Validate DATABASE_URL before starting the API.

Usage:
    python check_db.py

Reads DATABASE_URL from .env, checks it against what the asyncpg driver
actually accepts, then attempts a real connection and reports which app
tables exist.
"""

from __future__ import annotations

import asyncio
import inspect
import sys
import time

import asyncpg
from sqlalchemy import text
from sqlalchemy.engine.url import make_url

from app import models as _models  # noqa: F401  register metadata
from app.core.config import settings
from app.core.database import Base, get_engine

OK, WARN, FAIL = "OK  ", "WARN", "FAIL"


def say(level: str, message: str) -> None:
    print(f"[{level}] {message}")


def masked(password: str | None) -> str:
    if not password:
        return "(none)"
    if len(password) <= 4:
        return "*" * len(password)
    return "*" * (len(password) - 4) + password[-4:]


def check_url() -> "tuple[object, list[str]]":
    """Parse the URL and validate driver + query parameters."""
    try:
        url = make_url(settings.database_url)
    except Exception as exc:  # noqa: BLE001
        say(FAIL, f"DATABASE_URL is not a parseable URL: {exc}")
        return None, []

    if url.get_backend_name() != "postgresql":
        say(FAIL, f"Unsupported scheme '{url.get_backend_name()}' — expected postgresql.")
        return None, []

    say(OK, "DATABASE_URL parsed")
    print(f"       scheme : {url.get_backend_name()}+asyncpg")
    print(f"       host   : {url.host}:{url.port or 5432}")
    print(f"       db     : {url.database}")
    print(f"       user   : {url.username}")
    print(f"       password: {masked(url.password)}")
    print(f"       params : {dict(url.query) or '(none)'}")

    try:
        import sqlalchemy

        say(OK, f"asyncpg {asyncpg.__version__} + SQLAlchemy {sqlalchemy.__version__} installed")
    except ImportError as exc:
        say(FAIL, f"Missing driver: {exc}  →  pip install -r requirements.txt")
        return None, []

    # Query params are forwarded verbatim to asyncpg.connect(**kwargs).
    accepted = set(inspect.signature(asyncpg.connect).parameters)
    unsupported = sorted(k for k in url.query if k not in accepted)
    if unsupported:
        say(FAIL, f"Query params asyncpg does not accept: {', '.join(unsupported)}")
        if "sslmode" in unsupported:
            print("       fix: sslmode=require  →  ssl=require")
        if {"sslrootcert", "sslcert", "sslkey"} & set(unsupported):
            print("       fix: client certs need an ssl.SSLContext passed in code")
        if "application_name" in unsupported:
            print("       fix: set it via server_settings in code")
        if "connect_timeout" in unsupported:
            print("       fix: connect_timeout=10  →  timeout=10")
    else:
        say(OK, "all query params accepted by asyncpg")
    return url, unsupported


async def probe() -> int:
    engine = get_engine()
    started = time.perf_counter()
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    elapsed = (time.perf_counter() - started) * 1000
    say(OK, f"connected in {elapsed:.0f} ms (SELECT 1)")
    return int(elapsed)


async def schema_report() -> None:
    engine = get_engine()
    expected = sorted(Base.metadata.tables)
    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'")
        )
        actual = int(result.scalar_one())
    print(f"       tables in public schema: {actual}")
    if actual == 0:
        say(WARN, "schema is empty — start the API once (lifespan runs create_all): uvicorn app.main:app")
    elif actual < len(expected):
        say(WARN, f"expected {len(expected)} app tables, found {actual}")
    else:
        say(OK, f"schema ready — {len(expected)} app tables available")


async def verify() -> None:
    """One event loop for everything — the pooled engine binds to it."""
    try:
        await probe()
        await schema_report()
    finally:
        if get_engine.cache_info().currsize:
            try:
                await get_engine().dispose()
            except Exception:  # noqa: BLE001
                pass


def explain(exc: BaseException) -> None:
    name = type(exc).__name__
    text_ = str(exc)
    say(FAIL, f"{name}: {text_}")
    lowered = text_.lower()
    if isinstance(exc, TimeoutError) or "timeout" in lowered:
        print("       → host/port unreachable, or a firewall/security group is blocking port 5432")
    elif "password authentication failed" in lowered or "authentication" in lowered:
        print("       → wrong username or password (password must be URL-encoded)")
    elif "does not exist" in lowered and "database" in lowered:
        print("       → database name is wrong; it must already exist on the server")
    elif "ssl" in lowered:
        print("       → TLS mismatch; try ?ssl=require or drop the ssl param")
    elif isinstance(exc, TypeError) and "keyword argument" in lowered:
        print("       → a query param is not an asyncpg.connect() argument; see the list above")
    elif "temporary failure in name resolution" in lowered or "getaddrinfo" in lowered:
        print("       → hostname does not resolve; check host spelling / VPN")


def main() -> int:
    print("=" * 64)
    print("DATABASE CONNECTION CHECK")
    print("=" * 64)

    url, unsupported = check_url()
    if url is None:
        return 1

    failures = 0
    try:
        asyncio.run(verify())
    except Exception as exc:  # noqa: BLE001
        explain(exc)
        failures += 1

    print("=" * 64)
    if failures:
        say(FAIL, "connection FAILED — fix the issues above")
        return 1
    say(OK, "connection healthy — run: uvicorn app.main:app --reload")
    return 0


if __name__ == "__main__":
    sys.exit(main())
