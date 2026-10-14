"""arq WorkerSettings."""

from __future__ import annotations

from arq.connections import RedisSettings
from arq.cron import cron

from app.core.config import get_settings
from app.workers.tasks import (
    canary_task,
    expiry_task,
    pending_vote_recovery_task,
    points_snapshot_task,
    settle_task,
    vote_task,
    webhook_retry_task,
)

settings = get_settings()


def _redis_settings() -> RedisSettings:
    # from_dsn honours the full URL: redis:// and rediss:// (TLS), username,
    # password, and the DB index. Render Key Value's internal URL
    # (redis://red-xxxx:6379) and TLS providers (rediss://...) both work.
    return RedisSettings.from_dsn(str(settings.redis_url))


class WorkerSettings:
    functions = [
        vote_task,
        settle_task,
        expiry_task,
        pending_vote_recovery_task,
        canary_task,
        points_snapshot_task,
        webhook_retry_task,
    ]
    redis_settings = _redis_settings()
    max_jobs = 20
    job_timeout = 120
    keep_result = 300
    cron_jobs = [
        cron(expiry_task, hour=0, minute=0),
        cron(pending_vote_recovery_task, minute=set(range(60))),
        cron(canary_task, minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55}),
        # Balance readings for the growth dashboard. Every 15 minutes so the
        # "last hour" window has several points to draw. A dedicated timeout
        # keeps a large channel fleet from being cut off by the default 120s.
        cron(points_snapshot_task, minute={0, 15, 30, 45}, timeout=600),
    ]

    @staticmethod
    async def on_startup(ctx: dict) -> None:
        from app.core.logging import configure_logging

        configure_logging()

    @staticmethod
    async def on_shutdown(ctx: dict) -> None:
        from app.core.metrics import mark_metrics_process_dead
        from app.db.session import close_redis

        await close_redis()
        mark_metrics_process_dead()
