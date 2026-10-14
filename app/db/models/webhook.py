"""Webhook event tracking models."""

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import DateTime, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from ..base import BaseModel


class WebhookEvent(BaseModel):
    """Webhook events from external providers."""

    id: Mapped[str] = mapped_column(
        String(100),
        primary_key=True,
        comment="Provider webhook event ID (ensures idempotency)",
    )
    type: Mapped[str] = mapped_column(
        String(100), nullable=False, comment="Event type from provider"
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, comment="Raw webhook payload"
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="When webhook was received",
    )
    processed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When webhook was successfully processed",
    )
    error: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="Processing error details (if any)"
    )

    def __repr__(self) -> str:
        return f"<WebhookEvent(id='{self.id}', type='{self.type}', processed_at={self.processed_at})>"
