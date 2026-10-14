"""FakeKickGateway — deterministic implementation for tests and local dev."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

from app.integrations.kick.models import (
    ChannelInfo,
    OutcomeInfo,
    PredictionEvent,
    VoteResult,
)


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


def _make_prediction(
    channel_slug: str, pred_id: str = "fake-pred-1"
) -> PredictionEvent:
    return PredictionEvent(
        kick_prediction_id=pred_id,
        channel_slug=channel_slug,
        title="Will the streamer win the next match?",
        outcomes=[
            OutcomeInfo(id="outcome-yes", title="Yes", return_rate=1.5),
            OutcomeInfo(id="outcome-no", title="No", return_rate=2.0),
        ],
        state="ACTIVE",
        opened_at=_utcnow(),
        closes_at=_utcnow() + timedelta(minutes=5),
    )


class FakeKickGateway:
    """Scripted gateway for tests. Behaviour is controlled via attributes."""

    def __init__(self) -> None:
        # Set to True to simulate a channel resolution failure
        self.fail_resolve: bool = False
        # Set to True to simulate a vote placement failure
        self.fail_vote: bool = False
        # Control whether an active prediction exists
        self.has_active_prediction: bool = True
        # Winning outcome for settle tests
        self.winning_outcome_id: str = "outcome-yes"
        # Simulate 401 from Kick
        self.simulate_auth_failure: bool = False
        # Points balance to return
        self.points_balance: int | None = 500
        # Track placed votes: list of (slug, prediction_id, outcome_id, amount)
        self.placed_votes: list[tuple[str, str, str, int]] = []
        # Scripted prediction events to emit from listen()
        self.scripted_events: list[PredictionEvent] = []
        # Latest prediction returned by get_latest_prediction (falls back to the
        # active prediction when unset)
        self.latest_prediction: PredictionEvent | None = None

    async def resolve_channel(self, slug: str, token: str) -> ChannelInfo:
        if self.fail_resolve:
            raise Exception("Simulated resolve failure")
        return ChannelInfo(
            slug=slug,
            kick_channel_id=12345,
            chatroom_id=99999,
        )

    async def get_active_prediction(
        self, slug: str, token: str
    ) -> PredictionEvent | None:
        if self.simulate_auth_failure:
            raise Exception("401 Unauthorized")
        if not self.has_active_prediction:
            return None
        return _make_prediction(slug)

    async def get_latest_prediction(
        self, slug: str, token: str
    ) -> PredictionEvent | None:
        if self.simulate_auth_failure:
            raise Exception("401 Unauthorized")
        if self.latest_prediction is not None:
            return self.latest_prediction
        if not self.has_active_prediction:
            return None
        return _make_prediction(slug)

    async def get_points_balance(self, slug: str, token: str) -> int | None:
        return self.points_balance

    async def place_vote(
        self,
        slug: str,
        prediction_id: str,
        outcome_id: str,
        amount: int,
        token: str,
    ) -> VoteResult:
        if self.simulate_auth_failure:
            return VoteResult(success=False, error="401 Unauthorized")
        if self.fail_vote:
            return VoteResult(success=False, error="Simulated vote failure")
        self.placed_votes.append((slug, prediction_id, outcome_id, amount))
        return VoteResult(success=True, amount_placed=amount)

    async def _gen(self, slug: str) -> AsyncGenerator[PredictionEvent, None]:
        for event in self.scripted_events:
            yield event
            await asyncio.sleep(0)

    def listen(self, slug: str) -> AsyncGenerator[PredictionEvent, None]:
        return self._gen(slug)
