"""User settings model."""

from sqlalchemy import Boolean, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import BaseModel


class UserSettings(BaseModel):
    """Per-user settings for prediction voting."""

    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("user.id", ondelete="CASCADE"),
        primary_key=True,
        comment="User who owns these settings",
    )
    default_vote_amount: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Default points to vote per prediction (>= MIN_VOTE_AMOUNT)",
    )
    fallback_to_min: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        comment="If true, vote MIN_VOTE_AMOUNT when balance insufficient",
    )
    paused: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="If true, user has globally paused all voting",
    )

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="user_settings")

    def __repr__(self) -> str:
        return f"<UserSettings(user_id={self.user_id}, default_vote_amount={self.default_vote_amount}, paused={self.paused})>"
