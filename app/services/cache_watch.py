"""Bridge Postgres changes to Redis cache invalidation.

Tracked tables (vote, channel, prediction, user_settings, kick_token, "user")
carry AFTER triggers that LISTEN/NOTIFY on ``db_cache_invalidate``. This module
owns the consumer: a single dedicated asyncpg connection LISTENs to that
channel and drops the affected user's Redis caches — so cached reads always
reflect whatever was just written, no matter who wrote it (API, worker,
listener, or a direct SQL edit).

Best-effort by design: if Postgres or the listener is down, caches self-heal
from their (short) TTLs.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from sqlalchemy import select

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models import Channel, User
from app.db.session import get_session

log = get_logger(__name__)

_NOTIFY_CHANNEL = "db_cache_invalidate"

_pending: set[asyncio.Task] = set()


def _pg_dsn() -> str:
    """SQLAlchemy URL -> asyncpg client DSN (no driver prefix)."""
    dsn = str(get_settings().database_url)
    return dsn.replace("postgresql+asyncpg://", "postgresql://", 1)


async def _affected_user_ids(payload: dict[str, Any]) -> list[int]:
    """Resolve the user(s) a change notification applies to."""
    raw = payload.get("user_id")
    if raw:
        try:
            return [int(raw)]
        except (TypeError, ValueError):
            return []
    slug = payload.get("channel_slug")
    if not slug:
        return []
    async with get_session() as db:
        result = await db.execute(
            select(User.id)
            .join(Channel, Channel.user_id == User.id)
            .where(Channel.channel_slug == str(slug))
        )
        return list(result.scalars().all())


async def _invalidate_payload(payload: dict[str, Any]) -> None:
    from app.core.cache import invalidate_user

    user_ids = await _affected_user_ids(payload)
    if not user_ids:
        return
    for user_id in user_ids:
        await invalidate_user(user_id)
    log.debug(
        "cache_invalidated_from_db",
        table=payload.get("table"),
        user_ids=user_ids,
    )


async def _notify_handler(
    _conn: Any, _pid: int, _channel: str, payload: str
) -> None:
    """asyncpg invokes this synchronously in the loop — delegate as a task."""
    try:
        data = json.loads(payload or "{}")
    except (TypeError, ValueError):
        log.warning("cache_invalidate_bad_payload", payload=payload)
        return
    task = asyncio.get_running_loop().create_task(_invalidate_payload(data))
    _pending.add(task)
    task.add_done_callback(_pending.discard)


async def watch_db_changes(stop: asyncio.Event) -> None:
    """Listen for DB change notifications until `stop` is set.

    Reconnects with exponential backoff if the connection drops.
    """
    import asyncpg

    backoff = 1.0
    while not stop.is_set():
        try:
            conn = await asyncpg.connect(_pg_dsn())
        except Exception as exc:  # noqa: BLE001 - retry forever
            log.warning(
                "cache_watch_connect_failed", error_type=type(exc).__name__
            )
            try:
                await asyncio.wait_for(stop.wait(), timeout=min(backoff, 30.0))
            except asyncio.TimeoutError:
                backoff = min(backoff * 2, 30.0)
                continue
            return

        backoff = 1.0
        try:
            await conn.add_listener(_NOTIFY_CHANNEL, _notify_handler)
            log.info("cache_watch_listening", channel=_NOTIFY_CHANNEL)
            while not stop.is_set():
                try:
                    await asyncio.wait_for(stop.wait(), timeout=30.0)
                except asyncio.TimeoutError:
                    pass
                if stop.is_set():
                    break
                # Touch the connection so a dropped backend is detected.
                await conn.execute("SELECT 1")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - reconnect forever
            log.warning(
                "cache_watch_error",
                error_type=type(exc).__name__,
                error=str(exc),
            )
        finally:
            try:
                await conn.remove_listener(_NOTIFY_CHANNEL, _notify_handler)
            except Exception:  # noqa: BLE001 - connection may already be dead
                pass
            try:
                await conn.close()
            except Exception:  # noqa: BLE001
                pass