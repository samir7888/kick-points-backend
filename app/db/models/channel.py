"""Channel management models."""

import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from ..base import BaseModel


class PausedReason(str, enum.Enum):
    """Reasons why a channel might be paused."""

    USER = "user"  # User manually paused
    PLAN_LIMIT = "plan_limit"  # Over plan limit
    TOKEN = "token"  # Token needs reauth

    # Backward-compatible aliases used across earlier revisions.
    user = USER
    plan_limit = PLAN_LIMIT
    token = TOKEN


class Channel(BaseModel):
    """User's monitored channels."""

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="User who owns this channel",
    )
    channel_slug: Mapped[str] = mapped_column(
        String(100), nullable=False, comment="Kick.com channel username/slug"
    )
    kick_channel_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        nullable=True,
        comment="Numeric Kick channel ID (resolved from slug)",
    )
    vote_amount: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Channel-specific vote amount (NULL = use default)",
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, comment="User-controlled enable/disable"
    )
    paused_reason: Mapped[Optional[PausedReason]] = mapped_column(
        Enum(PausedReason),
        nullable=True,
        comment="Reason channel is paused (NULL = not paused)",
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Channel removal timestamp (soft delete)",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Channel addition timestamp",
    )

    __table_args__ = (
        UniqueConstraint("user_id", "channel_slug", name="uq_user_channel_slug"),
    )

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="channels")
    votes: Mapped[list["Vote"]] = relationship(
        "Vote",
        back_populates="channel",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return f"<Channel(id={self.id}, user_id={self.user_id}, channel_slug='{self.channel_slug}', enabled={self.enabled})>"
