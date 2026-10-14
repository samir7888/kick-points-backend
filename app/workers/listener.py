"""Listener manager — watches channels for predictions and dispatches vote tasks.

Run as: python -m app.workers.listener
"""

from __future__ import annotations

import asyncio
import signal
from typing import Any

from sqlalchemy import select, text

from app.core.config import get_settings
from app.core.errors import KickSchemaError
from app.core.logging import configure_logging, get_logger
from app.core.metrics import CIRCUIT_BREAKER_OPEN, mark_metrics_process_dead
from app.core.security import decrypt_token
from app.db.models import Channel, KickToken, TokenHealth, User, UserSettings
from app.db.session import get_engine, get_redis, get_session
from app.services.plans import channel_limit
from app.services.predictions import upsert_prediction

configure_logging()
log = get_logger(__name__)
settings = get_settings()

_running: dict[str, asyncio.Task[Any]] = {}
_stop_event = asyncio.Event()


async def _get_eligible_channels() -> list[dict[str, Any]]:
    """Return list of {slug, channel_id, kick_channel_id} for active, unpaused channels."""
    async with get_session() as db:
        result = await db.execute(
            select(Channel, User, UserSettings, KickToken)
            .join(User, Channel.user_id == User.id)
            .join(UserSettings, UserSettings.user_id == User.id, isouter=True)
            .join(KickToken, KickToken.user_id == User.id, isouter=True)
            .where(
                Channel.enabled == True,  # noqa: E712
                Channel.deleted_at.is_(None),
                User.deleted_at.is_(None),
            )
            .order_by(Channel.created_at.asc(), Channel.id.asc())
        )
        rows = result.all()

        eligible: dict[str, dict[str, Any]] = {}
        count_by_user: dict[int, int] = {}
        for ch, user, us, kt in rows:
            current_count = count_by_user.get(user.id, 0)
            count_by_user[user.id] = current_count + 1
            if current_count >= channel_limit(user):
                continue
            if ch.paused_reason is not None:
                continue
            if kt is None or kt.health != TokenHealth.OK:
                continue
            if us and us.paused:
                continue
            # Distinct by slug
            if ch.channel_slug not in eligible:
                eligible[ch.channel_slug] = {
                    "slug": ch.channel_slug,
                    "kick_channel_id": ch.kick_channel_id,
                }
        return list(eligible.values())


async def _get_eligible_users_for_channel(channel_slug: str) -> list[dict]:
    """Return eligible (user_id, channel_id) pairs for a channel slug."""
    async with get_session() as db:
        result = await db.execute(
            select(Channel, User, UserSettings, KickToken)
            .join(User, User.id == Channel.user_id)
            .join(UserSettings, UserSettings.user_id == Channel.user_id, isouter=True)
            .join(KickToken, KickToken.user_id == Channel.user_id, isouter=True)
            .where(
                Channel.channel_slug == channel_slug,
                Channel.enabled == True,  # noqa: E712
                Channel.deleted_at.is_(None),
                User.deleted_at.is_(None),
            )
            .order_by(Channel.created_at.asc(), Channel.id.asc())
        )
        rows = result.all()
        users = []
        count_by_user: dict[int, int] = {}
        for ch, user, us, kt in rows:
            if user.id not in count_by_user:
                enabled_result = await db.execute(
                    select(Channel.id)
                    .where(
                        Channel.user_id == user.id,
                        Channel.enabled.is_(True),
                    )
                    .order_by(Channel.created_at.asc(), Channel.id.asc())
                )
                count_by_user[user.id] = channel_limit(user)
                if ch.id not in enabled_result.scalars().all()[: channel_limit(user)]:
                    continue
            if ch.paused_reason is not None:
                continue
            if kt is None or kt.health != TokenHealth.OK:
                continue
            if us and us.paused:
                continue
            users.append(
                {
                    "user_id": user.id,
                    "channel_id": ch.id,
                    "access_token": decrypt_token(kt.access_token_enc),
                }
            )
        return users


async def _enqueue_prediction_event(event, arq_pool) -> None:
    """Persist a prediction event and enqueue idempotent user jobs."""
    async with get_session() as db:
        async with db.begin():
            prediction = await upsert_prediction(event, db)
            prediction_id = prediction.id

    await publish_prediction_event(event)
    if event.state.upper() in ("ACTIVE", "OPEN"):
        for user in await _get_eligible_users_for_channel(event.channel_slug):
            await arq_pool.enqueue_job(
                "vote_task",
                user["user_id"],
                user["channel_id"],
                prediction_id,
                _job_id=f"vote:{user['user_id']}:{prediction_id}",
            )
    elif event.state.upper() in ("RESOLVED", "CANCELLED"):
        await arq_pool.enqueue_job(
            "settle_task",
            prediction_id,
            _job_id=f"settle:{prediction_id}",
        )


async def publish_prediction_event(event) -> None:
    """Publish prediction state to every subscribed user of the channel."""
    from app.api.v1.ops import publish_sse

    users = await _get_eligible_users_for_channel(event.channel_slug)
    for user in users:
        await publish_sse(
            user["user_id"],
            "prediction_updated",
            {
                "channel_slug": event.channel_slug,
                "prediction_id": event.kick_prediction_id,
                "state": event.state.lower(),
            },
        )


async def _listen_channel(slug: str) -> None:
    """Listen to a single channel and dispatch vote tasks on new predictions."""
    log.info("listener_started", slug=slug)
    redis = await get_redis()
    await redis.sadd("listeners:active", slug)  # type: ignore[misc]

    try:
        from app.integrations.kick.gateway import get_kick_gateway

        gateway = get_kick_gateway()

        from arq import create_pool

        from app.workers.settings import WorkerSettings

        arq_pool = await create_pool(WorkerSettings.redis_settings)
        backoff = 1
        iterator = gateway.listen(slug).__aiter__()
        event_task = asyncio.create_task(iterator.__anext__())
        try:
            while not _stop_event.is_set():
                done, _ = await asyncio.wait({event_task}, timeout=30)
                if done:
                    try:
                        event = event_task.result()
                    except StopAsyncIteration:
                        await asyncio.sleep(backoff)
                        backoff = min(backoff * 2, 60)
                        iterator = gateway.listen(slug).__aiter__()
                        event_task = asyncio.create_task(iterator.__anext__())
                        continue
                    event_task = asyncio.create_task(iterator.__anext__())
                    backoff = 1
                else:
                    eligible_users = await _get_eligible_users_for_channel(slug)
                    if not eligible_users:
                        continue
                    event = await gateway.get_latest_prediction(
                        slug, eligible_users[0]["access_token"]
                    )
                    if event is None:
                        continue

                log.info(
                    "prediction_event",
                    slug=slug,
                    pred_id=event.kick_prediction_id,
                    state=event.state,
                )
                await _enqueue_prediction_event(event, arq_pool)
        finally:
            if not event_task.done():
                event_task.cancel()
                await asyncio.gather(event_task, return_exceptions=True)
            await arq_pool.close()
    except asyncio.CancelledError:
        raise
    except KickSchemaError as exc:
        await redis.set("circuit_breaker:kick", "open", ex=300)
        CIRCUIT_BREAKER_OPEN.set(1)
        from app.api.v1.ops import publish_system_sse

        await publish_system_sse(
            "voting_paused",
            {"paused": True, "reason": "kick_schema_error"},
        )
        log.error(
            "listener_schema_error",
            slug=slug,
            error_type=type(exc).__name__,
        )
    except Exception as exc:
        log.error("listener_error", slug=slug, error_type=type(exc).__name__)
    finally:
        await redis.srem("listeners:active", slug)  # type: ignore[misc]
        log.info("listener_stopped", slug=slug)


async def _manager_loop() -> None:
    """Reconcile listeners every 30 seconds."""
    while not _stop_event.is_set():
        try:
            eligible = await _get_eligible_channels()
            wanted = {ch["slug"] for ch in eligible}
            current = set(_running.keys())

            # Start new listeners
            for ch_data in eligible:
                slug = ch_data["slug"]
                if slug not in _running or _running[slug].done():
                    task = asyncio.create_task(
                        _listen_channel(slug), name=f"listener:{slug}"
                    )
                    _running[slug] = task
                    log.info("listener_started_task", slug=slug)

            # Stop unwanted listeners
            for slug in list(current - wanted):
                if slug in _running:
                    task = _running.pop(slug)
                    if not task.done():
                        task.cancel()
                        log.info("listener_cancelled", slug=slug)

        except Exception as exc:
            log.error("manager_loop_error", error=str(exc))

        await asyncio.sleep(30)


async def main() -> None:
    loop = asyncio.get_event_loop()

    def _shutdown(*_):
        log.info("shutdown_signal")
        _stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _shutdown)
        except NotImplementedError:
            pass  # Windows

    async with get_engine().connect() as connection:
        acquired = await connection.scalar(
            text("SELECT pg_try_advisory_lock(:lock_id)"),
            {"lock_id": 781204515},
        )
        # The advisory lock is session-scoped and survives a commit, so commit
        # right away to avoid holding an idle-in-transaction snapshot for the
        # whole lifetime of the listener.
        await connection.commit()
        if not acquired:
            log.info("listener_manager_already_active")
            return
        try:
            await _manager_loop()
        finally:
            await connection.execute(
                text("SELECT pg_advisory_unlock(:lock_id)"),
                {"lock_id": 781204515},
            )
            await connection.commit()

    # Cancel all running listeners
    for task in _running.values():
        task.cancel()
    if _running:
        await asyncio.gather(*_running.values(), return_exceptions=True)
    mark_metrics_process_dead()


if __name__ == "__main__":
    asyncio.run(main())
