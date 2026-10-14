"""Plan limits and permissions service."""

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import get_settings
from ..core.errors import PlanLimitException
from ..db.models import Channel, PausedReason, User, UserSettings


def is_premium(user: User) -> bool:
    """Check active entitlement or an explicitly granted renewal grace period."""
    now = datetime.now(UTC)
    return bool(
        (user.premium_until and user.premium_until > now)
        or (user.premium_grace_until and user.premium_grace_until > now)
    )


def channel_limit(user: User) -> int:
    """Return the allowed channel count for the user's current plan."""
    return (
        get_settings().premium_channel_limit
        if is_premium(user)
        else get_settings().free_channel_limit
    )


async def ensure_user_settings(user_id: int, db: AsyncSession) -> UserSettings:
    """Ensure the user has a settings row, creating it if needed."""
    result = await db.execute(
        select(UserSettings).where(UserSettings.user_id == user_id)
    )
    settings = result.scalar_one_or_none()
    if settings is not None:
        return settings

    app_settings = get_settings()
    new_settings = UserSettings(
        user_id=user_id,
        default_vote_amount=app_settings.min_vote_amount,
        fallback_to_min=True,
        paused=False,
    )
    db.add(new_settings)
    await db.flush()
    return new_settings


class PlanService:
    """Service for managing user plan limits and permissions."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.settings = get_settings()

    def is_premium(self, user: User) -> bool:
        return is_premium(user)

    def get_channel_limit(self, user: User) -> int:
        return channel_limit(user)

    async def check_can_add_channel(self, user: User) -> None:
        """Check if the user can add another channel."""
        stmt = (
            select(func.count(Channel.id))
            .where(Channel.user_id == user.id)
            .where(Channel.deleted_at.is_(None))
        )
        result = await self.session.execute(stmt)
        current_count = result.scalar() or 0

        limit = self.get_channel_limit(user)
        if current_count >= limit:
            raise PlanLimitException(
                f"You can only have {limit} channel{'s' if limit != 1 else ''} "
                f"on your current plan. {'Upgrade to Premium' if not self.is_premium(user) else 'You have reached the maximum'} "
                f"to monitor more channels."
            )

    async def reconcile_channels(self, user: User) -> int:
        """Pause extra channels when the user exceeds their plan limit."""
        limit = self.get_channel_limit(user)
        stmt = (
            select(Channel)
            .where(Channel.user_id == user.id)
            .where(Channel.deleted_at.is_(None))
            .order_by(Channel.created_at.asc())
        )
        result = await self.session.execute(stmt)
        channels = result.scalars().all()

        paused_count = 0
        for i, channel in enumerate(channels):
            if i < limit:
                if channel.paused_reason == PausedReason.PLAN_LIMIT:
                    channel.paused_reason = None
            else:
                if channel.paused_reason != PausedReason.PLAN_LIMIT:
                    channel.paused_reason = PausedReason.PLAN_LIMIT
                    paused_count += 1

        await self.session.flush()
        return paused_count

    async def get_channels_used(self, user: User) -> int:
        """Count the active channels for a user."""
        stmt = (
            select(func.count(Channel.id))
            .where(Channel.user_id == user.id)
            .where(Channel.deleted_at.is_(None))
        )
        result = await self.session.execute(stmt)
        return result.scalar() or 0

    def get_plan_info(self, user: User) -> dict:
        """Return plan metadata for a user."""
        premium = self.is_premium(user)
        return {
            "plan": "premium" if premium else "free",
            "is_premium": premium,
            "premium_until": user.premium_until,
            "channel_limit": self.get_channel_limit(user),
            "price_display": self.settings.premium_price_display
            if not premium
            else None,
        }


async def reconcile_channels(user: User, db: AsyncSession) -> int:
    """Compatibility helper to reconcile plan limits for a user."""
    service = PlanService(db)
    return await service.reconcile_channels(user)
