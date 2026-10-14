"""OAuth-related models for state management and token storage."""

import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from ..base import BaseModel


class OAuthState(BaseModel):
    """OAuth state parameter storage for PKCE flow."""

    state: Mapped[str] = mapped_column(
        String(64), primary_key=True, comment="OAuth state parameter (single use)"
    )
    code_verifier: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="PKCE code verifier"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="State creation timestamp",
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
        comment="State expiry (short TTL)",
    )

    def __repr__(self) -> str:
        return f"<OAuthState(state={self.state[:8]}..., expires_at={self.expires_at})>"


class TokenHealth(str, enum.Enum):
    """Enum for Kick token health status."""

    OK = "ok"
    NEEDS_REAUTH = "needs_reauth"

    ok = OK
    needs_reauth = NEEDS_REAUTH


class KickToken(BaseModel):
    """Encrypted Kick.com OAuth tokens."""

    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("user.id", ondelete="CASCADE"),
        primary_key=True,
        comment="User who owns these tokens",
    )
    access_token_enc: Mapped[str] = mapped_column(
        Text, nullable=False, comment="Encrypted access token"
    )
    refresh_token_enc: Mapped[str] = mapped_column(
        Text, nullable=False, comment="Encrypted refresh token"
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, comment="Access token expiry"
    )
    scopes: Mapped[str] = mapped_column(
        String(500), nullable=False, default="", comment="Space-separated OAuth scopes"
    )
    session_token_enc: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Encrypted kick.com site session token used for voting",
    )
    health: Mapped[TokenHealth] = mapped_column(
        Enum(TokenHealth),
        nullable=False,
        default=TokenHealth.OK,
        comment="Token health status",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        comment="Last token update timestamp",
    )

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="kick_token")

    def __repr__(self) -> str:
        return f"<KickToken(user_id={self.user_id}, health={self.health}, expires_at={self.expires_at})>"
