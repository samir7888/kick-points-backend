"""Billing and subscription models."""

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from ..base import BaseModel


class Subscription(BaseModel):
    """User subscriptions via Dodo Payments."""

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="User who owns this subscription",
    )
    dodo_subscription_id: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        nullable=False,
        comment="Dodo Payments subscription ID",
    )
    dodo_customer_id: Mapped[str] = mapped_column(
        String(100), nullable=False, comment="Dodo Payments customer ID"
    )
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, comment="Subscription status from Dodo"
    )
    current_period_end: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="Current billing period end"
    )
    cancel_at_period_end: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="Whether subscription cancels at period end",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        comment="Last update from webhook",
    )

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="subscriptions")

    def __repr__(self) -> str:
        return f"<Subscription(id={self.id}, user_id={self.user_id}, dodo_subscription_id='{self.dodo_subscription_id}', status='{self.status}')>"
