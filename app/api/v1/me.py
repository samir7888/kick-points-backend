"""Me router: user profile, disconnect kick, delete account."""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import check_csrf, get_current_user, rate_limit_for_user
from app.core.cache import cache_get, cache_set, invalidate_user, user_key
from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.security import decrypt_token, utcnow
from app.db.models import (
    Channel,
    KickToken,
    PausedReason,
    Session,
    User,
)
from app.db.session import get_db_session
from app.integrations.kick.oauth import revoke_token
from app.services.audit import record_audit
from app.services.plans import channel_limit, is_premium
from app.services.runtime_flags import is_kill_switch_enabled

router = APIRouter(tags=["me"])
log = get_logger(__name__)
settings = get_settings()

# `/me` is read on every page (shell badge, banners, plan) and by the Next.js
# server layout. A short TTL absorbs the burst without hiding state for long;
# writes and live events invalidate it immediately.
_ME_TTL = 10


async def _revoke_kick_tokens(token: KickToken, user_id: int) -> None:
    for encrypted_token, token_type in (
        (token.access_token_enc, "access_token"),
        (token.refresh_token_enc, "refresh_token"),
    ):
        if not encrypted_token:
            continue
        try:
            revoked = await revoke_token(
                decrypt_token(encrypted_token),
                token_type=token_type,
            )
        except Exception as exc:
            log.warning(
                "kick_token_revoke_error",
                user_id=user_id,
                token_type=token_type,
                error_type=type(exc).__name__,
            )
        else:
            if not revoked:
                log.warning(
                    "kick_token_revoke_failed",
                    user_id=user_id,
                    token_type=token_type,
                )


@router.get("/me")
async def get_me(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    key = user_key(user.id, "me")
    cached = await cache_get(key)
    if cached is not None:
        return cast(dict[str, Any], cached)

    result = await db.execute(select(KickToken).where(KickToken.user_id == user.id))
    kt = result.scalar_one_or_none()

    # Check global kill switch / breaker
    from app.db.session import get_redis

    redis = await get_redis()
    voting_paused = (
        await is_kill_switch_enabled(db)
        or await redis.get("admin:kill_switch") == "1"
        or await redis.get("circuit_breaker:kick") == "open"
    )

    ch_result = await db.execute(
        select(Channel).where(
            Channel.user_id == user.id,
            Channel.enabled == True,  # noqa: E712
            Channel.deleted_at.is_(None),
        )
    )
    channels_used = len(ch_result.scalars().all())

    payload = {
        "id": user.id,
        "kick_user_id": user.kick_user_id,
        "username": user.username,
        "plan": "premium" if is_premium(user) else "free",
        "premium_until": user.premium_until.isoformat() if user.premium_until else None,
        "channel_limit": channel_limit(user),
        "channels_used": channels_used,
        "token_health": kt.health.value if kt else "disconnected",
        "voting_paused": voting_paused,
        "is_admin": user.is_admin,
    }
    await cache_set(key, payload, _ME_TTL)
    return payload


@router.delete(
    "/me/kick-connection",
    dependencies=[
        Depends(check_csrf),
        Depends(rate_limit_for_user("kick_disconnect", limit=5, window=60)),
    ],
)
async def disconnect_kick(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Revoke Kick tokens and pause all channels."""
    result = await db.execute(select(KickToken).where(KickToken.user_id == user.id))
    kt = result.scalar_one_or_none()
    if kt:
        await _revoke_kick_tokens(kt, user.id)
        await db.delete(kt)

    ch_result = await db.execute(select(Channel).where(Channel.user_id == user.id))
    for ch in ch_result.scalars().all():
        ch.paused_reason = PausedReason.TOKEN
    await record_audit(
        db,
        "kick_disconnect",
        user_id=user.id,
        ip=request.client.host if request.client else None,
    )
    await db.commit()
    await invalidate_user(user.id)

    log.info("kick_disconnected", user_id=user.id)
    return {"ok": True}


@router.delete(
    "/me",
    dependencies=[
        Depends(check_csrf),
        Depends(rate_limit_for_user("account_delete", limit=2, window=3600)),
    ],
)
async def delete_me(
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Soft-delete user and revoke tokens."""
    token_result = await db.execute(
        select(KickToken).where(KickToken.user_id == user.id)
    )
    kt = token_result.scalar_one_or_none()
    if kt:
        await _revoke_kick_tokens(kt, user.id)
        await db.delete(kt)

    channel_result = await db.execute(select(Channel).where(Channel.user_id == user.id))
    for channel in channel_result.scalars().all():
        channel.enabled = False
        channel.paused_reason = PausedReason.TOKEN

    await db.execute(delete(Session).where(Session.user_id == user.id))
    user.deleted_at = utcnow()
    user.username = "[deleted]"
    user.email = None
    await record_audit(
        db,
        "account_delete",
        user_id=user.id,
        ip=request.client.host if request.client else None,
    )
    await db.commit()
    await invalidate_user(user.id)
    response.delete_cookie(settings.session_cookie_name)

    log.info("user_deleted", user_id=user.id)
    return {"ok": True}
