"""Dodo Payments client.

References:
- https://docs.dodopayments.com/developer-resources/checkout-session
- https://docs.dodopayments.com/developer-resources/webhooks
- https://docs.dodopayments.com/api-reference/customers/create-customer-portal-session
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import time
from typing import Any
from urllib.parse import quote

import httpx

from app.core.config import get_settings
from app.core.errors import AppException, ErrorCode
from app.core.logging import get_logger

log = get_logger(__name__)


def _get_base_url(env: str) -> str:
    if env == "live":
        return "https://live.dodopayments.com"
    return "https://test.dodopayments.com"


class DodoAPIError(AppException):
    """Safe error for failed Dodo API requests."""

    def __init__(
        self,
        operation: str,
        status_code: int | None = None,
        *,
        provider_code: str | None = None,
        provider_message: str | None = None,
    ) -> None:
        details: dict[str, Any] = {}
        if status_code is not None:
            details["provider_status"] = status_code
        if provider_code:
            details["provider_code"] = provider_code
        if provider_message:
            details["provider_message"] = provider_message
        super().__init__(
            code=ErrorCode.BILLING_ERROR,
            message=f"Dodo {operation} request failed",
            details=details,
            status_code=502,
        )


def _provider_error(response: httpx.Response) -> tuple[str | None, str | None]:
    """Pull the Dodo-provided error code and message from a failed response."""
    try:
        body = response.json()
    except (ValueError, TypeError):
        return None, None
    if not isinstance(body, dict):
        return None, None
    code = body.get("code")
    message = body.get("message")
    return (
        code if isinstance(code, str) and code else None,
        message if isinstance(message, str) and message else None,
    )


class DodoClient:
    """HTTP client for Dodo Payments API."""

    def __init__(self) -> None:
        settings = get_settings()
        self._api_key = settings.dodo_api_key
        self._base = _get_base_url(settings.dodo_env)
        self._product_id = settings.dodo_premium_product_id
        self._settings = settings

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    async def create_checkout(
        self, user_id: int, customer_email: str | None = None
    ) -> dict[str, Any]:
        """Create a documented Dodo checkout session for the configured product.

        Dodo sends every outcome (success or failure) to ``return_url`` and
        appends ``status``, ``payment_id``/``subscription_id`` and ``email``.
        The frontend success page routes a failed status to ``/failed``. A
        deliberate cancel or back navigation goes to ``cancel_url``.

        The signed-in user's email is prefilled, but ``allow_customer_editing_email``
        lets the buyer type a different email at checkout (for example a gift
        receipt) without losing the ``user_id`` entitlement link.
        """
        payload: dict[str, Any] = {
            "product_cart": [{"product_id": self._product_id, "quantity": 1}],
            "metadata": {"user_id": str(user_id)},
            "feature_flags": {"allow_customer_editing_email": True},
            "return_url": f"{self._settings.site_url}/success",
            "cancel_url": f"{self._settings.site_url}/failed",
        }
        if customer_email:
            payload["customer"] = {"email": customer_email}

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post(
                    f"{self._base}/checkouts",
                    json=payload,
                    headers=self._headers(),
                )
        except httpx.HTTPError as exc:
            log.error("dodo_checkout_transport_failed", error_type=type(exc).__name__)
            raise DodoAPIError("checkout") from exc
        if response.status_code != 200:
            log.error("dodo_checkout_failed", status=response.status_code)
            provider_code, provider_message = _provider_error(response)
            raise DodoAPIError(
                "checkout",
                response.status_code,
                provider_code=provider_code,
                provider_message=provider_message,
            )
        return response.json()

    async def create_portal(self, customer_id: str) -> dict[str, Any]:
        """Create a documented Dodo customer portal session."""
        encoded_customer_id = quote(customer_id, safe="")
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post(
                    f"{self._base}/customers/{encoded_customer_id}/customer-portal/session",
                    params={"return_url": f"{self._settings.site_url}/billing"},
                    headers=self._headers(),
                )
        except httpx.HTTPError as exc:
            log.error("dodo_portal_transport_failed", error_type=type(exc).__name__)
            raise DodoAPIError("portal") from exc
        if response.status_code != 200:
            log.error("dodo_portal_failed", status=response.status_code)
            provider_code, provider_message = _provider_error(response)
            raise DodoAPIError(
                "portal",
                response.status_code,
                provider_code=provider_code,
                provider_message=provider_message,
            )
        return response.json()

    async def get_subscription(self, dodo_subscription_id: str) -> dict[str, Any]:
        """Fetch subscription status directly from Dodo."""
        encoded_subscription_id = quote(dodo_subscription_id, safe="")
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(
                    f"{self._base}/subscriptions/{encoded_subscription_id}",
                    headers=self._headers(),
                )
        except httpx.HTTPError as exc:
            log.error(
                "dodo_subscription_transport_failed", error_type=type(exc).__name__
            )
            raise DodoAPIError("subscription lookup") from exc
        if response.status_code != 200:
            log.error("dodo_subscription_lookup_failed", status=response.status_code)
            provider_code, provider_message = _provider_error(response)
            raise DodoAPIError(
                "subscription lookup",
                response.status_code,
                provider_code=provider_code,
                provider_message=provider_message,
            )
        return response.json()

    async def cancel_subscription(self, dodo_subscription_id: str) -> dict[str, Any]:
        """Schedule cancellation at the end of the current billing period."""
        encoded_subscription_id = quote(dodo_subscription_id, safe="")
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.patch(
                    f"{self._base}/subscriptions/{encoded_subscription_id}",
                    json={"cancel_at_next_billing_date": True},
                    headers=self._headers(),
                )
        except httpx.HTTPError as exc:
            log.error(
                "dodo_subscription_cancel_transport_failed",
                error_type=type(exc).__name__,
            )
            raise DodoAPIError("subscription cancellation") from exc
        if response.status_code != 200:
            log.error(
                "dodo_subscription_cancel_failed",
                status=response.status_code,
            )
            provider_code, provider_message = _provider_error(response)
            raise DodoAPIError(
                "subscription cancellation",
                response.status_code,
                provider_code=provider_code,
                provider_message=provider_message,
            )
        return response.json()


def verify_webhook_signature(
    payload_bytes: bytes,
    webhook_id: str | None,
    webhook_timestamp: str | None,
    signature_header: str | None,
    secret: str,
    *,
    tolerance_seconds: int = 300,
) -> bool:
    """Verify Dodo's Standard Webhooks HMAC-SHA256 signature."""
    if not all((webhook_id, webhook_timestamp, signature_header, secret)):
        return False
    try:
        timestamp = int(webhook_timestamp)
        if abs(time.time() - timestamp) > tolerance_seconds:
            return False
        secret_value = secret.removeprefix("whsec_")
        key = base64.b64decode(secret_value, validate=True)
        signed_content = b".".join(
            (
                webhook_id.encode(),
                webhook_timestamp.encode(),
                payload_bytes,
            )
        )
        expected = base64.b64encode(
            hmac.new(key, signed_content, hashlib.sha256).digest()
        ).decode()
        for versioned_signature in signature_header.split():
            version, separator, signature = versioned_signature.partition(",")
            if (
                separator
                and version == "v1"
                and hmac.compare_digest(signature, expected)
            ):
                return True
        return False
    except (ValueError, TypeError, binascii.Error, UnicodeEncodeError):
        return False


DODO_EVENT_SUBSCRIPTION_ACTIVE = "subscription.active"
DODO_EVENT_SUBSCRIPTION_UPDATED = "subscription.updated"
DODO_EVENT_SUBSCRIPTION_PLAN_CHANGED = "subscription.plan_changed"
DODO_EVENT_SUBSCRIPTION_PAUSED = "subscription.paused"
DODO_EVENT_SUBSCRIPTION_UNPAUSED = "subscription.unpaused"
DODO_EVENT_SUBSCRIPTION_RENEWED = "subscription.renewed"
DODO_EVENT_PAYMENT_SUCCEEDED = "payment.succeeded"
DODO_EVENT_SUBSCRIPTION_ON_HOLD = "subscription.on_hold"
DODO_EVENT_SUBSCRIPTION_PAST_DUE = "subscription.past_due"
DODO_EVENT_SUBSCRIPTION_FAILED = "subscription.failed"
DODO_EVENT_PAYMENT_FAILED = "payment.failed"
DODO_EVENT_SUBSCRIPTION_CANCELLED = "subscription.cancelled"
DODO_EVENT_SUBSCRIPTION_EXPIRED = "subscription.expired"
