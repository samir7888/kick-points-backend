"""Cloudinary-backed QR payment proof tests.

When Cloudinary is configured the upload endpoint forwards the screenshot to
Cloudinary (mocked with respx), stores the returned secure URL and public id,
and the admin proof endpoint redirects to that URL instead of reading disk.
"""

import io

import httpx
import pytest
import respx

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.db.models import User
from app.db.session import get_db_session

PASSWORD = "sameer12345"
ORIGIN = "http://localhost:3000"
CLOUD_NAME = "kick-test-cloud"
SECURE_URL = (
    "https://res.cloudinary.com/kick-test-cloud/image/upload/"
    "v1/kick-payment-proofs/proof-abc123.png"
)
PUBLIC_ID = "kick-payment-proofs/proof-abc123"


def _loop_local_db_override():
    """See test_qr_payments_api: bind the DB session to the request loop."""

    async def _override():
        from sqlalchemy.ext.asyncio import (
            AsyncSession,
            async_sessionmaker,
            create_async_engine,
        )

        from app.db import session as db_session_module

        engine = create_async_engine(str(get_settings().database_url), echo=False)
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
            db_session_module._redis_client = None

    return _override


def _fake_current_user(user: User):
    async def _override():
        yield user

    return _override


@pytest.fixture
def cloudinary_settings(monkeypatch):
    """Point the cached settings at a fake Cloudinary account."""
    settings = get_settings()
    monkeypatch.setattr(settings, "cloudinary_cloud_name", CLOUD_NAME)
    monkeypatch.setattr(settings, "cloudinary_api_key", "test-key")
    monkeypatch.setattr(settings, "cloudinary_api_secret", "test-secret")
    monkeypatch.setattr(settings, "cloudinary_folder", "kick-payment-proofs")
    return settings


@pytest.mark.asyncio
@respx.mock
async def test_qr_payment_upload_to_cloudinary(
    test_app, db_session, cloudinary_settings
):
    from fastapi.testclient import TestClient

    user = User(kick_user_id=9_910_001, username="cloud-user", email="c@example.com")
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    test_app.dependency_overrides[get_db_session] = _loop_local_db_override()
    test_app.dependency_overrides[get_current_user] = _fake_current_user(user)
    client = TestClient(test_app)

    upload_route = respx.post(
        f"https://api.cloudinary.com/v1_1/{CLOUD_NAME}/image/upload"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "secure_url": SECURE_URL,
                "public_id": PUBLIC_ID,
                "url": SECURE_URL,
            },
        )
    )

    upload = client.post(
        "/api/v1/billing/qr-payments",
        headers={"Origin": ORIGIN},
        files={"proof": ("proof.png", io.BytesIO(b"fake-png"), "image/png")},
    )
    assert upload.status_code == 200, upload.text
    assert upload_route.called

    listed = client.get(
        "/api/v1/qr-payments", headers={"X-Payments-Password": PASSWORD}
    )
    assert listed.status_code == 200, listed.text
    items = listed.json()["items"]
    assert len(items) == 1
    assert items[0]["proof_url"] == SECURE_URL
    payment_id = items[0]["id"]

    proof = client.get(
        f"/api/v1/qr-payments/{payment_id}/proof",
        headers={"X-Payments-Password": PASSWORD},
    )
    assert proof.status_code == 307
    assert proof.headers["location"] == SECURE_URL


@pytest.mark.asyncio
@respx.mock
async def test_qr_payment_cloudinary_failure_is_recoverable(
    test_app, db_session, cloudinary_settings
):
    from fastapi.testclient import TestClient

    user = User(kick_user_id=9_910_002, username="cloud-user-2")
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    test_app.dependency_overrides[get_db_session] = _loop_local_db_override()
    test_app.dependency_overrides[get_current_user] = _fake_current_user(user)
    client = TestClient(test_app)

    respx.post(
        f"https://api.cloudinary.com/v1_1/{CLOUD_NAME}/image/upload"
    ).mock(return_value=httpx.Response(500, json={"error": {"message": "boom"}}))

    upload = client.post(
        "/api/v1/billing/qr-payments",
        headers={"Origin": ORIGIN},
        files={"proof": ("proof.png", io.BytesIO(b"fake-png"), "image/png")},
    )
    assert upload.status_code == 422, upload.text
    assert "Could not save your proof" in upload.json()["error"]["message"]


@pytest.mark.asyncio
async def test_upload_requires_cloudinary_credentials():
    from app.core.config import Settings
    from app.integrations.cloudinary import CloudinaryClient, CloudinaryError

    settings = Settings(
        cloudinary_cloud_name="", cloudinary_api_key="", cloudinary_api_secret=""
    )
    assert settings.cloudinary_enabled is False
    with pytest.raises(CloudinaryError):
        await CloudinaryClient(settings).upload_image(b"x", filename="a.png")
