"""Cloudinary client for payment-proof storage.

Proof screenshots are uploaded to Cloudinary with a signed request so the API
secret never leaves the backend: the browser still posts the image to our API,
we forward it to Cloudinary, and the admin review page renders the returned
``secure_url``. Requests are signed with Cloudinary's documented SHA-1 scheme
(https://cloudinary.com/documentation/upload_images#generating_authentication_signatures)
and sent over ``httpx`` directly.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import Settings, get_settings
from app.core.errors import AppException, ErrorCode
from app.core.logging import get_logger

log = get_logger(__name__)

CLOUDINARY_TIMEOUT_SECONDS = 30.0


class CloudinaryError(AppException):
    """Safe error for a failed Cloudinary request."""

    def __init__(self, operation: str, status_code: int | None = None) -> None:
        details: dict[str, Any] = {}
        if status_code is not None:
            details["provider_status"] = status_code
        super().__init__(
            code=ErrorCode.BILLING_ERROR,
            message=f"Cloudinary {operation} failed",
            details=details,
            status_code=502,
        )


@dataclass(frozen=True)
class CloudinaryUpload:
    """The parts of a Cloudinary upload response we persist."""

    url: str
    public_id: str


def _signature(params: dict[str, str], api_secret: str) -> str:
    """Sign request parameters the way Cloudinary expects (sorted SHA-1)."""
    to_sign = "&".join(f"{key}={params[key]}" for key in sorted(params))
    return hashlib.sha1(f"{to_sign}{api_secret}".encode()).hexdigest()


class CloudinaryClient:
    """Uploads and deletes payment-proof images in Cloudinary."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    @property
    def _base(self) -> str:
        return (
            f"https://api.cloudinary.com/v1_1/{self._settings.cloudinary_cloud_name}"
        )

    def _signed_fields(self, params: dict[str, str]) -> dict[str, str]:
        """Return ``params`` plus the api key and matching signature."""
        return {
            **params,
            "api_key": self._settings.cloudinary_api_key,
            "signature": _signature(params, self._settings.cloudinary_api_secret),
        }

    async def upload_image(
        self,
        content: bytes,
        *,
        filename: str,
        content_type: str = "application/octet-stream",
        folder: str | None = None,
    ) -> CloudinaryUpload:
        """Upload one proof image and return its secure URL and public id."""
        if not self._settings.cloudinary_enabled:
            raise CloudinaryError("upload")

        target_folder = folder or self._settings.cloudinary_folder
        fields = self._signed_fields(
            {"folder": target_folder, "timestamp": str(int(time.time()))}
        )
        try:
            async with httpx.AsyncClient(timeout=CLOUDINARY_TIMEOUT_SECONDS) as client:
                response = await client.post(
                    f"{self._base}/image/upload",
                    data=fields,
                    files={"file": (filename, content, content_type)},
                )
        except httpx.HTTPError as exc:
            log.error(
                "cloudinary_upload_transport_failed", error_type=type(exc).__name__
            )
            raise CloudinaryError("upload") from exc

        if response.status_code not in (200, 201):
            log.error("cloudinary_upload_failed", status=response.status_code)
            raise CloudinaryError("upload", response.status_code)

        body = response.json()
        secure_url = body.get("secure_url") or body.get("url")
        public_id = body.get("public_id")
        if not secure_url or not public_id:
            log.error("cloudinary_upload_missing_fields", status=response.status_code)
            raise CloudinaryError("upload", response.status_code)
        return CloudinaryUpload(url=str(secure_url), public_id=str(public_id))

    async def delete_image(self, public_id: str) -> bool:
        """Delete a stored proof. Returns False when the asset is already gone."""
        if not self._settings.cloudinary_enabled or not public_id:
            return False

        fields = self._signed_fields(
            {"public_id": public_id, "timestamp": str(int(time.time()))}
        )
        try:
            async with httpx.AsyncClient(timeout=CLOUDINARY_TIMEOUT_SECONDS) as client:
                response = await client.post(
                    f"{self._base}/image/destroy", data=fields
                )
        except httpx.HTTPError as exc:
            log.warning(
                "cloudinary_delete_transport_failed", error_type=type(exc).__name__
            )
            return False

        if response.status_code != 200:
            log.warning("cloudinary_delete_failed", status=response.status_code)
            return False
        return True
