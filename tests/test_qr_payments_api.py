"""HTTP-level tests for the QR payment flow.

Covers proof upload (authenticated), the password-gated admin list/proof, and
confirm/reject. Uses the real app with only auth and DB dependencies overridden.

TestClient runs every request in its *own* event loop (a fresh anyio portal is
spawned per call unless the client is entered as a context manager), so an
AsyncSession created in the pytest-asyncio loop cannot be handed to the app —
the greenlet/connection is bound to the wrong loop and raises
"another operation is in progress". The ``get_db_session`` override below
therefore creates a brand-new engine and session lazily *inside* the request
loop and disposes them afterwards, which keeps every connection tied to the
loop that actually uses it. The same loop-affinity applies to the shared Redis
client, so the override also drops the cached client on teardown of every
request; the next request (in a new loop) then builds its own.
"""

import io

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.db.models import User
from app.db.session import get_db_session
from app.services.plans import is_premium

PASSWORD = "sameer12345"
ORIGIN = "http://localhost:3000"


def _test_database_url() -> str:
    """Return the test database URL the app is currently configured against."""
    from app.core.config import get_settings

    return str(get_settings().database_url)


def _loop_local_db_override():
    """Build a ``get_db_session`` override bound to the requesting event loop."""

    async def _override():
        from sqlalchemy.ext.asyncio import (
            AsyncSession,
            async_sessionmaker,
            create_async_engine,
        )

        from app.db import session as db_session_module

        engine = create_async_engine(_test_database_url(), echo=False)
        maker = async_sessionmaker(
            bind=engine, class_=AsyncSession, expire_on_commit=False
        )
        try:
            async with maker() as session:
                try:
                    yield session
                finally:
                    await session.rollback()
        finally:
            await engine.dispose()
            # Any Redis client created during this request is bound to this
            # request's loop; clear it so the next request (new loop) starts
            # fresh instead of reusing a connection from a dead loop.
            db_session_module._redis_client = None

    return _override


def _fake_current_user(user: User):
    async def _override():
        yield user

    return _override


def _qr_client(test_app, user) -> TestClient:
    """Wire the loop-local DB override + fake user into the app."""
    test_app.dependency_overrides[get_db_session] = _loop_local_db_override()
    test_app.dependency_overrides[get_current_user] = _fake_current_user(user)
    return TestClient(test_app)


@pytest.mark.asyncio
async def test_qr_payment_http_flow(test_app, db_session):
    user = User(
        kick_user_id=9_900_001,
        username="qr-http-tester",
        email="qr-http@example.com",
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    client = _qr_client(test_app, user)

    # 1. Upload a proof as the user.
    upload = client.post(
        "/api/v1/billing/qr-payments",
        headers={"Origin": ORIGIN},
        data={"reference": "NMB-QR-1", "amount": "1199"},
        files={"proof": ("proof.png", io.BytesIO(b"fake-png"), "image/png")},
    )
    assert upload.status_code == 200, upload.text
    assert upload.json()["status"] == "pending"

    # 2. The user can list their own submissions.
    mine = client.get("/api/v1/billing/qr-payments")
    assert mine.status_code == 200
    assert len(mine.json()["items"]) == 1

    # 3. Wrong password is rejected on the admin list.
    denied = client.get(
        "/api/v1/qr-payments", headers={"X-Payments-Password": "wrong"}
    )
    assert denied.status_code == 401

    # 4. Correct password lists the submission with the user's email.
    listed = client.get(
        "/api/v1/qr-payments", headers={"X-Payments-Password": PASSWORD}
    )
    assert listed.status_code == 200, listed.text
    items = listed.json()["items"]
    assert len(items) == 1
    payment_id = items[0]["id"]
    assert items[0]["email"] == "qr-http@example.com"
    assert items[0]["reference"] == "NMB-QR-1"
    assert items[0]["amount"] == 1199

    # 5. The proof file is served behind the password.
    proof = client.get(
        f"/api/v1/qr-payments/{payment_id}/proof",
        headers={"X-Payments-Password": PASSWORD},
    )
    assert proof.status_code == 200
    assert proof.content == b"fake-png"

    # 6. Confirming grants Premium and marks the submission confirmed.
    confirmed = client.post(
        f"/api/v1/qr-payments/{payment_id}/confirm",
        headers={"X-Payments-Password": PASSWORD, "Origin": ORIGIN},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "confirmed"
    assert confirmed.json()["premium_until"] is not None

    # The request-loop session updated the row, so re-read it in this loop.
    await db_session.refresh(user)
    assert is_premium(user) is True

    after = client.get(
        "/api/v1/qr-payments", headers={"X-Payments-Password": PASSWORD}
    ).json()["items"]
    assert after[0]["status"] == "confirmed"
    assert after[0]["decided_at"] is not None


@pytest.mark.asyncio
async def test_qr_payment_reject_http_flow(test_app, db_session):
    user = User(kick_user_id=9_900_002, username="qr-http-tester-2")
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    client = _qr_client(test_app, user)

    upload = client.post(
        "/api/v1/billing/qr-payments",
        headers={"Origin": ORIGIN},
        files={"proof": ("proof2.png", io.BytesIO(b"fake-png"), "image/png")},
    )
    assert upload.status_code == 200, upload.text
    payment_id = upload.json()["id"]

    rejected = client.post(
        f"/api/v1/qr-payments/{payment_id}/reject",
        headers={"X-Payments-Password": PASSWORD, "Origin": ORIGIN},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "rejected"

    await db_session.refresh(user)
    assert is_premium(user) is False


@pytest.mark.asyncio
async def test_qr_payment_submit_rejected_for_premium_user(test_app, db_session):
    """A Premium account must not be able to submit a (second) payment proof."""
    from datetime import timedelta

    from app.core.security import utcnow

    user = User(
        kick_user_id=9_900_003,
        username="qr-http-tester-3",
        premium_until=utcnow() + timedelta(days=30),
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    client = _qr_client(test_app, user)

    upload = client.post(
        "/api/v1/billing/qr-payments",
        headers={"Origin": ORIGIN},
        files={"proof": ("proof3.png", io.BytesIO(b"fake-png"), "image/png")},
    )
    assert upload.status_code == 422, upload.text
    assert upload.json()["error"]["message"].startswith(
        "You already have a Premium account"
    )

    mine = client.get("/api/v1/billing/qr-payments")
    assert mine.status_code == 200
    assert mine.json()["items"] == []
