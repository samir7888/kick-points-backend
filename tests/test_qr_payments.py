"""Tests for manual QR / bank-transfer payment review.

Confirming an approved transfer must grant exactly one month of Premium
(extending from any existing paid-through date), while rejecting keeps the
account free.
"""

from datetime import timedelta

import pytest
from starlette.requests import Request

from app.api.v1.payments import (
    _admin_password_dependency,
    confirm_qr_payment,
    list_qr_payments,
    reject_qr_payment,
)
from app.core.errors import UnauthorizedError
from app.core.security import utcnow
from app.db.models import QrPayment, User
from app.services.plans import is_premium

PASSWORD = "sameer12345"


def _admin_request() -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/qr-payments",
        "headers": [(b"x-payments-admin", b"tester")],
        "query_string": b"",
        "client": ("127.0.0.1", 12345),
    }
    return Request(scope)


@pytest.mark.asyncio
async def test_password_dependency_rejects_wrong_password():
    with pytest.raises(UnauthorizedError):
        await _admin_password_dependency(password="nope")
    await _admin_password_dependency(password=PASSWORD)


@pytest.mark.asyncio
async def test_confirm_grants_one_month_of_premium(db_session):
    user = User(kick_user_id=8_800_001, username="qr-user-1", email="qr1@example.com")
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    payment = QrPayment(
        user_id=user.id,
        username=user.username,
        email=user.email,
        reference="NMB-83921",
        amount=99.0,
        proof_filename="proof.png",
        status=QrPayment.PENDING,
    )
    db_session.add(payment)
    await db_session.commit()
    await db_session.refresh(payment)

    response = await confirm_qr_payment(request=_admin_request(), payment_id=payment.id, db=db_session)

    assert response["status"] == "confirmed"
    assert user.premium_until is not None
    assert is_premium(user) is True
    # Roughly 30 days from now.
    delta = user.premium_until - utcnow()
    assert timedelta(days=29) < delta <= timedelta(days=31)

    await db_session.refresh(payment)
    assert payment.status == "confirmed"
    assert payment.decided_at is not None

    listing = (await list_qr_payments(db=db_session))["items"]
    assert len(listing) == 1
    assert listing[0]["email"] == "qr1@example.com"


@pytest.mark.asyncio
async def test_confirm_extends_existing_premium(db_session):
    user = User(
        kick_user_id=8_800_002,
        username="qr-user-2",
        email="qr2@example.com",
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    existing = utcnow() + timedelta(days=10)
    user.premium_until = existing
    payment = QrPayment(
        user_id=user.id,
        username=user.username,
        email=user.email,
        proof_filename="proof2.png",
        status=QrPayment.PENDING,
    )
    db_session.add(payment)
    await db_session.commit()
    await db_session.refresh(payment)

    await confirm_qr_payment(request=_admin_request(), payment_id=payment.id, db=db_session)

    # Extends from the existing paid-through date, not from today.
    assert user.premium_until is not None
    assert user.premium_until > existing + timedelta(days=29)


@pytest.mark.asyncio
async def test_reject_does_not_grant_premium(db_session):
    user = User(kick_user_id=8_800_003, username="qr-user-3", email="qr3@example.com")
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    payment = QrPayment(
        user_id=user.id,
        username=user.username,
        email=user.email,
        proof_filename="proof3.png",
        status=QrPayment.PENDING,
    )
    db_session.add(payment)
    await db_session.commit()
    await db_session.refresh(payment)

    response = await reject_qr_payment(request=_admin_request(), payment_id=payment.id, db=db_session)

    assert response["status"] == "rejected"
    assert user.premium_until is None
    assert user.premium_grace_until is None
    assert is_premium(user) is False
    await db_session.refresh(payment)
    assert payment.status == "rejected"
    assert payment.decided_at is not None


@pytest.mark.asyncio
async def test_double_confirm_is_idempotent(db_session):
    user = User(kick_user_id=8_800_004, username="qr-user-4")
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    payment = QrPayment(
        user_id=user.id, username=user.username, proof_filename="proof4.png"
    )
    db_session.add(payment)
    await db_session.commit()
    await db_session.refresh(payment)
    first_until = user.premium_until

    await confirm_qr_payment(request=_admin_request(), payment_id=payment.id, db=db_session)
    before = user.premium_until
    await confirm_qr_payment(request=_admin_request(), payment_id=payment.id, db=db_session)
    assert user.premium_until == before
    assert first_until is None
