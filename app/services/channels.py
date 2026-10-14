"""Channel service — add, update, delete channels with plan enforcement."""

from __future__ import annotations

import re

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AppException, ConflictError, PlanLimitError, ValidationError
from app.core.logging import get_logger
from app.db.models import Channel, User, UserSettings
from app.services.plans import channel_limit

log = get_logger(__name__)
settings = get_settings()

_SLUG_RE = re.compile(r"^[A-Za-z0-9_-]{1,50}$")


def validate_slug(slug: str) -> str:
    if not _SLUG_RE.match(slug):
        raise ValidationError("Invalid channel slug", {"slug": slug})
    return slug.lower()


async def add_channel(
    user: User,
    slug: str,
    vote_amount: int | None,
    db: AsyncSession,
    gateway=None,
    token: str | None = None,
) -> Channel:
    """Add a channel to user's account with plan limit enforcement.

    Uses SELECT ... FOR UPDATE on the user row to serialize concurrent adds.
    """
    slug = validate_slug(slug)
    if vote_amount is not None and vote_amount < settings.min_vote_amount:
        raise ValidationError(
            f"vote_amount must be >= {settings.min_vote_amount}",
            {"min_vote_amount": settings.min_vote_amount},
        )

    # Lock user row to prevent race conditions
    result = await db.execute(select(User).where(User.id == user.id).with_for_update())
    locked_user = result.scalar_one()

    # If this slug was previously removed, re-activate that row instead of
    # creating a duplicate (unique constraint on user_id + slug).
    existing_result = await db.execute(
        select(Channel)
        .where(
            Channel.user_id == user.id,
            Channel.channel_slug == slug,
        )
        .with_for_update()
    )
    existing = existing_result.scalar_one_or_none()
    if existing is not None and existing.deleted_at is None:
        raise ConflictError(
            "CHANNEL_EXISTS", f"Channel '{slug}' already added"
        )

    # Count active channels
    count_result = await db.execute(
        select(func.count(Channel.id))
        .where(Channel.user_id == user.id)
        .where(Channel.enabled == True)  # noqa: E712
        .where(Channel.deleted_at.is_(None))
    )
    current_count = count_result.scalar_one()

    if current_count >= channel_limit(locked_user):
        raise PlanLimitError(
            f"Channel limit reached ({channel_limit(locked_user)} max on your plan)"
        )

    # Optionally resolve kick_channel_id
    kick_channel_id = None
    if gateway is not None:
        if token is None:
            raise ValidationError("Kick account must be connected")
        try:
            info = await gateway.resolve_channel(slug, token)
        except AppException:
            raise
        except Exception as exc:
            log.warning(
                "resolve_channel_failed",
                slug=slug,
                error_type=type(exc).__name__,
            )
            raise ValidationError("Kick channel could not be resolved") from exc
        kick_channel_id = info.kick_channel_id

    if existing is not None:
        # Resurrect the previously removed channel.
        existing.deleted_at = None
        existing.enabled = True
        existing.paused_reason = None
        if kick_channel_id is not None:
            existing.kick_channel_id = kick_channel_id
        if vote_amount is not None:
            existing.vote_amount = vote_amount
        await db.flush()
        return existing

    ch = Channel(
        user_id=user.id,
        channel_slug=slug,
        kick_channel_id=kick_channel_id,
        vote_amount=vote_amount,
        enabled=True,
    )
    try:
        async with db.begin_nested():
            db.add(ch)
            await db.flush()
    except IntegrityError:
        raise ConflictError(
            "CHANNEL_EXISTS", f"Channel '{slug}' already added"
        ) from None

    return ch


async def get_user_channels(user_id: int, db: AsyncSession) -> list[Channel]:
    result = await db.execute(
        select(Channel)
        .where(Channel.user_id == user_id)
        .where(Channel.deleted_at.is_(None))
        .order_by(Channel.created_at.asc())
    )
    return list(result.scalars().all())


async def get_channel_or_404(
    channel_id: int, user_id: int, db: AsyncSession
) -> Channel:
    from app.core.errors import NotFoundError

    result = await db.execute(
        select(Channel).where(
            Channel.id == channel_id,
            Channel.user_id == user_id,
            Channel.deleted_at.is_(None),
        )
    )
    ch = result.scalar_one_or_none()
    if not ch:
        raise NotFoundError("Channel not found")
    return ch


async def effective_vote_amount(channel: Channel, user_settings: UserSettings) -> int:
    return (
        channel.vote_amount
        if channel.vote_amount is not None
        else user_settings.default_vote_amount
    )
