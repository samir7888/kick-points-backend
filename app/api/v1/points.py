"""Points-growth analytics router."""

from __future__ import annotations

from typing import Any, Literal, cast

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.cache import cache_get, cache_set, user_key
from app.db.models import User
from app.db.session import get_db_session
from app.services.points import DEFAULT_RANGE, get_points_history

router = APIRouter(tags=["points"])

# Snapshots arrive every 15 minutes, so a short TTL keeps the chart responsive
# without ever showing stale data for long.
_HISTORY_TTL = 60

RangeKey = Literal["1h", "24h", "7d", "30d"]


@router.get("/points/history")
async def points_history(
    range: RangeKey = Query(DEFAULT_RANGE),
    channel_id: int | None = Query(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Channel-points growth for the caller, aggregated over ``range``.

    ``range`` is one of ``1h``, ``24h``, ``7d`` or ``30d``. Returns per-channel
    series, a combined total series, and headline totals. Growth exists only from
    the first recorded snapshot, so a freshly added channel starts with a single
    point.
    """
    key = user_key(user.id, "points-history", range, channel_id or "all")
    cached = await cache_get(key)
    if cached is not None:
        return cast(dict[str, Any], cached)

    payload = await get_points_history(
        db, user.id, range_key=range, channel_id=channel_id
    )
    await cache_set(key, payload, _HISTORY_TTL)
    return payload
