"""Billing service — Dodo webhook processing and subscription state management."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.security import utcnow
from app.db.models import Subscription, User
from app.integrations.dodo.client import (
    DODO_EVENT_PAYMENT_FAILED,
    DODO_EVENT_PAYMENT_SUCCEEDED,
    DODO_EVENT_SUBSCRIPTION_ACTIVE,
    DODO_EVENT_SUBSCRIPTION_CANCELLED,
    DODO_EVENT_SUBSCRIPTION_EXPIRED,
    DODO_EVENT_SUBSCRIPTION_FAILED,
    DODO_EVENT_SUBSCRIPTION_ON_HOLD,
    DODO_EVENT_SUBSCRIPTION_PAST_DUE,
    DODO_EVENT_SUBSCRIPTION_PAUSED,
    DODO_EVENT_SUBSCRIPTION_PLAN_CHANGED,
    DODO_EVENT_SUBSCRIPTION_RENEWED,
    DODO_EVENT_SUBSCRIPTION_UNPAUSED,
    DODO_EVENT_SUBSCRIPTION_UPDATED,
)
from app.services.audit import record_audit

log = get_logger(__name__)


async def find_user_for_webhook(
    payload: dict[str, Any], db: AsyncSession
) -> User | None:
    """Locate user from webhook metadata or customer_id."""
    metadata = payload.get("metadata")
    user_id = metadata.get("user_id") if isinstance(metadata, dict) else None
    if user_id is not None and str(user_id).isdigit():
        result = await db.execute(select(User).where(User.id == int(user_id)))
        user = result.scalar_one_or_none()
        if user:
            return user

    # Fall back to dodo_customer_id
    customer = payload.get("customer") or {}
    customer_id = (
        payload.get("customer_id") or customer.get("customer_id") or customer.get("id")
    )
    if customer_id:
        sub_result = await db.execute(
            select(Subscription).where(
                Subscription.dodo_customer_id == str(customer_id)
            )
        )
        sub: Subscription | None = sub_result.scalar_one_or_none()
        if sub:
            result2 = await db.execute(select(User).where(User.id == sub.user_id))
            return result2.scalar_one_or_none()

    return None


async def apply_webhook_event(
    event_type: str,
    payload: dict[str, Any],
    user: User,
    db: AsyncSession,
) -> None:
    """Apply a Dodo webhook event to the user's subscription state.

    All event types → one function (easier to audit).
    """
    sub_id = str(payload.get("subscription_id") or "")
    customer = payload.get("customer") or {}
    customer_id = str(
        payload.get("customer_id")
        or customer.get("customer_id")
        or customer.get("id")
        or ""
    )
    period_end_raw = (
        payload.get("next_billing_date")
        or payload.get("current_period_end")
        or payload.get("expires_at")
    )
    period_end: datetime | None = None
    if period_end_raw:
        try:
            if isinstance(period_end_raw, (int, float)):
                period_end = datetime.fromtimestamp(period_end_raw, tz=UTC)
            else:
                period_end = datetime.fromisoformat(
                    str(period_end_raw).replace("Z", "+00:00")
                )
        except (ValueError, TypeError, OverflowError):
            period_end = None

    result = await db.execute(
        select(Subscription).where(
            Subscription.user_id == user.id,
            Subscription.dodo_subscription_id == sub_id,
        )
    )
    sub = result.scalar_one_or_none()
    # Status before this event, used to decide whether a failure is a renewal
    # of a paying subscription (grace applies) or a declined first payment
    # (no grace — the user never had Premium).
    prior_status = sub.status if sub else None

    if event_type in (
        DODO_EVENT_SUBSCRIPTION_ACTIVE,
        DODO_EVENT_SUBSCRIPTION_RENEWED,
    ):
        if not sub and sub_id:
            sub = Subscription(
                user_id=user.id,
                dodo_subscription_id=sub_id,
                dodo_customer_id=customer_id,
            )
            db.add(sub)
        if sub:
            sub.status = "active"
            sub.cancel_at_period_end = False
            sub.current_period_end = period_end
        if period_end:
            user.premium_until = period_end
        user.premium_grace_until = None
        log.info("subscription_activated", user_id=user.id, period_end=period_end)

    elif event_type in (
        DODO_EVENT_SUBSCRIPTION_UPDATED,
        DODO_EVENT_SUBSCRIPTION_PLAN_CHANGED,
        DODO_EVENT_SUBSCRIPTION_UNPAUSED,
    ):
        if sub:
            status = payload.get("status")
            sub.status = str(status) if status else "active"
            if "cancel_at_next_billing_date" in payload:
                sub.cancel_at_period_end = bool(payload["cancel_at_next_billing_date"])
            if period_end:
                sub.current_period_end = period_end
        if period_end and (not sub or sub.status == "active"):
            user.premium_until = period_end
        user.premium_grace_until = None

    elif event_type == DODO_EVENT_SUBSCRIPTION_PAUSED:
        if sub:
            sub.status = "paused"
        user.premium_until = None
        user.premium_grace_until = None

    elif event_type in (
        DODO_EVENT_SUBSCRIPTION_ON_HOLD,
        DODO_EVENT_SUBSCRIPTION_PAST_DUE,
        DODO_EVENT_PAYMENT_FAILED,
    ):
        if sub:
            sub.status = (
                "on_hold"
                if event_type == DODO_EVENT_SUBSCRIPTION_ON_HOLD
                else "past_due"
            )
            if period_end:
                sub.current_period_end = period_end
        if event_type == DODO_EVENT_SUBSCRIPTION_ON_HOLD:
            user.premium_grace_until = None
        elif sub is not None and prior_status in {"active", "past_due", "on_hold"}:
            # A subscription that was already paying failed to renew. Keep
            # Premium for the dunning window so the customer can fix payment.
            grace_until_raw = payload.get("past_due_ends_at")
            if grace_until_raw:
                try:
                    user.premium_grace_until = datetime.fromisoformat(
                        str(grace_until_raw).replace("Z", "+00:00")
                    )
                except ValueError:
                    user.premium_grace_until = None
            if not user.premium_grace_until:
                user.premium_grace_until = utcnow() + timedelta(
                    days=get_settings().past_due_grace_days
                )
        # Otherwise there is no paying subscription to protect — a declined
        # first payment (or an unknown subscription) must not grant Premium.
        log.info(
            "subscription_past_due",
            user_id=user.id,
            prior_status=prior_status,
        )

    elif event_type == DODO_EVENT_SUBSCRIPTION_CANCELLED:
        if sub:
            sub.status = "cancelled"
            sub.cancel_at_period_end = True
            if period_end:
                sub.current_period_end = period_end
                user.premium_until = period_end
        user.premium_grace_until = None
        log.info("subscription_cancelled", user_id=user.id)

    elif event_type == DODO_EVENT_SUBSCRIPTION_FAILED:
        if sub:
            sub.status = "failed"
        user.premium_grace_until = None
        log.info("subscription_creation_failed", user_id=user.id)

    elif event_type == DODO_EVENT_SUBSCRIPTION_EXPIRED:
        if sub:
            sub.status = "expired"
        user.premium_until = None
        user.premium_grace_until = None
        log.info("subscription_expired", user_id=user.id)

    elif event_type == DODO_EVENT_PAYMENT_SUCCEEDED:
        paid_through = period_end or (sub.current_period_end if sub else None)
        if sub and paid_through:
            sub.current_period_end = paid_through
            if sub.status in ("past_due", "on_hold"):
                sub.status = "active"
            if paid_through > utcnow():
                user.premium_until = paid_through
                user.premium_grace_until = None

    await db.flush()

    # Reconcile channels after every billing event
    from app.services.plans import reconcile_channels

    await reconcile_channels(user, db)
    await record_audit(
        db,
        "billing_webhook",
        user_id=user.id,
        detail={"event_type": event_type, "subscription_id": sub_id},
    )
