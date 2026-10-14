"""Regression tests for deriving the winning outcome from Kick payloads.

Kick sometimes omits the top-level ``winning_outcome_id`` on resolved
predictions and only flags the winner on the outcome itself. These tests make
sure that flag is turned into a stored winner so the votes list can show the
"Winning outcome" and "Change" columns and settlement can run.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.db.models import Vote, VoteStatus
from app.integrations.kick.models import OutcomeInfo, PredictionEvent
from app.services.predictions import (
    _derive_winning_outcome_id,
    _vote_dict,
    upsert_prediction,
)


def _event(pred_id: str, outcomes: list[OutcomeInfo]) -> PredictionEvent:
    return PredictionEvent(
        kick_prediction_id=pred_id,
        channel_slug="test-channel",
        title="Will it happen?",
        outcomes=outcomes,
        state="RESOLVED",
        opened_at=datetime.now(tz=UTC),
    )


def test_derive_prefers_explicit_winning_outcome_id():
    outcomes = [{"id": "a", "is_winner": True}, {"id": "b"}]
    assert _derive_winning_outcome_id("explicit", outcomes) == "explicit"


def test_derive_falls_back_to_is_winner_flag():
    outcomes = [{"id": "a"}, {"id": "b", "is_winner": True}]
    assert _derive_winning_outcome_id(None, outcomes) == "b"


def test_derive_supports_legacy_winner_flag():
    outcomes = [{"id": "a"}, {"id": "b", "winner": True}]
    assert _derive_winning_outcome_id(None, outcomes) == "b"


def test_derive_returns_none_without_a_winner():
    assert _derive_winning_outcome_id(None, [{"id": "a"}]) is None
    assert _derive_winning_outcome_id(None, None) is None


async def test_upsert_stores_the_winner_flagged_on_the_outcome(db_session):
    event = _event(
        "pred-upsert-1",
        [
            OutcomeInfo(id="yes", title="Yes", is_winner=True),
            OutcomeInfo(id="no", title="No"),
        ],
    )
    pred = await upsert_prediction(event, db_session)

    assert pred.winning_outcome_id == "yes"

    vote = Vote(
        user_id=1,
        channel_id=1,
        prediction_id=pred.id,
        outcome_id="yes",
        amount_requested=100,
        amount_placed=100,
        status=VoteStatus.WON,
        created_at=datetime.now(tz=UTC),
    )
    payload = _vote_dict(vote, pred)
    assert payload["winning_outcome_id"] == "yes"
    assert payload["winning_outcome_title"] == "Yes"


async def test_vote_dict_derives_winner_when_column_is_stale(db_session):
    event = _event(
        "pred-upsert-2",
        [
            OutcomeInfo(id="a", title="A"),
            OutcomeInfo(id="b", title="B", is_winner=True),
        ],
    )
    pred = await upsert_prediction(event, db_session)
    # Simulate a row written before the fallback existed.
    pred.winning_outcome_id = None
    await db_session.flush()

    vote = Vote(
        user_id=1,
        channel_id=1,
        prediction_id=pred.id,
        outcome_id="b",
        amount_requested=100,
        amount_placed=100,
        status=VoteStatus.WON,
        created_at=datetime.now(tz=UTC),
    )
    payload = _vote_dict(vote, pred)
    assert payload["winning_outcome_id"] == "b"
    assert payload["winning_outcome_title"] == "B"
