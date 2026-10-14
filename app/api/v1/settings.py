"""Settings router."""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import check_csrf, get_current_user, rate_limit_for_user
from app.core.cache import cache_get, cache_set, invalidate_user, user_key
from app.core.config import get_settings
from app.core.security import encrypt_token
from app.db.models import KickToken, User, UserSettings
from app.db.session import get_db_session
from app.integrations.kick.session_token import (
    INVALID,
    VALID,
    validate_kick_session_token,
)
from app.services.plans import ensure_user_settings

router = APIRouter(tags=["settings"])
settings = get_settings()
_SETTINGS_TTL = 30


class SettingsBody(BaseModel):
    default_vote_amount: int | None = Field(default=None, ge=settings.min_vote_amount)
    fallback_to_min: bool | None = None
    paused: bool | None = None
    kick_session_token: str | None = Field(default=None, max_length=2000)


def _normalize_session_token(value: str) -> str:
    """Strip whitespace and an optional 'Bearer ' prefix from a pasted token."""
    token = value.strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    return token


async def _settings_payload(
    user_id: int, db: AsyncSession, us: UserSettings | None = None
) -> dict:
    if us is None:
        us = await ensure_user_settings(user_id, db)
    token_result = await db.execute(
        select(KickToken).where(KickToken.user_id == user_id)
    )
    kt = token_result.scalar_one_or_none()
    return {
        "default_vote_amount": us.default_vote_amount,
        "fallback_to_min": us.fallback_to_min,
        "paused": us.paused,
        "kick_session_token_set": bool(kt and kt.session_token_enc),
    }


@router.get("/settings")
async def get_settings_endpoint(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    key = user_key(user.id, "settings")
    cached = await cache_get(key)
    if cached is not None:
        return cast(dict[str, Any], cached)

    payload = await _settings_payload(user.id, db)
    await db.commit()
    await cache_set(key, payload, _SETTINGS_TTL)
    return payload


@router.put(
    "/settings",
    dependencies=[
        Depends(check_csrf),
        Depends(rate_limit_for_user("settings_update")),
    ],
)
async def update_settings(
    body: SettingsBody,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    us = await ensure_user_settings(user.id, db)
    validation: dict[str, str | None] | None = None
    if body.default_vote_amount is not None:
        us.default_vote_amount = body.default_vote_amount
    if body.fallback_to_min is not None:
        us.fallback_to_min = body.fallback_to_min
    if body.paused is not None:
        us.paused = body.paused
    if "kick_session_token" in body.model_fields_set:
        raw = _normalize_session_token(body.kick_session_token or "")
        token_result = await db.execute(
            select(KickToken).where(KickToken.user_id == user.id)
        )
        kt = token_result.scalar_one_or_none()
        if not raw:
            if kt is not None:
                kt.session_token_enc = None
        else:
            validation = await validate_kick_session_token(raw)
            # Keep a Kick-rejected token out of the DB so it never replaces a
            # working one; unverifiable tokens (Kick unreachable) are stored.
            if kt is not None and validation["status"] != INVALID:
                kt.session_token_enc = encrypt_token(raw)
    await db.commit()
    await invalidate_user(user.id)
    payload = await _settings_payload(user.id, db, us=us)
    if validation is not None:
        if validation["status"] == VALID:
            payload["kick_session_token_valid"] = True
            payload["kick_session_token_message"] = None
        elif validation["status"] == INVALID:
            payload["kick_session_token_valid"] = False
            payload["kick_session_token_message"] = validation["message"]
        else:
            # Kick could not be reached — saved, but not confirmed.
            payload["kick_session_token_valid"] = None
            payload["kick_session_token_message"] = None
    return payload
