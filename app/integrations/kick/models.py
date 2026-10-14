"""Normalized domain models for Kick prediction events."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.core.errors import KickSchemaError


class OutcomeInfo(BaseModel):
    id: str
    title: str
    total_voters: int = 0
    total_points: int = 0
    return_rate: float = 1.0
    is_winner: bool = False


class PredictionEvent(BaseModel):
    """Normalized prediction from Kick API or Pusher."""

    kick_prediction_id: str
    channel_slug: str
    title: str
    outcomes: list[OutcomeInfo]
    state: str  # ACTIVE, LOCKED, RESOLVED, CANCELLED
    winning_outcome_id: str | None = None
    opened_at: datetime
    closes_at: datetime | None = None
    resolved_at: datetime | None = None
    raw: dict[str, Any] = Field(default_factory=dict, exclude=True)


class VoteResult(BaseModel):
    success: bool
    amount_placed: int | None = None
    error: str | None = None


class ChannelInfo(BaseModel):
    slug: str
    kick_channel_id: int
    chatroom_id: int | None = None
