"""SSE stream router, health checks, metrics, and admin routes."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    check_csrf,
    get_current_admin,
    get_current_user,
    rate_limit_for_user,
)
from app.core.cache import invalidate_user
from app.core.logging import get_logger
from app.core.metrics import (
    CIRCUIT_BREAKER_OPEN,
    LISTENER_COUNT,
    QUEUE_DEPTH,
    render_metrics,
)
from app.core.security import utcnow
from app.db.models import User, WebhookEvent
from app.db.session import get_db_session, get_redis
from app.services.audit import record_audit
from app.services.runtime_flags import is_kill_switch_enabled, set_kill_switch_enabled

router = APIRouter(tags=["ops"])
log = get_logger(__name__)


@router.get("/healthz", include_in_schema=False)
async def healthz() -> dict:
    return {"status": "ok"}


async def dependency_readiness(db: AsyncSession) -> tuple[bool, dict[str, str]]:
    checks = {"database": "ok", "redis": "ok"}
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        checks["database"] = "unavailable"
        await db.rollback()
    try:
        redis = await get_redis()
        await redis.ping()
    except Exception:
        checks["redis"] = "unavailable"
    return all(value == "ok" for value in checks.values()), checks


@router.get("/readyz", include_in_schema=False)
async def readyz(db: AsyncSession = Depends(get_db_session)) -> JSONResponse:
    ready, checks = await dependency_readiness(db)
    return JSONResponse(
        {"status": "ready" if ready else "fail", "checks": checks},
        status_code=200 if ready else 503,
    )


@router.get(
    "/metrics",
    summary="Prometheus metrics",
    description="Prometheus metrics endpoint restricted to administrators.",
)
async def metrics(
    admin: User = Depends(get_current_admin),
) -> StreamingResponse:
    from prometheus_client import CONTENT_TYPE_LATEST

    redis = await get_redis()
    QUEUE_DEPTH.set(await redis.zcard("arq:queue"))
    LISTENER_COUNT.set(await redis.scard("listeners:active"))
    CIRCUIT_BREAKER_OPEN.set(
        1 if await redis.get("circuit_breaker:kick") == "open" else 0
    )
    return StreamingResponse(
        iter([render_metrics()]),
        media_type=CONTENT_TYPE_LATEST,
    )


@router.get("/stream")
async def sse_stream(user: User = Depends(get_current_user)) -> StreamingResponse:
    """Server-Sent Events stream for the authenticated user."""
    redis = await get_redis()

    async def event_generator():
        pubsub = redis.pubsub()
        channel = f"user:{user.id}"
        system_channel = "system:voting_paused"
        await pubsub.subscribe(channel, system_channel)
        try:
            while True:
                # Heartbeat comment every 15 seconds
                timeout = 15.0
                try:
                    msg = await asyncio.wait_for(
                        pubsub.get_message(
                            ignore_subscribe_messages=True, timeout=timeout
                        ),
                        timeout=timeout + 1,
                    )
                except TimeoutError:
                    msg = None

                if msg is None:
                    yield ": heartbeat\n\n"
                    continue

                if msg["type"] == "message":
                    data = msg["data"]
                    yield f"data: {data}\n\n"
        finally:
            await pubsub.unsubscribe(channel, system_channel)
            await pubsub.aclose()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def publish_sse(user_id: int, event_type: str, data: dict) -> None:
    """Publish an SSE event to a user's Redis channel and drop their cache.

    Workers and the listener change data in their own processes; publishing an
    event is the one moment we know a user's cached reads are stale, so this is
    where cross-process invalidation happens.
    """
    redis = await get_redis()
    payload = json.dumps({"type": event_type, **data})
    await redis.publish(f"user:{user_id}", payload)
    await invalidate_user(user_id)


async def publish_system_sse(event_type: str, data: dict) -> None:
    """Publish a non-sensitive status update to every connected SSE client."""
    redis = await get_redis()
    await redis.publish(
        "system:voting_paused",
        json.dumps({"type": event_type, **data}),
    )


# ── Admin ─────────────────────────────────────────────────────────────────────


class KillSwitchBody(BaseModel):
    enabled: bool


@router.post(
    "/admin/kill-switch",
    dependencies=[
        Depends(check_csrf),
        Depends(rate_limit_for_user("admin_kill_switch", limit=10, window=60)),
    ],
)
async def set_kill_switch(
    body: KillSwitchBody,
    request: Request,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Enable or disable the global voting kill switch."""
    redis = await get_redis()
    await set_kill_switch_enabled(db, body.enabled)
    await record_audit(
        db,
        "kill_switch",
        user_id=admin.id,
        detail={"enabled": body.enabled},
        ip=request.client.host if request.client else None,
    )
    await db.commit()
    if body.enabled:
        await redis.set("admin:kill_switch", "1")
        log.info("kill_switch_enabled", admin_id=admin.id)
    else:
        await redis.delete("admin:kill_switch")
        log.info("kill_switch_disabled", admin_id=admin.id)
    breaker_open = await redis.get("circuit_breaker:kick") == "open"
    await publish_system_sse(
        "voting_paused",
        {
            "paused": body.enabled or breaker_open,
            "reason": (
                "kill_switch"
                if body.enabled
                else "circuit_breaker"
                if breaker_open
                else None
            ),
        },
    )
    return {"kill_switch": body.enabled}


@router.get("/admin/overview")
async def admin_overview(
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db_session),
) -> dict:

    redis = await get_redis()
    kill_switch = (
        await is_kill_switch_enabled(db) or await redis.get("admin:kill_switch") == "1"
    )
    breaker = await redis.get("circuit_breaker:kick")
    CIRCUIT_BREAKER_OPEN.set(1 if breaker == "open" else 0)

    user_count = (await db.execute(select(func.count(User.id)))).scalar_one()
    now = utcnow()
    premium_count = (
        await db.execute(
            select(func.count(User.id)).where(
                or_(
                    User.premium_until > now,
                    User.premium_grace_until > now,
                )
            )
        )
    ).scalar_one()

    # Listener count from Redis key
    listener_count = (await redis.scard("listeners:active")) or 0  # type: ignore[misc]
    queue_depth = await redis.zcard("arq:queue")
    LISTENER_COUNT.set(listener_count)
    QUEUE_DEPTH.set(queue_depth)
    queue_head = await redis.zrange("arq:queue", 0, 0, withscores=True)
    queue_lag_seconds = (
        max(0.0, utcnow().timestamp() - queue_head[0][1] / 1000) if queue_head else 0.0
    )
    unprocessed_webhooks = (
        await db.execute(
            select(func.count(WebhookEvent.id)).where(
                WebhookEvent.processed_at.is_(None)
            )
        )
    ).scalar_one()

    return {
        "users": user_count,
        "premium_users": premium_count,
        "active_listeners": listener_count,
        "kill_switch": kill_switch,
        "circuit_breaker": breaker or "closed",
        "canary_failures": int(await redis.get("canary:failures") or 0),
        "queue_depth": queue_depth,
        "queue_lag_seconds": queue_lag_seconds,
        "unprocessed_webhooks": unprocessed_webhooks,
    }
