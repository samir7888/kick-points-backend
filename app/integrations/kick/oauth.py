"""Kick.com OAuth 2.1 + PKCE implementation."""

import base64
import hashlib
from typing import Any
from urllib.parse import urlencode

import httpx

from ...core.config import get_settings
from ...core.errors import AppException, ErrorCode
from ...core.logging import get_logger
from ...core.metrics import track_kick_call

logger = get_logger(__name__)


async def exchange_code(
    code: str,
    code_verifier: str,
    redirect_uri: str | None = None,
) -> dict[str, Any]:
    """Exchange an OAuth authorization code for a Kick token pair."""
    client = KickOAuth()
    try:
        if redirect_uri is None:
            redirect_uri = get_settings().kick_redirect_uri
        return await client.exchange_code_for_tokens(
            code, code_verifier, redirect_uri=redirect_uri
        )
    finally:
        await client.close()


async def fetch_kick_user(access_token: str) -> dict[str, Any]:
    """Fetch the authenticated Kick user profile."""
    client = KickOAuth()
    try:
        return await client.get_user_info(access_token)
    finally:
        await client.close()


async def revoke_token(token: str, token_type: str = "access_token") -> bool:
    """Revoke a Kick access or refresh token."""
    client = KickOAuth()
    try:
        return await client.revoke_token(token, token_type=token_type)
    finally:
        await client.close()


class KickOAuthError(AppException):
    """Kick OAuth specific error."""

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(
            code=ErrorCode.KICK_API_ERROR,
            message=message,
            details=details or {},
            status_code=400,
        )


class KickOAuth:
    """Handles Kick.com OAuth 2.1 + PKCE flow."""

    def __init__(self):
        self.settings = get_settings()
        self.client = httpx.AsyncClient(timeout=self.settings.kick_timeout_seconds)

    def generate_authorization_url(self, state: str, code_verifier: str) -> str:
        """
        Generate the authorization URL for Kick OAuth.

        """
        code_challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )

        params = {
            "response_type": "code",
            "client_id": self.settings.kick_client_id,
            "redirect_uri": self.settings.kick_redirect_uri,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }

        # Add scopes if configured
        if self.settings.kick_scopes:
            params["scope"] = self.settings.kick_scopes

        return f"{self.settings.kick_authorize_url}?{urlencode(params)}"

    async def exchange_code_for_tokens(
        self,
        code: str,
        code_verifier: str,
        *,
        redirect_uri: str | None = None,
    ) -> dict[str, Any]:
        """
        Exchange authorization code for access and refresh tokens.

        """
        data = {
            "grant_type": "authorization_code",
            "client_id": self.settings.kick_client_id,
            "client_secret": self.settings.kick_client_secret,
            "code": code,
            "redirect_uri": redirect_uri or self.settings.kick_redirect_uri,
            "code_verifier": code_verifier,
        }

        try:
            response = await track_kick_call(
                "oauth_token_exchange",
                self.client.post(
                    self.settings.kick_token_url,
                    data=data,
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                ),
            )

            if response.status_code != 200:
                logger.warning(
                    "Kick token exchange failed",
                    status_code=response.status_code,
                )
                raise KickOAuthError(
                    f"Failed to exchange code for tokens: {response.status_code}",
                    {"status_code": response.status_code},
                )

            try:
                token_data = response.json()
            except ValueError as exc:
                raise KickOAuthError("Kick returned an invalid token response") from exc
            if not isinstance(token_data, dict):
                raise KickOAuthError("Kick returned an invalid token response")

            required_fields = ["access_token", "token_type"]
            for field in required_fields:
                if field not in token_data:
                    raise KickOAuthError(
                        f"Missing required field '{field}' in token response",
                    )

            return token_data

        except httpx.RequestError as exc:
            logger.error(
                "Network error during token exchange",
                error_type=type(exc).__name__,
            )
            raise KickOAuthError("Network error during token exchange") from exc

    async def get_user_info(self, access_token: str) -> dict[str, Any]:
        """
        Get user information from Kick API using access token.

        """
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }

        user_info_url = f"{self.settings.kick_api_base.rstrip('/')}/users"

        try:
            response = await track_kick_call(
                "oauth_user_info", self.client.get(user_info_url, headers=headers)
            )

            if response.status_code == 401:
                raise KickOAuthError("Invalid or expired access token")
            elif response.status_code != 200:
                logger.warning(
                    "Failed to fetch user info",
                    status_code=response.status_code,
                )
                raise KickOAuthError(
                    f"Failed to fetch user info: {response.status_code}",
                    {"status_code": response.status_code},
                )

            try:
                response_data = response.json()
            except ValueError as exc:
                raise KickOAuthError("Kick returned an invalid user response") from exc
            if not isinstance(response_data, dict):
                raise KickOAuthError("Kick returned an invalid user response")

            # Kick's documented response wraps the current user's record in data.
            user_data = response_data.get("data")
            if (
                not isinstance(user_data, list)
                or not user_data
                or not isinstance(user_data[0], dict)
            ):
                raise KickOAuthError("Kick user response contained no users")
            user_data = user_data[0]
            required_fields = ["user_id", "name"]
            for field in required_fields:
                if field not in user_data:
                    raise KickOAuthError(
                        f"Missing required field '{field}' in user response"
                    )

            return {
                "id": user_data["user_id"],
                "username": user_data["name"],
                "email": user_data.get("email"),
            }

        except httpx.RequestError as exc:
            logger.error(
                "Network error fetching user info",
                error_type=type(exc).__name__,
            )
            raise KickOAuthError("Network error fetching user info") from exc

    async def refresh_access_token(self, refresh_token: str) -> dict[str, Any]:
        """
        Refresh access token using refresh token.

        """
        data = {
            "grant_type": "refresh_token",
            "client_id": self.settings.kick_client_id,
            "client_secret": self.settings.kick_client_secret,
            "refresh_token": refresh_token,
        }

        try:
            response = await track_kick_call(
                "oauth_token_refresh",
                self.client.post(
                    self.settings.kick_token_url,
                    data=data,
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                ),
            )

            if response.status_code != 200:
                logger.warning(
                    "Token refresh failed",
                    status_code=response.status_code,
                )
                raise KickOAuthError(
                    f"Failed to refresh token: {response.status_code}",
                    {"status_code": response.status_code},
                )

            try:
                token_data = response.json()
            except ValueError as exc:
                raise KickOAuthError("Kick returned an invalid token response") from exc
            if not isinstance(token_data, dict):
                raise KickOAuthError("Kick returned an invalid token response")
            required_fields = ["access_token", "refresh_token", "token_type"]
            if any(field not in token_data for field in required_fields):
                raise KickOAuthError("Kick token response is incomplete")
            return token_data

        except httpx.RequestError as exc:
            logger.error(
                "Network error during token refresh",
                error_type=type(exc).__name__,
            )
            raise KickOAuthError("Network error during token refresh") from exc

    async def revoke_token(self, token: str, token_type: str = "access_token") -> bool:
        """
        Revoke access or refresh token.

        """
        try:
            response = await track_kick_call(
                "oauth_token_revoke",
                self.client.post(
                    self.settings.kick_revoke_url,
                    params={"token": token, "token_hint_type": token_type},
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                ),
            )

            # Token revocation should return 200 or 204
            return response.status_code in (200, 204)

        except httpx.RequestError as exc:
            logger.error(
                "Network error during token revocation",
                error_type=type(exc).__name__,
            )
            return False  # Don't raise error for revocation failures

    async def close(self):
        """Close the HTTP client."""
        await self.client.aclose()
