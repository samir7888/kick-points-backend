"""Predictions and votes router."""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.cache import cache_get, cache_set, user_key
from app.db.models import Channel, Prediction, User, Vote, VoteStatus
from app.db.session import get_db_session
from app.services.predictions import (
    _vote_dict,
    get_channel_predictions,
    get_channel_stats,
)

router = APIRouter(tags=["predictions"])

# Cached only for the first page so the initial view of each page is instant
# while later pages always read fresh data. Live SSE events invalidate.
_FIRST_PAGE_TTL = 10
_STATS_TTL = 30
_ACTIVE_TTL = 10


@router.get("/channels/{channel_id}/predictions")
async def list_channel_predictions(
    channel_id: int,
    page: int = Query(1, ge=1),
    limit: int = Query(10, le=100, ge=1),
    status: str | None = Query(None, pattern="^(open|resolved|cancelled)$"),
    voted: bool | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Page-numbered predictions for a channel with caller's vote.

    History only exists from when the channel was first enabled.
    """
    cache_key = user_key(
        user.id,
        "history",
        channel_id,
        status or "all",
        "voted" if voted else "all",
        limit,
        page,
    )
    if page == 1:
        cached = await cache_get(cache_key)
        if cached is not None:
            return cast(dict[str, Any], cached)

    items, total = await get_channel_predictions(
        channel_id, user.id, db, page=page, limit=limit, status=status, voted=voted
    )
    payload = {
        "items": items,
        "page": page,
        "limit": limit,
        "total": total,
        "pages": (total + limit - 1) // limit,
    }
    if page == 1:
        await cache_set(cache_key, payload, _FIRST_PAGE_TTL)
    return payload


@router.get("/channels/{channel_id}/predictions/{prediction_id}")
async def get_prediction(
    channel_id: int,
    prediction_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    from app.core.errors import NotFoundError
    from app.db.models import Channel, Prediction

    ch_result = await db.execute(
        select(Channel).where(
            Channel.id == channel_id,
            Channel.user_id == user.id,
            Channel.deleted_at.is_(None),
        )
    )
    ch = ch_result.scalar_one_or_none()
    if not ch:
        raise NotFoundError("Channel not found")

    pred_result = await db.execute(
        select(Prediction).where(
            Prediction.id == prediction_id,
            Prediction.channel_slug == ch.channel_slug,
        )
    )
    pred = pred_result.scalar_one_or_none()
    if not pred:
        raise NotFoundError("Prediction not found")

    vote_result = await db.execute(
        select(Vote).where(Vote.prediction_id == pred.id, Vote.user_id == user.id)
    )
    vote = vote_result.scalar_one_or_none()

    return {
        "id": pred.id,
        "kick_prediction_id": pred.kick_prediction_id,
        "title": pred.title,
        "outcomes": pred.outcomes,
        "state": pred.state.value,
        "winning_outcome_id": pred.winning_outcome_id,
        "opened_at": pred.opened_at.isoformat(),
        "closes_at": pred.closes_at.isoformat() if pred.closes_at else None,
        "resolved_at": pred.resolved_at.isoformat() if pred.resolved_at else None,
        "my_vote": _vote_dict(vote) if vote else None,
    }


@router.get("/channels/{channel_id}/stats")
async def channel_stats(
    channel_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    key = user_key(user.id, "stats", channel_id)
    cached = await cache_get(key)
    if cached is not None:
        return cast(dict[str, Any], cached)
    stats = await get_channel_stats(channel_id, user.id, db)
    await cache_set(key, stats, _STATS_TTL)
    return stats


@router.get("/predictions/active")
async def active_predictions(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> list[dict]:
    from app.db.models import Prediction, PredictionState

    key = user_key(user.id, "active")
    cached = await cache_get(key)
    if cached is not None:
        return cast(list[dict[str, Any]], cached)

    ch_result = await db.execute(
        select(Channel).where(
            Channel.user_id == user.id,
            Channel.enabled == True,  # noqa: E712
            Channel.deleted_at.is_(None),
        )
    )
    channels = ch_result.scalars().all()
    slugs = [ch.channel_slug for ch in channels]

    pred_result = await db.execute(
        select(Prediction)
        .where(
            Prediction.channel_slug.in_(slugs),
            Prediction.state == PredictionState.open,
        )
        .order_by(Prediction.opened_at.desc(), Prediction.id.desc())
    )
    preds = pred_result.scalars().all()

    payload = [
        {
            "id": p.id,
            "channel_slug": p.channel_slug,
            "kick_prediction_id": p.kick_prediction_id,
            "title": p.title,
            "state": p.state.value,
            "opened_at": p.opened_at.isoformat(),
            "closes_at": p.closes_at.isoformat() if p.closes_at else None,
        }
        for p in preds
    ]
    await cache_set(key, payload, _ACTIVE_TTL)
    return payload


@router.get("/votes")
async def list_votes(
    channel_id: int | None = None,
    status: VoteStatus | None = None,
    page: int = Query(1, ge=1),
    limit: int = Query(10, le=100, ge=1),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """All votes across the user's channels with page-number pagination."""
    cache_key = user_key(
        user.id,
        "votes",
        channel_id if channel_id is not None else "all",
        status.value if status else "all",
        limit,
        page,
    )
    if page == 1:
        cached = await cache_get(cache_key)
        if cached is not None:
            return cast(dict[str, Any], cached)

    filters = [Vote.user_id == user.id]
    if channel_id is not None:
        filters.append(Vote.channel_id == channel_id)
    if status:
        filters.append(Vote.status == status)

    total_result = await db.execute(
        select(func.count()).select_from(Vote).where(*filters)
    )
    total = int(total_result.scalar_one())

    result = await db.execute(
        select(Vote)
        .where(*filters)
        .order_by(Vote.created_at.desc(), Vote.id.desc())
        .offset((page - 1) * limit)
        .limit(limit)
    )
    votes = list(result.scalars().all())

    pred_ids = {v.prediction_id for v in votes}
    preds_by_id: dict[int, Prediction] = {}
    if pred_ids:
        pred_result = await db.execute(
            select(Prediction).where(Prediction.id.in_(pred_ids))
        )
        preds_by_id = {p.id: p for p in pred_result.scalars().all()}
    items = [_vote_dict(v, preds_by_id.get(v.prediction_id)) for v in votes]

    payload = {
        "items": items,
        "page": page,
        "limit": limit,
        "total": total,
        "pages": (total + limit - 1) // limit,
    }
    if page == 1:
        await cache_set(cache_key, payload, _FIRST_PAGE_TTL)
    return payload
