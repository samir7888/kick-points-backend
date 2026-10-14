"""Tests for the Redis-backed response cache."""

import pytest

from app.core.cache import (
    cache_delete,
    cache_get,
    cache_set,
    invalidate_user,
    user_key,
)


@pytest.mark.asyncio
async def test_cache_round_trip():
    key = user_key(999_999, "roundtrip")
    await cache_set(key, {"hello": "world", "count": 3}, ttl=30)
    assert await cache_get(key) == {"hello": "world", "count": 3}
    await cache_delete(key)
    assert await cache_get(key) is None


@pytest.mark.asyncio
async def test_invalidate_user_clears_all_keys():
    user_id = 999_998
    await cache_set(user_key(user_id, "me"), {"id": user_id}, ttl=30)
    await cache_set(user_key(user_id, "channels"), [], ttl=30)
    await cache_set(user_key(user_id, "stats", 1), {"votes_placed": 1}, ttl=30)

    await invalidate_user(user_id)

    assert await cache_get(user_key(user_id, "me")) is None
    assert await cache_get(user_key(user_id, "channels")) is None
    assert await cache_get(user_key(user_id, "stats", 1)) is None


@pytest.mark.asyncio
async def test_invalidate_user_leaves_other_users_alone():
    keep = user_key(999_997, "me")
    drop = user_key(999_996, "me")
    await cache_set(keep, {"id": 999_997}, ttl=30)
    await cache_set(drop, {"id": 999_996}, ttl=30)

    await invalidate_user(999_996)

    assert await cache_get(keep) == {"id": 999_997}
    assert await cache_get(drop) is None
    await cache_delete(keep)
