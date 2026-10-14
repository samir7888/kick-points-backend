"""arq background tasks: vote, settle, expiry, canary, webhook_retry."""

from __future__ import annotations

import asyncio
import math
import random
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.api.v1.ops import publish_sse
from app.core.config import get_settings
from app.core.errors import KickSchemaError
from app.core.logging import get_logger
from app.core.metrics import CIRCUIT_BREAKER_OPEN, VOTE_OUTCOMES
from app.core.security import decrypt_token, utcnow
from app.db.models import (
    Channel,
    KickToken,
    PausedReason,
    Prediction,
    PredictionState,
    TokenHealth,
    User,
    UserSettings,
    Vote,
    VoteStatus,
)
from app.db.session import get_redis, get_session
from app.integrations.kick.gateway import KickGateway
from app.services.plans import (
    channel_limit,
    ensure_user_settings,
    is_premium,
    reconcile_channels,
)
from app.services.predictions import _derive_winning_outcome_id
from app.services.runtime_flags import is_kill_switch_enabled
from app.services.votes import VoteStrategy

log = get_logger(__name__)
settings = get_settings()


async def _get_gateway() -> KickGateway:
    """Return the configured gateway (fake in dev/test, legacy adapter in prod)."""
    from app.integrations.kick.gateway import get_kick_gateway

    return get_kick_gateway()


def _vote_token(kt: KickToken) -> str:
    """Return the credential used to place votes on Kick.

    Kick rejects OAuth bearer tokens on its internal vote endpoint (401), so a
    kick.com site session token (Authorization: Bearer <user_id>|<session>) is
    used when the user has provided one; otherwise fall back to the OAuth token.
    """
    if kt.session_token_enc:
        return decrypt_token(kt.session_token_enc)
    return decrypt_token(kt.access_token_enc)


async def _record_kick_failure(redis, status_code: int | None) -> None:
    if status_code not in (403, 429):
        return
    failures = await redis.incr("kick:global_failures")
    await redis.expire("kick:global_failures", 60)
    if int(failures) >= 5:
        await redis.set("circuit_breaker:kick", "open", ex=300)
        CIRCUIT_BREAKER_OPEN.set(1)
        from app.api.v1.ops import publish_system_sse

        await publish_system_sse(
            "voting_paused",
            {"paused": True, "reason": "kick_api_failures"},
        )
        log.error("circuit_breaker_opened", status_code=status_code)


# ── Vote task ─────────────────────────────────────────────────────────────────


async def _incomplete_vote_task(
    ctx: dict,
    user_id: int,
    channel_id: int,
    prediction_id: int,  # DB id
) -> None:
    """Critical path: place a single vote for one user on one prediction."""
    log.info("vote_task_start", user_id=user_id, prediction_id=prediction_id)
    redis = await get_redis()

    # 1. Check global kill switch / circuit breaker
    kill_switch = await redis.get("admin:kill_switch")
    if kill_switch == "1":
        log.info("vote_skipped_kill_switch", user_id=user_id)
        return

    breaker = await redis.get("circuit_breaker:kick")
    if breaker == "open":
        log.info("vote_skipped_breaker", user_id=user_id)
        return

    async with get_session() as db:
        async with db.begin():
            # 2. Idempotency: INSERT ... ON CONFLICT DO NOTHING
            result = await db.execute(
                select(Prediction).where(Prediction.id == prediction_id)
            )
            pred = result.scalar_one_or_none()
            if not pred or pred.state != PredictionState.open:
                log.info("vote_skipped_not_open", prediction_id=prediction_id)
                return

            # Timing check
            if pred.closes_at:
                secs_left = (pred.closes_at - utcnow()).total_seconds()
                if secs_left < settings.min_seconds_before_close:
                    await _record_vote(
                        db,
                        user_id,
                        channel_id,
                        prediction_id,
                        outcome_id="",
                        amount_requested=0,
                        status=VoteStatus.skipped,
                        reason="TOO_LATE",
                    )
                    return

            # Jitter
            jitter = random.uniform(0, settings.vote_jitter_max_seconds)
            await asyncio.sleep(jitter)

            # Insert pending vote (idempotency guard)
            try:
                bind = db.get_bind()
                is_sqlite = (
                    getattr(getattr(bind, "dialect", None), "name", "") == "sqlite"
                )
                if is_sqlite:
                    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

                    ins = (
                        sqlite_insert(Vote)
                        .values(
                            user_id=user_id,
                            channel_id=channel_id,
                            prediction_id=prediction_id,
                            outcome_id="",
                            amount_requested=0,
                            status=VoteStatus.pending,
                            created_at=utcnow(),
                        )
                        .on_conflict_do_nothing()
                        .returning(Vote.id)
                    )
                else:
                    ins = (
                        pg_insert(Vote)
                        .values(
                            user_id=user_id,
                            channel_id=channel_id,
                            prediction_id=prediction_id,
                            outcome_id="",
                            amount_requested=0,
                            status=VoteStatus.pending,
                            created_at=utcnow(),
                        )
                        .on_conflict_do_nothing(constraint="uq_votes_user_prediction")
                        .returning(Vote.id)
                    )
                r = await db.execute(ins)
                vote_id = r.scalar_one_or_none()
                if vote_id is None:
                    log.info(
                        "vote_already_exists",
                        user_id=user_id,
                        prediction_id=prediction_id,
                    )
                    return
            except Exception as exc:
                log.error("vote_insert_error", error=str(exc))
                return

            # Fetch user settings and channel
            us_result = await db.execute(
                select(UserSettings).where(UserSettings.user_id == user_id)
            )
            user_settings = us_result.scalar_one_or_none()
            ch_result = await db.execute(
                select(Channel).where(Channel.id == channel_id)
            )
            ch = ch_result.scalar_one_or_none()

            if not user_settings or not ch:
                return

            if user_settings.paused or ch.deleted_at is not None:
                await db.execute(
                    update(Vote)
                    .where(Vote.id == vote_id)
                    .values(
                        status=VoteStatus.skipped,
                        reason=(
                            "USER_PAUSED"
                            if user_settings.paused
                            else "CHANNEL_PAUSED"
                        ),
                    )
                )
                return

            # Get decrypted token
            kt_result = await db.execute(
                select(KickToken).where(KickToken.user_id == user_id)
            )
            kt = kt_result.scalar_one_or_none()
            if not kt or kt.health == TokenHealth.needs_reauth:
                await db.execute(
                    update(Vote)
                    .where(Vote.id == vote_id)
                    .values(status=VoteStatus.skipped, reason="TOKEN_INVALID")
                )
                return

            token = _vote_token(kt)
            effective_amount = (
                ch.vote_amount
                if ch.vote_amount is not None
                else user_settings.default_vote_amount
            )

            # Choose outcome via VoteStrategy
            from app.integrations.kick.models import OutcomeInfo, PredictionEvent

            outcomes_objs = [
                OutcomeInfo(
                    id=str(o.get("id", "")),
                    title=o.get("title", ""),
                    return_rate=float(o.get("return_rate") or 1.0),
                )
                for o in (pred.outcomes or [])
            ]
            fake_event = PredictionEvent(
                kick_prediction_id=pred.kick_prediction_id,
                channel_slug=pred.channel_slug,
                title=pred.title,
                outcomes=outcomes_objs,
                state=pred.state.value,
                opened_at=pred.opened_at,
            )
            outcome_id = VoteStrategy.choose_outcome(fake_event, user_settings)
            if outcome_id is None:
                await db.execute(
                    update(Vote)
                    .where(Vote.id == vote_id)
                    .values(status=VoteStatus.skipped, reason="STRATEGY_SKIP")
                )
                return

            # Update pending row with chosen outcome
            await db.execute(
                update(Vote)
                .where(Vote.id == vote_id)
                .values(
                    outcome_id=outcome_id,
                    amount_requested=effective_amount,
                )
            )

        # Place vote outside transaction (retries on conn errors only)
        gateway = await _get_gateway()
        vote_result = await gateway.place_vote(
            pred.channel_slug,
            pred.kick_prediction_id,
            outcome_id,
            effective_amount,
            token,
        )

        async with db.begin():
            if vote_result.success:
                await db.execute(
                    update(Vote)
                    .where(Vote.id == vote_id)
                    .values(
                        status=VoteStatus.placed,
                        amount_placed=vote_result.amount_placed,
                    )
                )
                log.info(
                    "vote_placed",
                    user_id=user_id,
                    prediction_id=prediction_id,
                    amount=vote_result.amount_placed,
                )

                # Publish SSE event
                await publish_sse(
                    user_id,
                    "vote_placed",
                    {
                        "prediction_id": prediction_id,
                        "outcome_id": outcome_id,
                        "amount": vote_result.amount_placed,
                    },
                )
            else:
                # Check if it was auth-related
                if vote_result.error and "401" in str(vote_result.error):
                    # Keep the OAuth read token valid (listening must continue);
                    # the user just needs a working session token for votes.
                    await publish_sse(user_id, "token_needs_reauth", {})

                await db.execute(
                    update(Vote)
                    .where(Vote.id == vote_id)
                    .values(
                        status=VoteStatus.failed,
                        error=vote_result.error,
                    )
                )
                log.warning("vote_failed", user_id=user_id, error=vote_result.error)


async def vote_task(
    ctx: dict,
    user_id: int,
    channel_id: int,
    prediction_id: int,
) -> None:
    """Place at most one vote for a user/prediction pair."""
    redis = await get_redis()
    vote_id: int | None = None
    skip_reason: str | None = None
    token: str | None = None
    outcome_id: str | None = None
    slug: str | None = None
    platform_prediction_id: str | None = None
    closes_at = None
    amount = 0
    fallback_to_min = False

    async with get_session() as db:
        async with db.begin():
            pred_result = await db.execute(
                select(Prediction).where(Prediction.id == prediction_id)
            )
            prediction = pred_result.scalar_one_or_none()
            if prediction is None:
                log.warning("vote_prediction_missing", prediction_id=prediction_id)
                return

            insert_statement = (
                sqlite_insert(Vote)
                if db.get_bind().dialect.name == "sqlite"
                else pg_insert(Vote)
            )
            vote_id = await db.scalar(
                insert_statement.values(
                    user_id=user_id,
                    channel_id=channel_id,
                    prediction_id=prediction_id,
                    outcome_id="",
                    amount_requested=0,
                    status=VoteStatus.PENDING,
                    created_at=utcnow(),
                )
                .on_conflict_do_nothing(
                    index_elements=[Vote.user_id, Vote.prediction_id]
                )
                .returning(Vote.id)
            )
            if vote_id is None:
                log.info(
                    "vote_already_exists",
                    user_id=user_id,
                    prediction_id=prediction_id,
                )
                return

            if prediction.state != PredictionState.OPEN:
                skip_reason = "PREDICTION_NOT_OPEN"
            elif (
                prediction.closes_at
                and (prediction.closes_at - utcnow()).total_seconds()
                < settings.min_seconds_before_close
            ):
                skip_reason = "TOO_LATE"

            channel_result = await db.execute(
                select(Channel).where(
                    Channel.id == channel_id,
                    Channel.user_id == user_id,
                )
            )
            channel = channel_result.scalar_one_or_none()
            user = await db.get(User, user_id)
            user_settings = await ensure_user_settings(user_id, db)
            token_result = await db.execute(
                select(KickToken).where(KickToken.user_id == user_id)
            )
            kick_token = token_result.scalar_one_or_none()

            if skip_reason is None:
                if channel is None or user is None:
                    skip_reason = "CHANNEL_UNAVAILABLE"
                elif not channel.enabled or channel.paused_reason is not None:
                    skip_reason = "CHANNEL_PAUSED"
                elif channel.deleted_at is not None:
                    skip_reason = "CHANNEL_PAUSED"
                elif user.deleted_at is not None or user_settings.paused:
                    skip_reason = "USER_PAUSED"
                elif (
                    await is_kill_switch_enabled(db)
                    or await redis.get("admin:kill_switch") == "1"
                    or await redis.get("circuit_breaker:kick") == "open"
                ):
                    skip_reason = "VOTING_PAUSED"
                elif kick_token is None or kick_token.health != TokenHealth.OK:
                    skip_reason = "TOKEN_INVALID"
                else:
                    eligible_result = await db.execute(
                        select(Channel.id)
                        .where(
                            Channel.user_id == user_id,
                            Channel.enabled.is_(True),
                        )
                        .order_by(Channel.created_at.asc(), Channel.id.asc())
                    )
                    eligible_ids = eligible_result.scalars().all()[
                        : channel_limit(user)
                    ]
                    if channel.id not in eligible_ids:
                        skip_reason = "PLAN_LIMIT"
                    else:
                        token = _vote_token(kick_token)
                        session_token_present = bool(kick_token.session_token_enc)
                        amount = (
                            channel.vote_amount
                            if channel.vote_amount is not None
                            else user_settings.default_vote_amount
                        )
                        fallback_to_min = user_settings.fallback_to_min
                        from app.integrations.kick.models import (
                            OutcomeInfo,
                            PredictionEvent,
                        )

                        event = PredictionEvent(
                            kick_prediction_id=prediction.kick_prediction_id,
                            channel_slug=prediction.channel_slug,
                            title=prediction.title,
                            outcomes=[
                                OutcomeInfo(
                                    id=str(item.get("id", "")),
                                    title=str(item.get("title", "")),
                                    return_rate=float(item.get("return_rate") or 1.0),
                                )
                                for item in (prediction.outcomes or [])
                            ],
                            state=prediction.state.value,
                            opened_at=prediction.opened_at,
                            closes_at=prediction.closes_at,
                        )
                        outcome_id = VoteStrategy.choose_outcome(event, user_settings)
                        if outcome_id is None:
                            skip_reason = "STRATEGY_SKIP"
                        else:
                            slug = channel.channel_slug
                            platform_prediction_id = prediction.kick_prediction_id
                            closes_at = prediction.closes_at

            if skip_reason:
                await db.execute(
                    update(Vote)
                    .where(Vote.id == vote_id)
                    .values(status=VoteStatus.SKIPPED, reason=skip_reason)
                )
            elif outcome_id is not None:
                await db.execute(
                    update(Vote)
                    .where(Vote.id == vote_id)
                    .values(outcome_id=outcome_id, amount_requested=amount)
                )

    if vote_id is None:
        return
    if skip_reason:
        await publish_sse(
            user_id,
            "vote_failed",
            {"prediction_id": prediction_id, "reason": skip_reason},
        )
        return
    if (
        token is None
        or slug is None
        or platform_prediction_id is None
        or outcome_id is None
    ):
        raise RuntimeError("Vote preparation did not produce required placement data")

    await asyncio.sleep(random.uniform(0, settings.vote_jitter_max_seconds))
    async with get_session() as db:
        kill_switch_enabled = await is_kill_switch_enabled(db)
    if (
        kill_switch_enabled
        or await redis.get("admin:kill_switch") == "1"
        or await redis.get("circuit_breaker:kick") == "open"
    ):
        await _finish_vote(vote_id, VoteStatus.SKIPPED, reason="VOTING_PAUSED")
        return
    if (
        closes_at
        and (closes_at - utcnow()).total_seconds() < settings.min_seconds_before_close
    ):
        await _finish_vote(vote_id, VoteStatus.SKIPPED, reason="TOO_LATE")
        return

    gateway: KickGateway = ctx.get("gateway") or await _get_gateway()
    try:
        balance = await gateway.get_points_balance(slug, token)
        if balance is not None and balance < amount:
            if fallback_to_min and balance >= settings.min_vote_amount:
                amount = settings.min_vote_amount
            else:
                await _finish_vote(
                    vote_id,
                    VoteStatus.SKIPPED,
                    reason="INSUFFICIENT_POINTS",
                )
                return
        vote_result = await gateway.place_vote(
            slug,
            platform_prediction_id,
            outcome_id,
            amount,
            token,
        )
    except Exception as exc:
        if isinstance(exc, KickSchemaError):
            await redis.set("circuit_breaker:kick", "open", ex=300)
            CIRCUIT_BREAKER_OPEN.set(1)
            from app.api.v1.ops import publish_system_sse

            await publish_system_sse(
                "voting_paused",
                {"paused": True, "reason": "kick_schema_error"},
            )
        await _record_kick_failure(redis, getattr(exc, "status_code", None))
        log.error(
            "vote_gateway_error",
            user_id=user_id,
            prediction_id=prediction_id,
            error_type=type(exc).__name__,
        )
        await _finish_vote(
            vote_id,
            VoteStatus.FAILED,
            reason="UNKNOWN_OUTCOME",
            error="UNKNOWN_OUTCOME",
        )
        await publish_sse(
            user_id,
            "vote_failed",
            {"prediction_id": prediction_id, "reason": "UNKNOWN_OUTCOME"},
        )
        return

    if vote_result.success:
        placed_amount = vote_result.amount_placed or amount
        await _finish_vote(
            vote_id,
            VoteStatus.PLACED,
            amount_placed=placed_amount,
        )
        await publish_sse(
            user_id,
            "vote_placed",
            {
                "prediction_id": prediction_id,
                "outcome_id": outcome_id,
                "amount": placed_amount,
            },
        )
        log.info(
            "vote_placed",
            user_id=user_id,
            prediction_id=prediction_id,
            amount=placed_amount,
        )
        return

    error = (vote_result.error or "").lower()
    status_code = next(
        (code for code in (401, 403, 429) if str(code) in error),
        None,
    )
    await _record_kick_failure(redis, status_code)
    auth_failure = status_code in (401, 403)
    if auth_failure and not session_token_present:
        # Kick rejects the OAuth token on its vote endpoint (401); the fix is a
        # kick.com session token in Settings, not a re-auth of the OAuth flow.
        reason = "SESSION_TOKEN_REQUIRED"
    elif auth_failure:
        # A configured session token was rejected — ask the user to refresh it,
        # but never invalidate the OAuth read token (listening keeps working).
        reason = "TOKEN_INVALID"
        await publish_sse(user_id, "token_needs_reauth", {})
    else:
        reason = "UNKNOWN_OUTCOME"
    await _finish_vote(
        vote_id,
        VoteStatus.FAILED,
        reason=reason,
        error=reason,
    )
    await publish_sse(
        user_id,
        "vote_failed",
        {"prediction_id": prediction_id, "reason": reason},
    )
    log.warning(
        "vote_failed", user_id=user_id, prediction_id=prediction_id, reason=reason
    )


async def _finish_vote(
    vote_id: int,
    status: VoteStatus,
    *,
    reason: str | None = None,
    error: str | None = None,
    amount_placed: int | None = None,
    mark_token_invalid: bool = False,
) -> None:
    async with get_session() as db:
        async with db.begin():
            await db.execute(
                update(Vote)
                .where(Vote.id == vote_id)
                .values(
                    status=status,
                    reason=reason,
                    error=error,
                    amount_placed=amount_placed,
                )
            )
            if mark_token_invalid:
                user_result = await db.execute(
                    select(Vote.user_id).where(Vote.id == vote_id)
                )
                user_id = user_result.scalar_one()
                token_result = await db.execute(
                    select(KickToken).where(KickToken.user_id == user_id)
                )
                kick_token = token_result.scalar_one_or_none()
                if kick_token:
                    kick_token.health = TokenHealth.NEEDS_REAUTH
                await db.execute(
                    update(Channel)
                    .where(Channel.user_id == user_id)
                    .values(paused_reason=PausedReason.TOKEN)
                )
    VOTE_OUTCOMES.labels(status.value).inc()


async def webhook_retry_task(ctx: dict, event_id: str) -> None:
    """Retry a stored Dodo webhook only when it has not been processed."""
    from app.db.models import WebhookEvent
    from app.services.billing import apply_webhook_event, find_user_for_webhook

    try:
        async with get_session() as db:
            async with db.begin():
                event = await db.get(WebhookEvent, event_id, with_for_update=True)
                if event is None or event.processed_at is not None:
                    return
                data = event.payload.get("data")
                if not isinstance(data, dict):
                    raise ValueError("Stored webhook data is invalid")
                user = await find_user_for_webhook(data, db)
                if user is None:
                    raise ValueError("Webhook customer could not be associated")
                await apply_webhook_event(event.type, data, user, db)
                event.processed_at = utcnow()
                event.error = None
    except Exception as exc:
        async with get_session() as db:
            async with db.begin():
                event = await db.get(WebhookEvent, event_id)
                if event is not None and event.processed_at is None:
                    event.error = type(exc).__name__
        raise


async def _record_vote(
    db, user_id, channel_id, prediction_id, outcome_id, amount_requested, status, reason
):
    v = Vote(
        user_id=user_id,
        channel_id=channel_id,
        prediction_id=prediction_id,
        outcome_id=outcome_id,
        amount_requested=amount_requested,
        status=status,
        reason=reason,
        created_at=utcnow(),
    )
    db.add(v)
    await db.flush()


# ── Settle task ───────────────────────────────────────────────────────────────


async def settle_task(ctx: dict, prediction_id: int) -> None:
    """Settle votes (won/lost/refunded) when a prediction resolves."""
    events: list[tuple[int, str, int | None]] = []
    async with get_session() as db:
        async with db.begin():
            result = await db.execute(
                select(Prediction).where(Prediction.id == prediction_id)
            )
            pred = result.scalar_one_or_none()
            if not pred:
                return
            if pred.state not in (PredictionState.RESOLVED, PredictionState.CANCELLED):
                return
            # Kick sometimes omits the top-level winner, so fall back to the
            # outcome flagged with is_winner before giving up on settlement.
            winning_outcome_id = _derive_winning_outcome_id(
                pred.winning_outcome_id, pred.outcomes
            )
            if pred.state == PredictionState.RESOLVED and not winning_outcome_id:
                log.warning("settlement_missing_winner", prediction_id=prediction_id)
                return

            # Payout multipliers from the prediction's own outcome payload.
            return_rates: dict[str, float] = {}
            for outcome in pred.outcomes or []:
                rate = outcome.get("return_rate")
                if rate is not None:
                    try:
                        return_rates[str(outcome.get("id"))] = float(rate)
                    except (TypeError, ValueError):
                        continue

            votes_result = await db.execute(
                select(Vote).where(
                    Vote.prediction_id == prediction_id,
                    Vote.status == VoteStatus.placed,
                )
            )
            votes = votes_result.scalars().all()

            for vote in votes:
                if pred.state == PredictionState.CANCELLED:
                    new_status = VoteStatus.REFUNDED
                    points_delta = 0
                elif winning_outcome_id and vote.outcome_id == winning_outcome_id:
                    new_status = VoteStatus.WON
                    points_delta = _won_points_delta(
                        vote.amount_placed, return_rates.get(vote.outcome_id)
                    )
                else:
                    new_status = VoteStatus.LOST
                    points_delta = (
                        -(vote.amount_placed or 0)
                        if vote.amount_placed is not None
                        else None
                    )

                vote.status = new_status
                vote.points_delta = points_delta
                vote.settled_at = utcnow()
                events.append((vote.user_id, new_status.value, points_delta))

    for user_id, status, points_delta in events:
        VOTE_OUTCOMES.labels(status).inc()
        await publish_sse(
            user_id,
            "vote_settled",
            {
                "prediction_id": prediction_id,
                "status": status,
                "points_delta": points_delta,
            },
        )
    log.info("settle_done", prediction_id=prediction_id, votes_settled=len(events))


def _won_points_delta(amount: int | None, return_rate: float | None) -> int | None:
    """Point change for a won vote: floor(stake * return_rate) - stake.

    Kick pays integer points, so the stake-plus-payout is floored before the
    original stake is subtracted. Returns None when the payout can't be derived.
    """
    if amount is None or return_rate is None:
        return None
    return math.floor(amount * return_rate) - amount


# ── Expiry task ───────────────────────────────────────────────────────────────


async def expiry_task(ctx: dict) -> None:
    """Daily job: reconcile channels for users whose premium has lapsed."""
    async with get_session() as db:
        async with db.begin():
            result = await db.execute(
                select(User).where(
                    (User.premium_until.isnot(None))
                    | (User.premium_grace_until.isnot(None)),
                    User.deleted_at.is_(None),
                )
            )
            users = result.scalars().all()
            for user in users:
                if not is_premium(user):
                    user.premium_until = None
                    user.premium_grace_until = None
                await reconcile_channels(user, db)
    log.info("expiry_task_done")


async def pending_vote_recovery_task(ctx: dict) -> None:
    """Mark unresolved placements after two minutes without risking a duplicate."""
    cutoff = utcnow() - timedelta(minutes=2)
    recovered: list[tuple[int, int]] = []
    async with get_session() as db:
        async with db.begin():
            result = await db.execute(
                select(Vote).where(
                    Vote.status == VoteStatus.PENDING,
                    Vote.created_at < cutoff,
                )
            )
            for vote in result.scalars().all():
                vote.status = VoteStatus.FAILED
                vote.reason = "UNKNOWN_OUTCOME"
                vote.error = "UNKNOWN_OUTCOME"
                vote.settled_at = utcnow()
                recovered.append((vote.user_id, vote.prediction_id))
    for user_id, prediction_id in recovered:
        VOTE_OUTCOMES.labels(VoteStatus.FAILED.value).inc()
        await publish_sse(
            user_id,
            "vote_failed",
            {"prediction_id": prediction_id, "reason": "UNKNOWN_OUTCOME"},
        )
    if recovered:
        log.warning("pending_votes_recovered", count=len(recovered))


# ── Canary task ───────────────────────────────────────────────────────────────


async def canary_task(ctx: dict) -> None:
    """Every 5 min: harmless read call to check Kick API health."""
    if not settings.canary_channel_slug or not settings.canary_access_token:
        return

    redis = await get_redis()
    gateway = ctx.get("gateway") or await _get_gateway()
    try:
        await gateway.get_active_prediction(
            settings.canary_channel_slug, settings.canary_access_token
        )
        await redis.delete("circuit_breaker:kick")
        await redis.delete("canary:failures")
        CIRCUIT_BREAKER_OPEN.set(0)
        async with get_session() as db:
            kill_switch_enabled = await is_kill_switch_enabled(db)
        kill_switch_enabled = (
            kill_switch_enabled or await redis.get("admin:kill_switch") == "1"
        )
        from app.api.v1.ops import publish_system_sse

        await publish_system_sse(
            "voting_paused",
            {
                "paused": kill_switch_enabled,
                "reason": "kill_switch" if kill_switch_enabled else None,
            },
        )
        log.info("canary_ok")
    except Exception as exc:
        log.warning("canary_failed", error_type=type(exc).__name__)
        # Increment failure counter; open breaker after 5 consecutive fails
        failures = await redis.incr("canary:failures")
        await redis.expire("canary:failures", 600)
        if int(failures) >= 5:
            await redis.set("circuit_breaker:kick", "open", ex=300)
            CIRCUIT_BREAKER_OPEN.set(1)
            from app.api.v1.ops import publish_system_sse

            await publish_system_sse(
                "voting_paused",
                {"paused": True, "reason": "canary_failures"},
            )
            log.error("circuit_breaker_opened")
