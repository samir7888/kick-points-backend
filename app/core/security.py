"""Security utilities for sessions, CSRF protection, and token encryption."""

import base64
import hashlib
import secrets
from datetime import UTC, datetime
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, MultiFernet
from fastapi import HTTPException, Request, status

from .config import get_settings
from .logging import get_logger

logger = get_logger(__name__)


class TokenEncryption:
    """Handles encryption/decryption of OAuth tokens using MultiFernet."""

    def __init__(self) -> None:
        settings = get_settings()
        keys = [
            key.encode() if isinstance(key, str) else key
            for key in settings.encryption_keys_list
        ]
        self._fernet = MultiFernet([Fernet(key) for key in keys])

    def encrypt(self, data: str) -> str:
        """Encrypt a string and return base64 encoded result."""
        return self._fernet.encrypt(data.encode()).decode()

    def decrypt(self, encrypted_data: str) -> str:
        """Decrypt base64 encoded data and return original string."""
        return self._fernet.decrypt(encrypted_data.encode()).decode()


def generate_session_token() -> str:
    """Generate a cryptographically secure session token."""
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    """Hash a session token for database storage."""
    return hashlib.sha256(token.encode()).hexdigest()


def generate_oauth_state() -> str:
    """Generate a secure OAuth state parameter."""
    return secrets.token_urlsafe(32)


def generate_pkce_verifier() -> str:
    """Generate a PKCE code verifier."""
    return secrets.token_urlsafe(32)


def create_pkce_challenge(verifier: str) -> str:
    """Create the S256 PKCE code challenge required by Kick OAuth."""
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    return challenge.rstrip(b"=").decode()


def encrypt_token(value: str) -> str:
    """Encrypt a token string for storage."""
    return TokenEncryption().encrypt(value)


def decrypt_token(value: str) -> str:
    """Decrypt a stored token string."""
    return TokenEncryption().decrypt(value)


def utcnow() -> datetime:
    """Return timezone-aware UTC time."""
    return datetime.now(UTC)


def verify_origin(request: Request) -> None:
    """Verify request origin for CSRF protection on state-changing requests."""
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        settings = get_settings()
        allowed_origins = settings.cors_origins_list

        # Check Origin header first, then Referer
        origin = request.headers.get("origin") or request.headers.get("referer")

        if not origin:
            logger.warning("Missing Origin/Referer header for state-changing request")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Missing Origin header",
            )

        parsed_origin = urlsplit(origin)
        normalized_origin = f"{parsed_origin.scheme}://{parsed_origin.netloc}".rstrip(
            "/"
        )
        normalized_allowed = {
            f"{urlsplit(allowed).scheme}://{urlsplit(allowed).netloc}".rstrip("/")
            for allowed in allowed_origins
        }
        if not normalized_origin.startswith(("http://", "https://")) or (
            normalized_origin not in normalized_allowed
        ):
            logger.warning(
                "Invalid origin for state-changing request",
                origin=normalized_origin,
                allowed=allowed_origins,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid origin",
            )


def create_session_cookie_params(settings: object | None = None) -> dict:
    """Create secure cookie parameters for session cookies."""
    if settings is None:
        settings = get_settings()

    return {
        "httponly": True,
        "secure": not settings.is_dev,
        "samesite": "lax",
        "domain": settings.cookie_domain
        if settings.cookie_domain != "localhost"
        else None,
        "max_age": 30 * 24 * 60 * 60,  # 30 days
    }
