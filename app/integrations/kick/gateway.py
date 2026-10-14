"""KickGateway Protocol — the single interface the app depends on for Kick."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from app.integrations.kick.models import ChannelInfo, PredictionEvent, VoteResult


@runtime_checkable
class KickGateway(Protocol):
    """Abstract interface for all Kick.com operations."""

    async def resolve_channel(self, slug: str, token: str) -> ChannelInfo:
        """Fetch basic channel info (id, chatroom_id) by slug."""
        ...

    async def get_active_prediction(
        self, slug: str, token: str
    ) -> PredictionEvent | None:
        """Return the currently open prediction or None."""
        ...

    async def get_latest_prediction(
        self, slug: str, token: str
    ) -> PredictionEvent | None:
        """Return the latest prediction in any state (used by the poll fallback)."""
        ...

    async def get_points_balance(self, slug: str, token: str) -> int | None:
        """Return the authenticated user's channel-point balance or None."""
        ...

    async def place_vote(
        self,
        slug: str,
        prediction_id: str,
        outcome_id: str,
        amount: int,
        token: str,
    ) -> VoteResult:
        """Place a vote. Must NEVER be blindly retried if it may have succeeded."""
        ...

    def listen(self, slug: str) -> AsyncIterator[PredictionEvent]:
        """Realtime stream (Pusher WebSocket) with REST poll fallback."""
        ...


def get_kick_gateway() -> KickGateway:
    """Create the configured gateway implementation.

    ``KICK_GATEWAY`` selects the implementation:
      - ``fake``: deterministic stub (tests / offline dev)
      - ``real``: legacy Kick.com client + Pusher websocket listener
      - ``auto``: fake in dev, real otherwise (default)
    """
    from app.core.config import get_settings

    settings = get_settings()
    choice = settings.kick_gateway
    if choice == "fake" or (choice == "auto" and settings.is_dev):
        from app.integrations.kick.fake import FakeKickGateway

        return FakeKickGateway()
    from app.integrations.kick.legacy_adapter import LegacyKickAdapter

    return LegacyKickAdapter()
