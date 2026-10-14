"""Dodo Payments client and webhook signature tests."""

import base64
import hashlib
import hmac
import json
import time

import pytest
import respx
from httpx import Response

from app.core.config import get_settings
from app.integrations.dodo.client import (
    DodoAPIError,
    DodoClient,
    verify_webhook_signature,
)

TEST_SECRET = "whsec_" + base64.b64encode(b"0123456789abcdef01234567").decode()
TIMEOUT = 15


def _signature(webhook_id: str, timestamp: int, body: bytes, secret: str) -> str:
    key = base64.b64decode(secret.removeprefix("whsec_"))
    signed = b".".join((webhook_id.encode(), str(timestamp).encode(), body))
    digest = hmac.new(key, signed, hashlib.sha256).digest()
    return f"v1,{base64.b64encode(digest).decode()}"


@pytest.mark.asyncio
async def test_create_checkout_uses_success_and_failed_return_urls() -> None:
    """The checkout session targets /success and /failed and carries user_id."""
    settings = get_settings()
    base = "https://test.dodopayments.com" if settings.dodo_env != "live" else "https://live.dodopayments.com"
    with respx.mock(assert_all_called=True) as router:
        route = router.post(f"{base}/checkouts").mock(
            return_value=Response(
                200,
                json={
                    "session_id": "cks_1",
                    "checkout_url": "https://checkout.dodopayments.com/start",
                },
            )
        )
        client = DodoClient()
        await client.create_checkout(user_id=42, customer_email="viewer@example.com")

        request = route.calls[0].request
        assert "Bearer" in request.headers.get("authorization", "")
        body = json.loads(request.content)
        assert body["metadata"] == {"user_id": "42"}
        assert body["customer"] == {"email": "viewer@example.com"}
        assert body["feature_flags"] == {"allow_customer_editing_email": True}
        assert body["return_url"] == f"{settings.site_url}/success"
        assert body["cancel_url"] == f"{settings.site_url}/failed"


@pytest.mark.asyncio
async def test_create_checkout_surfaces_provider_error() -> None:
    """A Dodo 4xx with a JSON body explains the failure (e.g. read-only key)."""
    settings = get_settings()
    base = "https://test.dodopayments.com" if settings.dodo_env != "live" else "https://live.dodopayments.com"
    with respx.mock(assert_all_called=True) as router:
        router.post(f"{base}/checkouts").mock(
            return_value=Response(
                403,
                json={
                    "code": "INSUFFICIENT_PERMISSION",
                    "message": (
                        "This API key does not have the required permission. "
                        "Required: checkout_sessions:write."
                    ),
                },
            )
        )
        client = DodoClient()
        with pytest.raises(DodoAPIError) as exc_info:
            await client.create_checkout(user_id=1, customer_email="a@b.c")

        details = exc_info.value.details
        assert exc_info.value.status_code == 502
        assert details["provider_status"] == 403
        assert details["provider_code"] == "INSUFFICIENT_PERMISSION"
        assert "checkout_sessions:write" in details["provider_message"]


def test_verify_webhook_signature_accepts_valid_headers() -> None:
    body = b'{"type":"subscription.active","data":{"subscription_id":"sub_1"}}'
    timestamp = int(time.time())
    signature = _signature("evt_1", timestamp, body, TEST_SECRET)
    assert (
        verify_webhook_signature(
            body, "evt_1", str(timestamp), signature, TEST_SECRET
        )
        is True
    )


def test_verify_webhook_signature_rejects_forged_payload() -> None:
    body = b'{"type":"subscription.active","data":{"subscription_id":"sub_1"}}'
    timestamp = int(time.time())
    signature = _signature("evt_1", timestamp, body, TEST_SECRET)
    forged = b'{"type":"subscription.active","data":{"subscription_id":"sub_2"}}'
    assert (
        verify_webhook_signature(
            forged, "evt_1", str(timestamp), signature, TEST_SECRET
        )
        is False
    )


def test_verify_webhook_signature_rejects_stale_timestamp() -> None:
    body = b'{"type":"subscription.active"}'
    stale = int(time.time()) - 600  # older than the 300 s tolerance
    signature = _signature("evt_1", stale, body, TEST_SECRET)
    assert (
        verify_webhook_signature(body, "evt_1", str(stale), signature, TEST_SECRET)
        is False
    )


def test_verify_webhook_signature_rejects_missing_headers() -> None:
    body = b'{"type":"subscription.active"}'
    assert (
        verify_webhook_signature(body, None, None, None, TEST_SECRET) is False
    )
