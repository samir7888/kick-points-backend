"""Channels router."""

from __future__ import annotations

import asyncio
from typing import Any, cast

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import check_csrf, get_current_user, rate_limit_for_user
from app.core.cache import cache_get, cache_set, invalidate_user, user_key
from app.core.config import get_settings
from app.core.errors import PlanLimitError, TokenNeedsReauthError, ValidationError
from app.core.logging import get_logger
from app.core.security import decrypt_token, utcnow
from app.db.models import (
    Channel,
    KickToken,
    PausedReason,
    Prediction,
    PredictionState,
    User,
    UserSettings,
)
from app.db.session import get_db_session, get_redis
from app.integrations.kick.gateway import get_kick_gateway
from app.services.audit import record_audit
from app.services.auth import get_decrypted_token
from app.services.channels import (
    add_channel,
    get_channel_or_404,
    get_user_channels,
)
from app.services.plans import ensure_user_settings
from app.services.predictions import get_user_channel_stats

router = APIRouter(tags=["channels"])
log = get_logger(__name__)
settings = get_settings()

# Short TTLs keep live/active-prediction state fresh while collapsing the burst
# of duplicate reads each page load produces. Writes invalidate explicitly.
_CHANNELS_TTL = 15
_CHANNEL_TTL = 15
_STATS_TTL = 30
_POINTS_TTL = 30


class AddChannelBody(BaseModel):
    slug: str = Field(pattern=r"^[A-Za-z0-9_-]{1,50}$")
    vote_amount: int | None = Field(None, ge=settings.min_vote_amount)


class PatchChannelBody(BaseModel):
    vote_amount: int | None = Field(default=None, ge=settings.min_vote_amount)
    enabled: bool | None = None


class BulkAmountItem(BaseModel):
    id: int
    vote_amount: int | None = Field(default=None, ge=settings.min_vote_amount)


class BulkAmountBody(BaseModel):
    items: list[BulkAmountItem]


def _channel_dict(ch: Channel, user_settings: UserSettings | None) -> dict[str, Any]:
    default_amount = (
        user_settings.default_vote_amount if user_settings else settings.min_vote_amount
    )
    eff = ch.vote_amount if ch.vote_amount is not None else default_amount
    return {
        "id": ch.id,
        "slug": ch.channel_slug,
        "kick_channel_id": ch.kick_channel_id,
        "enabled": ch.enabled,
        "paused_reason": ch.paused_reason.value if ch.paused_reason else None,
        "vote_amount": ch.vote_amount,
        "effective_amount": eff,
        "created_at": ch.created_at.isoformat(),
    }


async def _voting_session_token(user_id: int, db: AsyncSession) -> str | None:
    """The kick.com site session token used to place votes, if one is set.

    Only this session credential can read the channel-points balance; a plain
    OAuth token gets rejected. Returns None when the user hasn't added one, in
    which case no balance is shown.
    """
    result = await db.execute(
        select(KickToken).where(KickToken.user_id == user_id)
    )
    kt = result.scalar_one_or_none()
    if kt is None or not kt.session_token_enc:
        return None
    return decrypt_token(kt.session_token_enc)


async def _channel_points_balance(
    user: User, slug: str, db: AsyncSession
) -> int | None:
    """Live Kick channel-points balance for ``slug``, cached briefly.

    Returns None if there is no vote session token or Kick doesn't respond —
    the UI then simply omits the number ("if it allows").
    """
    key = user_key(user.id, "points", slug)
    cached = await cache_get(key)
    if cached is not None:
        return cast(int, cached)
    token = await _voting_session_token(user.id, db)
    if token is None:
        return None
    try:
        gateway = get_kick_gateway()
        balance = await gateway.get_points_balance(slug, token)
    except Exception as exc:  # pragma: no cover - external failure path
        log.warning(
            "channel_points_balance_failed",
            slug=slug,
            error_type=type(exc).__name__,
        )
        return None
    if isinstance(balance, int):
        await cache_set(key, balance, _POINTS_TTL)
        return balance
    return None


@router.get("/channels")
async def list_channels(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> list[dict]:
    """List channels with listener state and the latest active prediction."""
    cached = await cache_get(user_key(user.id, "channels"))
    if cached is not None:
        return cast(list[dict[str, Any]], cached)

    channels = await get_user_channels(user.id, db)
    result = await db.execute(
        select(UserSettings).where(UserSettings.user_id == user.id)
    )
    us = result.scalar_one_or_none()
    if not us:
        us = await ensure_user_settings(user.id, db)
    await db.commit()
    slugs = [channel.channel_slug for channel in channels]
    active_predictions: dict[str, Prediction] = {}
    if slugs:
        prediction_result = await db.execute(
            select(Prediction)
            .where(
                Prediction.channel_slug.in_(slugs),
                Prediction.state == PredictionState.OPEN,
            )
            .order_by(Prediction.opened_at.desc(), Prediction.id.desc())
        )
        for prediction in prediction_result.scalars().all():
            active_predictions.setdefault(prediction.channel_slug, prediction)

    redis = await get_redis()
    listening_slugs = await redis.smembers("listeners:active")
    items: list[dict] = []
    balances: dict[str, int | None] = {}
    if channels:
        results = await asyncio.gather(
            *[
                _channel_points_balance(user, channel.channel_slug, db)
                for channel in channels
            ],
            return_exceptions=True,
        )
        for channel, balance in zip(channels, results):
            balances[channel.channel_slug] = (
                balance if isinstance(balance, int) else None
            )
    for channel in channels:
        item = _channel_dict(channel, us)
        item["live"] = channel.channel_slug in listening_slugs
        item["points_balance"] = balances.get(channel.channel_slug)
        active = active_predictions.get(channel.channel_slug)
        item["active_prediction"] = (
            {
                "id": active.id,
                "title": active.title,
                "state": active.state.value,
                "closes_at": (
                    active.closes_at.isoformat() if active.closes_at else None
                ),
            }
            if active
            else None
        )
        items.append(item)
    await cache_set(user_key(user.id, "channels"), items, _CHANNELS_TTL)
    return items


@router.get("/channels/stats")
async def channels_stats(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Aggregate vote stats for all of the user's channels in one request."""
    key = user_key(user.id, "channel-stats")
    cached = await cache_get(key)
    if cached is not None:
        return cast(dict[str, Any], cached)
    stats = await get_user_channel_stats(user.id, db)
    await cache_set(key, stats, _STATS_TTL)
    return stats


@router.get("/channels/{channel_id}")
async def get_channel(
    channel_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """A single channel with listener state and its latest active prediction."""
    key = user_key(user.id, "channel", channel_id)
    cached = await cache_get(key)
    if cached is not None:
        return cast(dict[str, Any], cached)

    ch = await get_channel_or_404(channel_id, user.id, db)
    us = await ensure_user_settings(user.id, db)
    await db.commit()
    prediction_result = await db.execute(
        select(Prediction)
        .where(
            Prediction.channel_slug == ch.channel_slug,
            Prediction.state == PredictionState.OPEN,
        )
        .order_by(Prediction.opened_at.desc(), Prediction.id.desc())
        .limit(1)
    )
    active = prediction_result.scalars().first()
    redis = await get_redis()
    listening_slugs = await redis.smembers("listeners:active")
    item = _channel_dict(ch, us)
    item["live"] = ch.channel_slug in listening_slugs
    item["points_balance"] = await _channel_points_balance(user, ch.channel_slug, db)
    item["active_prediction"] = (
        {
            "id": active.id,
            "title": active.title,
            "state": active.state.value,
            "closes_at": active.closes_at.isoformat() if active.closes_at else None,
        }
        if active
        else None
    )
    await cache_set(key, item, _CHANNEL_TTL)
    return item


@router.post(
    "/channels",
    dependencies=[
        Depends(check_csrf),
        Depends(rate_limit_for_user("channel_create", limit=5, window=60)),
    ],
    status_code=201,
)
async def create_channel(
    body: AddChannelBody,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    try:
        token = await get_decrypted_token(user.id, db)
    except ValueError as exc:
        raise TokenNeedsReauthError(
            "Connect your Kick account before adding channels"
        ) from exc
    ch = await add_channel(
        user,
        body.slug,
        body.vote_amount,
        db,
        gateway=get_kick_gateway(),
        token=token,
    )
    result = await db.execute(
        select(UserSettings).where(UserSettings.user_id == user.id)
    )
    us = result.scalar_one_or_none()
    await record_audit(
        db,
        "channel_add",
        user_id=user.id,
        detail={"channel_id": ch.id, "slug": ch.channel_slug},
    )
    await db.commit()
    await invalidate_user(user.id)
    return _channel_dict(ch, us)


@router.patch(
    "/channels/{channel_id}",
    dependencies=[
        Depends(check_csrf),
        Depends(rate_limit_for_user("channel_update")),
    ],
)
async def patch_channel(
    channel_id: int,
    body: PatchChannelBody,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    ch = await get_channel_or_404(channel_id, user.id, db)

    if body.enabled is True and ch.paused_reason == PausedReason.PLAN_LIMIT:
        raise PlanLimitError("Upgrade your plan to enable this channel")

    if body.vote_amount is not None:
        if body.vote_amount < settings.min_vote_amount:
            raise ValidationError(f"vote_amount >= {settings.min_vote_amount}")
        else:
            ch.vote_amount = body.vote_amount
    elif "vote_amount" in body.model_fields_set and body.vote_amount is None:
        ch.vote_amount = None

    if body.enabled is not None:
        ch.enabled = body.enabled
        if body.enabled and ch.paused_reason == PausedReason.USER:
            ch.paused_reason = None
        elif not body.enabled:
            ch.paused_reason = PausedReason.USER

    await record_audit(
        db,
        "channel_update",
        user_id=user.id,
        detail={
            "channel_id": channel_id,
            "vote_amount": ch.vote_amount,
            "enabled": ch.enabled,
        },
    )
    await db.commit()
    await invalidate_user(user.id)
    result = await db.execute(
        select(UserSettings).where(UserSettings.user_id == user.id)
    )
    us = result.scalar_one_or_none()
    return _channel_dict(ch, us)


@router.put(
    "/channels/amounts",
    dependencies=[
        Depends(check_csrf),
        Depends(rate_limit_for_user("channel_amounts")),
    ],
)
async def bulk_update_amounts(
    body: BulkAmountBody,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Bulk update vote amounts atomically."""
    for item in body.items:
        ch = await get_channel_or_404(item.id, user.id, db)
        if item.vote_amount is None:
            ch.vote_amount = None
        elif item.vote_amount < settings.min_vote_amount:
            raise ValidationError(
                f"vote_amount >= {settings.min_vote_amount} for channel {item.id}"
            )
        else:
            ch.vote_amount = item.vote_amount
    await record_audit(
        db,
        "channel_amounts_update",
        user_id=user.id,
        detail={"channel_ids": [item.id for item in body.items]},
    )
    await db.commit()
    await invalidate_user(user.id)
    return {"ok": True}


@router.delete(
    "/channels/{channel_id}",
    dependencies=[
        Depends(check_csrf),
        Depends(rate_limit_for_user("channel_delete")),
    ],
)
async def delete_channel(
    channel_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Soft-delete: hide the channel but keep its prediction history."""
    ch = await get_channel_or_404(channel_id, user.id, db)
    ch.enabled = False
    ch.paused_reason = None
    ch.deleted_at = utcnow()
    await record_audit(
        db,
        "channel_remove",
        user_id=user.id,
        detail={"channel_id": channel_id, "slug": ch.channel_slug},
    )
    await db.commit()
    await invalidate_user(user.id)
    return {"ok": True}
