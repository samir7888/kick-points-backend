"""Redis-backed rate limiting."""

from __future__ import annotations

from fastapi import Request

from app.db.session import get_redis


async def _check_rate_limit(
    key: str, limit: int, window: int, request: Request
) -> bool:
    """Return True if allowed, False if rate-limited. window in seconds."""
    redis = await get_redis()
    current = await redis.incr(key)
    if current == 1:
        await redis.expire(key, window)
    return int(current) <= limit


async def rate_limit_ip(request: Request, limit: int = 20, window: int = 60) -> None:
    """Rate limit by IP address. Raises 429 if exceeded."""
    from fastapi import HTTPException

    ip = request.client.host if request.client else "unknown"
    key = f"rl:ip:{ip}:{request.url.path}"
    if not await _check_rate_limit(key, limit, window, request):
        raise HTTPException(status_code=429, detail="Too many requests")


async def rate_limit_user(
    user_id: int, action: str, limit: int = 30, window: int = 60
) -> None:
    from fastapi import HTTPException

    key = f"rl:user:{user_id}:{action}"
    redis = await get_redis()
    current = await redis.incr(key)
    if current == 1:
        await redis.expire(key, window)
    if int(current) > limit:
        raise HTTPException(status_code=429, detail="Too many requests")
