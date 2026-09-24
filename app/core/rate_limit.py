"""Small process-local limiter for the local AI test surface.

Replace this with Redis-backed distributed limiting when running multiple
workers. The interface is intentionally tiny so the route code does not depend
on a particular cache implementation.
"""

from __future__ import annotations

import time

_BUCKETS: dict[str, tuple[float, int]] = {}


def allow_event(key: str, *, limit: int = 30, window_seconds: int = 60) -> bool:
    now = time.monotonic()
    started, count = _BUCKETS.get(key, (now, 0))
    if now - started >= window_seconds:
        started, count = now, 0
    if len(_BUCKETS) > 10_000:
        cutoff = now - window_seconds
        for old_key, (old_started, _) in list(_BUCKETS.items()):
            if old_started < cutoff:
                _BUCKETS.pop(old_key, None)
    if count >= limit:
        _BUCKETS[key] = (started, count)
        return False
    _BUCKETS[key] = (started, count + 1)
    return True


def reset_rate_limits() -> None:
    _BUCKETS.clear()
