"""Points-growth analytics — snapshot recording and history aggregation.

Balances are read live from Kick and cached for the UI (see
``app/api/v1/channels.py``). This module layers a persisted, append-only history
on top so users can see how their points have grown over time.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import utcnow
from app.db.models import PointsSnapshot

# Supported windows → (span, bucket size). Short windows are thinned by their
# bucket so a handful of recent readings still draw a line; day windows collapse
# to one reading per day. Keys are the public ``range`` query values.
_RANGES: dict[str, tuple[timedelta, timedelta]] = {
    "1h": (timedelta(hours=1), timedelta(minutes=5)),
    "24h": (timedelta(hours=24), timedelta(hours=1)),
    "7d": (timedelta(days=7), timedelta(days=1)),
    "30d": (timedelta(days=30), timedelta(days=1)),
}
_RANGE_BUCKET_LABELS = {
    "1h": "5m",
    "24h": "1h",
    "7d": "1d",
    "30d": "1d",
}
DEFAULT_RANGE = "24h"

# Ordered front-to-back; the API uses this to validate the query parameter.
SUPPORTED_RANGES: tuple[str, ...] = tuple(_RANGES)


def _bucket_start(moment: datetime, size: timedelta) -> datetime:
    """Floor ``moment`` to the start of its bucket.

    Uses calendar fields rather than epoch arithmetic so it stays correct for
    both timezone-aware (Postgres) and naive (SQLite) datetimes.
    """
    if size >= timedelta(days=1):
        return moment.replace(hour=0, minute=0, second=0, microsecond=0)
    if size >= timedelta(hours=1):
        return moment.replace(minute=0, second=0, microsecond=0)
    minutes = max(1, int(size.total_seconds() // 60))
    return moment.replace(
        minute=(moment.minute // minutes) * minutes, second=0, microsecond=0
    )


def _per_bucket_series(
    rows: list[PointsSnapshot], size: timedelta
) -> dict[datetime, PointsSnapshot]:
    """Map each bucket to the last snapshot recorded in it.

    ``rows`` must be ordered oldest-first so later rows overwrite earlier ones.
    """
    per_bucket: dict[datetime, PointsSnapshot] = {}
    for row in rows:
        per_bucket[_bucket_start(row.recorded_at, size)] = row
    return per_bucket


async def get_points_history(
    db: AsyncSession,
    user_id: int,
    range_key: str = DEFAULT_RANGE,
    channel_id: int | None = None,
) -> dict[str, Any]:
    """Aggregate a user's snapshots into a growth dashboard payload.

    ``range_key`` selects the window (``1h``, ``24h``, ``7d`` or ``30d``); an
    unknown value falls back to the default. Returns per-channel series (the last
    reading of each bucket), a combined total series (carrying each channel's
    last known balance forward so channels recorded in different buckets still
    sum cleanly), and headline totals for the window.
    """
    if range_key not in _RANGES:
        range_key = DEFAULT_RANGE
    window, bucket = _RANGES[range_key]

    start = utcnow() - window
    filters = [
        PointsSnapshot.user_id == user_id,
        PointsSnapshot.recorded_at >= start,
    ]
    if channel_id is not None:
        filters.append(PointsSnapshot.channel_id == channel_id)

    result = await db.execute(
        select(PointsSnapshot)
        .where(*filters)
        .order_by(PointsSnapshot.recorded_at.asc(), PointsSnapshot.id.asc())
    )
    snapshots = list(result.scalars().all())

    by_channel: dict[int, list[PointsSnapshot]] = {}
    for snapshot in snapshots:
        by_channel.setdefault(snapshot.channel_id, []).append(snapshot)

    channels: list[dict[str, Any]] = []
    per_bucket_by_channel: dict[int, dict[datetime, PointsSnapshot]] = {}
    all_buckets: set[datetime] = set()
    for cid, rows in by_channel.items():
        per_bucket = _per_bucket_series(rows, bucket)
        if not per_bucket:
            continue
        per_bucket_by_channel[cid] = per_bucket
        ordered = sorted(per_bucket)
        all_buckets.update(ordered)
        first_balance = per_bucket[ordered[0]].balance
        last_balance = per_bucket[ordered[-1]].balance
        channels.append(
            {
                "channel_id": cid,
                "slug": rows[-1].channel_slug,
                "current": last_balance,
                "start": first_balance,
                "growth": last_balance - first_balance,
                "series": [
                    {"at": moment.isoformat(), "balance": per_bucket[moment].balance}
                    for moment in ordered
                ],
            }
        )

    channels.sort(key=lambda item: item["channel_id"])

    # Combined series: carry each channel's most recent balance forward so a
    # channel last seen in an earlier bucket still contributes to later totals.
    totals_series: list[dict[str, Any]] = []
    running: dict[int, int] = {}
    for moment in sorted(all_buckets):
        for cid, per_bucket in per_bucket_by_channel.items():
            if moment in per_bucket:
                running[cid] = per_bucket[moment].balance
        totals_series.append(
            {"at": moment.isoformat(), "total": sum(running.values())}
        )

    total_current = sum(ch["current"] for ch in channels)
    total_start = sum(ch["start"] for ch in channels)
    growth = total_current - total_start
    growth_pct = round(growth / total_start * 100, 1) if total_start else None

    return {
        "range": range_key,
        "bucket": _RANGE_BUCKET_LABELS[range_key],
        "totals": {
            "current": total_current,
            "start": total_start,
            "growth": growth,
            "growth_pct": growth_pct,
            "snapshots": len(snapshots),
        },
        "series": totals_series,
        "channels": channels,
    }
