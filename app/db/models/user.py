"""User model and related tables."""

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from ..base import BaseModel


class User(BaseModel):
    """User account linked to Kick.com via OAuth."""

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kick_user_id: Mapped[int] = mapped_column(
        BigInteger,
        unique=True,
        nullable=False,
        index=True,
        comment="Kick.com user ID (unique identifier)",
    )
    username: Mapped[str] = mapped_column(
        String(100), nullable=False, comment="Current Kick username (can change)"
    )
    email: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, comment="User email from OAuth (may be None)"
    )
    premium_until: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Premium subscription expiry (None = free tier)",
    )
    premium_grace_until: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="End of temporary premium access after a failed renewal",
    )
    is_admin: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="Admin privileges for kill switch, etc.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Account creation timestamp",
    )
    last_login_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="Last successful login"
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Soft deletion timestamp (NULL = active)",
    )

    # Relationships
    sessions: Mapped[list["Session"]] = relationship(
        "Session",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    kick_token: Mapped[Optional["KickToken"]] = relationship(
        "KickToken",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    user_settings: Mapped[Optional["UserSettings"]] = relationship(
        "UserSettings",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    channels: Mapped[list["Channel"]] = relationship(
        "Channel",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    votes: Mapped[list["Vote"]] = relationship(
        "Vote",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    audit_logs: Mapped[list["AuditLog"]] = relationship(
        "AuditLog",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.sessions = []
        self.kick_token = None
        self.user_settings = None
        self.channels = []
        self.votes = []
        self.subscriptions = []
        self.audit_logs = []

    def __repr__(self) -> str:
        return f"<User(id={self.id}, kick_user_id={self.kick_user_id}, username='{self.username}')>"
