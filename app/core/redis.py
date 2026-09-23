"""Cache-first context bootstrap module.

Provides an async cache interface using Redis (via redis.asyncio) if available,
with automatic fallback to an in-memory TTL cache when Redis is disconnected or unavailable.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)


class InMemoryTTLCache:
    """In-memory cache with TTL expiration fallback."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[Any, float]] = {}

    async def get(self, key: str) -> Any | None:
        if key not in self._store:
            return None
        val, expiry = self._store[key]
        if expiry > 0 and time.time() > expiry:
            del self._store[key]
            return None
        return val

    async def set(self, key: str, value: Any, ttl_seconds: int = 60) -> None:
        expiry = time.time() + ttl_seconds if ttl_seconds > 0 else 0.0
        self._store[key] = (value, expiry)

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)

    async def clear(self) -> None:
        self._store.clear()


class CacheClient:
    """Unified cache wrapper supporting Redis with in-memory fallback."""

    def __init__(self) -> None:
        self._fallback = InMemoryTTLCache()
        self._redis = None
        self._initialized = False

    async def _init_redis(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        try:
            import redis.asyncio as aioredis  # type: ignore[import-untyped]
            self._redis = aioredis.from_url(settings.redis_url, decode_responses=True)
            await self._redis.ping()
            logger.info("Connected to Redis cache at %s", settings.redis_url)
        except Exception as exc:
            logger.warning("Redis unavailable (%s); using in-memory TTL cache.", exc)
            self._redis = None

    async def get(self, key: str) -> Any | None:
        await self._init_redis()
        if self._redis:
            try:
                data = await self._redis.get(key)
                if data is not None:
                    return json.loads(data)
            except Exception as exc:
                logger.warning("Redis get failed (%s); reading fallback cache.", exc)
        return await self._fallback.get(key)

    async def set(self, key: str, value: Any, ttl_seconds: int = 60) -> None:
        await self._init_redis()
        if self._redis:
            try:
                serialized = json.dumps(value, default=str)
                await self._redis.set(key, serialized, ex=ttl_seconds)
                return
            except Exception as exc:
                logger.warning("Redis set failed (%s); writing to fallback cache.", exc)
        await self._fallback.set(key, value, ttl_seconds=ttl_seconds)

    async def delete(self, key: str) -> None:
        await self._init_redis()
        if self._redis:
            try:
                await self._redis.delete(key)
            except Exception:
                pass
        await self._fallback.delete(key)


cache_client = CacheClient()

