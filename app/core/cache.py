"""Small Redis-backed response cache for read-heavy API endpoints.

The cache is best-effort: any Redis failure falls through to the database so a
cache outage never takes the API down. Keys are namespaced per user, so a single
`invalidate_user` call drops every cached read model for that account. We also
call it whenever a live SSE event is published, which keeps the cache coherent
with changes made by the worker and listener processes.
"""

from __future__ import annotations

import json
from typing import Any

from app.core.logging import get_logger
from app.db.session import get_redis

log = get_logger(__name__)

_PREFIX = "cache:v1:"


def user_key(user_id: int, *parts: str | int) -> str:
    """Build a stable, per-user cache key."""
    suffix = ":".join(str(part) for part in parts)
    return f"u:{user_id}:{suffix}"


async def cache_get(key: str) -> Any | None:
    """Return a cached JSON value, or None on a miss or cache failure."""
    try:
        redis = await get_redis()
        raw = await redis.get(_PREFIX + key)
    except Exception as exc:  # noqa: BLE001 - cache must fail open
        log.warning("cache_get_failed", key=key, error_type=type(exc).__name__)
        return None
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


async def cache_set(key: str, value: Any, ttl: int) -> None:
    """Store a JSON-serializable value under `key` for `ttl` seconds."""
    try:
        redis = await get_redis()
        await redis.set(_PREFIX + key, json.dumps(value), ex=ttl)
    except Exception as exc:  # noqa: BLE001 - cache must fail open
        log.warning("cache_set_failed", key=key, error_type=type(exc).__name__)


async def cache_delete(*keys: str) -> None:
    """Delete specific cache keys, ignoring failures."""
    if not keys:
        return
    try:
        redis = await get_redis()
        await redis.delete(*[_PREFIX + key for key in keys])
    except Exception as exc:  # noqa: BLE001 - cache must fail open
        log.warning("cache_delete_failed", error_type=type(exc).__name__)


async def invalidate_user(user_id: int) -> None:
    """Drop every cached read model for a user.

    Uses SCAN so we never block Redis with KEYS, and clears in small batches.
    """
    try:
        redis = await get_redis()
        pattern = f"{_PREFIX}u:{user_id}:*"
        pending: list[str] = []
        async for key in redis.scan_iter(match=pattern, count=100):
            pending.append(key)
            if len(pending) >= 100:
                await redis.delete(*pending)
                pending.clear()
        if pending:
            await redis.delete(*pending)
    except Exception as exc:  # noqa: BLE001 - cache must fail open
        log.warning(
            "cache_invalidate_failed", user_id=user_id, error_type=type(exc).__name__
        )
