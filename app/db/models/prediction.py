"""Prediction and vote models."""

import enum
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from ..base import BaseModel


class PredictionState(str, enum.Enum):
    """Enum for prediction states."""

    OPEN = "open"
    LOCKED = "locked"
    RESOLVED = "resolved"
    CANCELLED = "cancelled"

    open = OPEN
    locked = LOCKED
    resolved = RESOLVED
    cancelled = CANCELLED


class VoteStatus(str, enum.Enum):
    """Enum for vote status."""

    PENDING = "pending"  # Vote queued but not yet placed
    PLACED = "placed"  # Successfully voted
    WON = "won"  # Prediction resolved, user won
    LOST = "lost"  # Prediction resolved, user lost
    REFUNDED = "refunded"  # Prediction cancelled, refunded
    SKIPPED = "skipped"  # Vote skipped (reason in 'reason' field)
    FAILED = "failed"  # Vote attempt failed

    pending = PENDING
    placed = PLACED
    won = WON
    lost = LOST
    refunded = REFUNDED
    skipped = SKIPPED
    failed = FAILED


class Prediction(BaseModel):
    """Predictions detected on monitored channels."""

    __table_args__ = (
        UniqueConstraint(
            "channel_slug", "kick_prediction_id", name="uq_channel_prediction"
        ),
        Index(
            "ix_channel_opened_id",
            "channel_slug",
            "opened_at",
            "id",
            postgresql_using="btree",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel_slug: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
        comment="Channel where prediction was found",
    )
    kick_prediction_id: Mapped[str] = mapped_column(
        String(50), nullable=False, comment="Kick.com prediction ID"
    )
    title: Mapped[str] = mapped_column(
        String(500), nullable=False, comment="Prediction title/question"
    )
    outcomes: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, comment="JSON array of prediction outcomes"
    )
    state: Mapped[PredictionState] = mapped_column(
        Enum(PredictionState),
        nullable=False,
        default=PredictionState.OPEN,
        comment="Current prediction state",
    )
    winning_outcome_id: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True, comment="ID of winning outcome (when resolved)"
    )
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="When prediction was opened",
    )
    closes_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When prediction voting closes (may be NULL)",
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When prediction was resolved/cancelled",
    )

    # Relationships
    votes: Mapped[list["Vote"]] = relationship(
        "Vote",
        back_populates="prediction",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return f"<Prediction(id={self.id}, channel_slug='{self.channel_slug}', title='{self.title[:50]}...', state={self.state})>"


class Vote(BaseModel):
    """User votes on predictions."""

    __table_args__ = (
        UniqueConstraint("user_id", "prediction_id", name="uq_user_prediction_vote"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="User who placed the vote",
    )
    channel_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("channel.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Channel where vote was placed",
    )
    prediction_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("prediction.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Prediction that was voted on",
    )
    outcome_id: Mapped[str] = mapped_column(
        String(50), nullable=False, comment="Kick.com outcome ID that was voted on"
    )
    amount_requested: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="Points user intended to vote"
    )
    amount_placed: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Points actually voted (may differ due to balance)",
    )
    status: Mapped[VoteStatus] = mapped_column(
        Enum(VoteStatus),
        nullable=False,
        default=VoteStatus.PENDING,
        comment="Current vote status",
    )
    reason: Mapped[Optional[str]] = mapped_column(
        String(200), nullable=True, comment="Reason for skipped/failed votes"
    )
    error: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="Error details for failed votes"
    )
    points_delta: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, comment="Net points gained/lost after resolution"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Vote creation timestamp",
    )
    settled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="When vote outcome was settled"
    )

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="votes")
    channel: Mapped["Channel"] = relationship("Channel", back_populates="votes")
    prediction: Mapped["Prediction"] = relationship(
        "Prediction", back_populates="votes"
    )

    def __repr__(self) -> str:
        return f"<Vote(id={self.id}, user_id={self.user_id}, prediction_id={self.prediction_id}, status={self.status})>"
