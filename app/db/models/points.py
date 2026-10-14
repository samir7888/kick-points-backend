"""Points-balance snapshots — powers the growth analytics dashboard."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from ..base import BaseModel


class PointsSnapshot(BaseModel):
    """A point-in-time reading of a user's channel-points balance.

    One append-only row per (user, channel) per snapshot run. The growth
    dashboard derives per-day balances, deltas, and totals from these rows;
    nothing here is ever mutated, so history is a faithful series.
    """

    __table_args__ = (
        Index(
            "ix_points_snapshot_user_channel_time",
            "user_id",
            "channel_id",
            "recorded_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Owner of the balance",
    )
    channel_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("channel.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Channel the balance belongs to",
    )
    channel_slug: Mapped[str] = mapped_column(
        String(100), nullable=False, comment="Denormalized channel slug"
    )
    balance: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="Channel-points balance at recorded_at"
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
        comment="When the balance was observed",
    )

    def __repr__(self) -> str:
        return (
            f"<PointsSnapshot(id={self.id}, user_id={self.user_id}, "
            f"channel_id={self.channel_id}, balance={self.balance})>"
        )
