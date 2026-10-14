"""Application configuration using pydantic-settings."""

import base64
import binascii
from functools import lru_cache
from typing import Any
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from pydantic import Field, PostgresDsn, RedisDsn, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_ignore_empty=True,
        case_sensitive=False,
        extra="forbid",
    )

    # App environment
    app_env: str = Field(default="dev", pattern=r"^(dev|prod)$")
    secret_key: str = Field(
        default="dev-secret-key-32-chars-minimum-length",
        min_length=32,
    )
    token_encryption_keys: str = Field(
        default="P2s3w6Y9lq1J5fG5lK0qZ3y5H4s7r2t8lN4mP7xQn0g=",
        description="Comma-separated Fernet keys for token encryption",
    )
    session_cookie_name: str = Field(default="kick_session")
    session_ttl_days: int = Field(default=30, ge=1)
    max_request_body_bytes: int = Field(default=1_048_576, ge=1024)

    # Database & Redis
    database_url: PostgresDsn = Field(
        default="postgresql+asyncpg://postgres:password@localhost:5433/kick_predictions"
    )
    redis_url: RedisDsn = Field(default="redis://localhost:6380/0")

    # API URLs
    api_url: str = Field(default="http://localhost:8000")
    site_url: str = Field(default="http://localhost:3000")
    cors_origins: str = Field(default="http://localhost:3000")
    cookie_domain: str = Field(default="localhost")

    # Admin users
    admin_kick_user_ids: str = Field(
        default="", description="Comma-separated kick_user_id values"
    )

    # OAuth defaults follow Kick's docs; prediction voting remains unverified.
    kick_client_id: str = Field(default="dev_client_id")
    kick_client_secret: str = Field(default="dev_client_secret")
    kick_authorize_url: str = "https://id.kick.com/oauth/authorize"
    kick_token_url: str = "https://id.kick.com/oauth/token"
    kick_revoke_url: str = "https://id.kick.com/oauth/revoke"
    kick_api_base: str = "https://api.kick.com/public/v1"
    kick_scopes: str = Field(default="", description="Space-separated scopes")
    kick_redirect_uri: str = ""
    kick_timeout_seconds: float = Field(default=15.0, gt=0)
    kick_gateway: str = Field(
        default="auto",
        pattern=r"^(auto|fake|real)$",
        description=(
            "Kick gateway implementation: 'fake' (deterministic stub), 'real' "
            "(legacy Kick.com client + Pusher listener), or 'auto' (fake in dev, "
            "real otherwise)."
        ),
    )

    # Dodo Payments
    dodo_env: str = Field(default="test", pattern=r"^(test|live)$")
    dodo_api_key: str = Field(default="dev_dodo_api_key")
    dodo_webhook_secret: str = Field(default="dev_dodo_webhook_secret")
    dodo_premium_product_id: str = Field(default="dev_premium_product_id")
    canary_channel_slug: str = ""
    canary_access_token: str = ""

    # Manual QR / bank-transfer payments (Nepal)
    payments_admin_password: str = Field(
        default="sameer12345",
        min_length=6,
        description="Password for the /payments review page and admin APIs",
    )
    qr_payment_dir: str = Field(
        default="./uploads/qr-proofs",
        description="Directory where uploaded payment proofs are stored",
    )
    max_upload_body_bytes: int = Field(
        default=8 * 1024 * 1024,
        ge=1024,
        description="Hard cap for the payment-proof upload request body",
    )

    # Product rules
    premium_price_display: float = 0.99
    free_channel_limit: int = 1
    premium_channel_limit: int = 6
    min_vote_amount: int = 10
    past_due_grace_days: int = 3
    min_seconds_before_close: int = 5
    vote_jitter_max_seconds: int = 2

    @field_validator("kick_redirect_uri", mode="before")
    @classmethod
    def build_redirect_uri(cls, v: str, info: Any) -> str:
        """Build redirect URI from API_URL if not explicitly set."""
        if v:
            return v
        api_url = info.data.get("api_url", "http://localhost:8000")
        return f"{api_url}/api/v1/auth/kick/callback"

    @model_validator(mode="after")
    def validate_production_settings(self) -> "Settings":
        if not self.is_production:
            return self
        keys = [part.strip() for part in self.token_encryption_keys.split(",")]
        if not keys or any(not key for key in keys):
            raise ValueError("TOKEN_ENCRYPTION_KEYS must contain Fernet keys")
        for key in keys:
            Fernet(key.encode())
        try:
            base64.b64decode(
                self.dodo_webhook_secret.removeprefix("whsec_"),
                validate=True,
            )
        except (binascii.Error, ValueError) as exc:
            raise ValueError(
                "DODO_WEBHOOK_SECRET must be a Standard Webhooks key"
            ) from exc
        required_values = {
            "SECRET_KEY": self.secret_key,
            "KICK_CLIENT_ID": self.kick_client_id,
            "KICK_CLIENT_SECRET": self.kick_client_secret,
            "DODO_API_KEY": self.dodo_api_key,
            "DODO_WEBHOOK_SECRET": self.dodo_webhook_secret,
            "DODO_PREMIUM_PRODUCT_ID": self.dodo_premium_product_id,
            "CANARY_ACCESS_TOKEN": self.canary_access_token,
            "KICK_SCOPES": self.kick_scopes,
        }
        placeholders = {
            "dev-secret-key-32-chars-minimum-length",
            "dev_client_id",
            "dev_client_secret",
            "dev_dodo_api_key",
            "dev_dodo_webhook_secret",
            "dev_premium_product_id",
        }
        missing = [
            name
            for name, value in required_values.items()
            if not value or value in placeholders
        ]
        if missing:
            raise ValueError(
                "Production configuration is missing real values for: "
                + ", ".join(missing)
            )
        if self.cookie_domain == "localhost":
            raise ValueError("COOKIE_DOMAIN must be configured in production")
        if (
            urlsplit(self.api_url).scheme != "https"
            or not urlsplit(self.api_url).hostname
        ):
            raise ValueError("API_URL must be an absolute HTTPS URL in production")
        if not self.cors_origins_list or any(
            not origin.startswith("https://") for origin in self.cors_origins_list
        ):
            raise ValueError("CORS_ORIGINS must contain explicit HTTPS origins")
        return self

    @property
    def cors_origins_list(self) -> list[str]:
        """Parse CORS origins into a list."""
        return [
            origin.strip() for origin in self.cors_origins.split(",") if origin.strip()
        ]

    @property
    def admin_kick_user_ids_list(self) -> list[int]:
        """Parse admin user IDs into a list of integers."""
        if not self.admin_kick_user_ids.strip():
            return []
        return [
            int(uid.strip())
            for uid in self.admin_kick_user_ids.split(",")
            if uid.strip().isdigit()
        ]

    @property
    def encryption_keys_list(self) -> list[bytes]:
        """Parse encryption keys into a list of bytes."""
        return [key.strip().encode() for key in self.token_encryption_keys.split(",")]

    @property
    def is_production(self) -> bool:
        """Check if running in production."""
        return self.app_env == "prod"

    @property
    def is_dev(self) -> bool:
        """Check if running in development mode."""
        return self.app_env == "dev"


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
