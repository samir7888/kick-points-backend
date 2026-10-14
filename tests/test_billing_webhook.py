"""Billing webhook entitlement tests.

Premium must only follow a verified payment: a declined first checkout must
leave the account free, while a failed renewal of a paying subscription keeps
a short grace window.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.db.models import Subscription, User
from app.integrations.dodo.client import (
    DODO_EVENT_PAYMENT_FAILED,
    DODO_EVENT_SUBSCRIPTION_ACTIVE,
    DODO_EVENT_SUBSCRIPTION_EXPIRED,
    DODO_EVENT_SUBSCRIPTION_PAST_DUE,
)
from app.services.billing import apply_webhook_event
from app.services.plans import is_premium


def _future(days: int = 30) -> datetime:
    return datetime.now(UTC) + timedelta(days=days)


async def _add_user(db_session, kick_user_id: int, **kwargs) -> User:
    user = User(kick_user_id=kick_user_id, username=f"user-{kick_user_id}", **kwargs)
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest.mark.asyncio
async def test_declined_first_payment_does_not_grant_premium(db_session):
    """A failed checkout with no prior subscription must not enable Premium."""
    user = await _add_user(db_session, 7_700_001)

    await apply_webhook_event(
        DODO_EVENT_PAYMENT_FAILED,
        {
            "metadata": {"user_id": str(user.id)},
            "subscription_id": "sub_declined",
            "customer": {"customer_id": "cus_declined"},
            "status": "failed",
        },
        user,
        db_session,
    )

    assert user.premium_until is None
    assert user.premium_grace_until is None
    assert is_premium(user) is False


@pytest.mark.asyncio
async def test_failed_payment_on_inactive_subscription_grants_no_grace(db_session):
    """A subscription that was never active gets no dunning grace."""
    user = await _add_user(db_session, 7_700_002)
    db_session.add(
        Subscription(
            user_id=user.id,
            dodo_subscription_id="sub_never_paid",
            dodo_customer_id="cus_never_paid",
            status="failed",
        )
    )
    await db_session.commit()

    await apply_webhook_event(
        DODO_EVENT_PAYMENT_FAILED,
        {
            "subscription_id": "sub_never_paid",
            "customer": {"customer_id": "cus_never_paid"},
        },
        user,
        db_session,
    )

    assert user.premium_until is None
    assert user.premium_grace_until is None
    assert is_premium(user) is False


@pytest.mark.asyncio
async def test_failed_renewal_keeps_premium_for_grace_window(db_session):
    """A renewal failure on an active subscription keeps a short grace period."""
    user = await _add_user(db_session, 7_700_003, premium_until=_future(5))
    sub = Subscription(
        user_id=user.id,
        dodo_subscription_id="sub_renew",
        dodo_customer_id="cus_renew",
        status="active",
        current_period_end=_future(5),
    )
    db_session.add(sub)
    await db_session.commit()

    await apply_webhook_event(
        DODO_EVENT_SUBSCRIPTION_PAST_DUE,
        {"subscription_id": "sub_renew", "customer": {"customer_id": "cus_renew"}},
        user,
        db_session,
    )

    assert sub.status == "past_due"
    assert user.premium_grace_until is not None
    assert user.premium_grace_until > datetime.now(UTC)
    assert is_premium(user) is True


@pytest.mark.asyncio
async def test_subscription_active_grants_premium(db_session):
    """Only a verified active subscription sets the paid-through date."""
    user = await _add_user(db_session, 7_700_004)

    await apply_webhook_event(
        DODO_EVENT_SUBSCRIPTION_ACTIVE,
        {
            "subscription_id": "sub_active",
            "customer": {"customer_id": "cus_active"},
            "next_billing_date": _future(30).isoformat(),
        },
        user,
        db_session,
    )

    assert user.premium_until is not None
    assert user.premium_grace_until is None
    assert is_premium(user) is True


@pytest.mark.asyncio
async def test_subscription_expired_revokes_premium(db_session):
    """Expiry clears both the paid-through date and any grace window."""
    user = await _add_user(db_session, 7_700_005, premium_until=_future(5))
    db_session.add(
        Subscription(
            user_id=user.id,
            dodo_subscription_id="sub_exp",
            dodo_customer_id="cus_exp",
            status="active",
            current_period_end=_future(5),
        )
    )
    await db_session.commit()

    await apply_webhook_event(
        DODO_EVENT_SUBSCRIPTION_EXPIRED,
        {"subscription_id": "sub_exp", "customer": {"customer_id": "cus_exp"}},
        user,
        db_session,
    )

    assert user.premium_until is None
    assert user.premium_grace_until is None
    assert is_premium(user) is False
