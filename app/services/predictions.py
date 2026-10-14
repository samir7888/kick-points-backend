"""Predictions service — upsert, pagination, stats."""

from __future__ import annotations

from typing import Any

from sqlalchemy import desc, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import KickSchemaError
from app.core.logging import get_logger
from app.db.models import Channel, Prediction, PredictionState, Vote
from app.integrations.kick.models import PredictionEvent

log = get_logger(__name__)


def _derive_winning_outcome_id(
    winning_outcome_id: str | None, outcomes: list[Any] | None
) -> str | None:
    """Return the stored winner, falling back to the outcome flagged as winner.

    Kick omits the top-level ``winning_outcome_id`` on some resolved
    predictions and only marks the winning outcome with ``is_winner`` (or the
    legacy ``winner`` flag). Without this fallback the winner is lost, so the
    votes list shows a blank "Winning outcome" and the settlement task never
    marks votes as won/lost.
    """
    if winning_outcome_id:
        return winning_outcome_id
    for outcome in outcomes or []:
        if not isinstance(outcome, dict):
            continue
        if outcome.get("is_winner") or outcome.get("winner"):
            outcome_id = outcome.get("id")
            if outcome_id not in (None, ""):
                return str(outcome_id)
    return None


async def upsert_prediction(event: PredictionEvent, db: AsyncSession) -> Prediction:
    """Insert or update a prediction row from a gateway event."""
    result = await db.execute(
        select(Prediction).where(
            Prediction.channel_slug == event.channel_slug,
            Prediction.kick_prediction_id == event.kick_prediction_id,
        )
    )
    pred = result.scalar_one_or_none()

    state_map = {
        "ACTIVE": PredictionState.open,
        "OPEN": PredictionState.open,
        "LOCKED": PredictionState.locked,
        "RESOLVED": PredictionState.resolved,
        "CANCELLED": PredictionState.cancelled,
    }
    state = state_map.get(event.state.upper())
    if state is None:
        raise KickSchemaError("Kick prediction has an unsupported state")

    outcomes_data = [o.model_dump() for o in event.outcomes]
    winning_outcome_id = _derive_winning_outcome_id(
        event.winning_outcome_id, outcomes_data
    )

    if pred is None:
        pred = Prediction(
            channel_slug=event.channel_slug,
            kick_prediction_id=event.kick_prediction_id,
            title=event.title,
            outcomes=outcomes_data,
            state=state,
            winning_outcome_id=winning_outcome_id,
            opened_at=event.opened_at,
            closes_at=event.closes_at,
            resolved_at=event.resolved_at,
        )
        db.add(pred)
    else:
        pred.state = state
        pred.outcomes = outcomes_data
        pred.winning_outcome_id = winning_outcome_id
        if event.closes_at:
            pred.closes_at = event.closes_at
        if event.resolved_at:
            pred.resolved_at = event.resolved_at

    await db.flush()
    await db.refresh(pred)
    return pred


async def get_channel_predictions(
    channel_id: int,
    user_id: int,
    db: AsyncSession,
    page: int = 1,
    limit: int = 20,
    status: str | None = None,
    voted: bool | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """One page of channel predictions with the caller's vote attached.

    Returns the page's items plus the total number of matching predictions so
    callers can render page controls.
    """
    result = await db.execute(
        select(Channel).where(
            Channel.id == channel_id,
            Channel.user_id == user_id,
            Channel.deleted_at.is_(None),
        )
    )
    ch = result.scalar_one_or_none()
    if not ch:
        from app.core.errors import NotFoundError

        raise NotFoundError("Channel not found")

    filters = [Prediction.channel_slug == ch.channel_slug]

    if status:
        try:
            filters.append(Prediction.state == PredictionState(status))
        except ValueError as exc:
            from app.core.errors import ValidationError

            raise ValidationError("Invalid prediction status") from exc

    vote_exists = exists(
        select(Vote.id).where(
            Vote.prediction_id == Prediction.id,
            Vote.user_id == user_id,
        )
    )
    if voted is True:
        filters.append(vote_exists)
    elif voted is False:
        filters.append(~vote_exists)

    total_result = await db.execute(
        select(func.count()).select_from(Prediction).where(*filters)
    )
    total = int(total_result.scalar_one())

    pred_result = await db.execute(
        select(Prediction)
        .where(*filters)
        .order_by(desc(Prediction.opened_at), desc(Prediction.id))
        .offset((page - 1) * limit)
        .limit(limit)
    )
    preds = list(pred_result.scalars().all())

    # Fetch caller's votes for this page only.
    pred_ids = [p.id for p in preds]
    vote_result = await db.execute(
        select(Vote).where(Vote.user_id == user_id, Vote.prediction_id.in_(pred_ids))
    )
    votes_by_pred = {v.prediction_id: v for v in vote_result.scalars().all()}

    items = []
    for p in preds:
        vote = votes_by_pred.get(p.id)
        items.append(
            {
                "id": p.id,
                "kick_prediction_id": p.kick_prediction_id,
                "title": p.title,
                "outcomes": p.outcomes,
                "state": p.state.value,
                "winning_outcome_id": p.winning_outcome_id,
                "opened_at": p.opened_at.isoformat(),
                "closes_at": p.closes_at.isoformat() if p.closes_at else None,
                "resolved_at": p.resolved_at.isoformat() if p.resolved_at else None,
                "my_vote": _vote_dict(vote, p) if vote else None,
            }
        )

    return items, total


def _vote_dict(vote: Vote, prediction: Prediction | None = None) -> dict[str, Any]:
    """Serialize a vote, enriched with its prediction's title and outcome names.

    Kick only stores opaque outcome ULIDs (e.g. ``01M4QG9TFX7D5NPX141ZJZZKCY``);
    the human-readable names live on the prediction's ``outcomes`` JSON.
    """
    outcomes_by_id: dict[str, dict[str, Any]] = {}
    if prediction and prediction.outcomes:
        outcomes_by_id = {
            str(o.get("id")): o
            for o in prediction.outcomes
            if isinstance(o, dict)
        }
    winning_id = (
        _derive_winning_outcome_id(prediction.winning_outcome_id, prediction.outcomes)
        if prediction
        else None
    )
    return {
        "id": vote.id,
        "prediction_id": vote.prediction_id,
        "channel_id": vote.channel_id,
        "channel_slug": prediction.channel_slug if prediction else None,
        "prediction_title": prediction.title if prediction else None,
        "prediction_state": prediction.state.value if prediction else None,
        "outcome_id": vote.outcome_id,
        "outcome_title": (outcomes_by_id.get(str(vote.outcome_id)) or {}).get("title"),
        "winning_outcome_id": winning_id,
        "winning_outcome_title": (
            (outcomes_by_id.get(str(winning_id)) or {}).get("title")
            if winning_id
            else None
        ),
        "amount_requested": vote.amount_requested,
        "amount_placed": vote.amount_placed,
        "status": vote.status.value,
        "reason": vote.reason,
        "points_delta": vote.points_delta,
        "created_at": vote.created_at.isoformat(),
        "settled_at": vote.settled_at.isoformat() if vote.settled_at else None,
    }


def _stats_for_votes(votes: list[Vote]) -> dict[str, Any]:
    """Summarize a set of votes into the shape the stats endpoints return."""
    placed = [
        v for v in votes if v.status.value in ("placed", "won", "lost", "refunded")
    ]
    won = [v for v in votes if v.status.value == "won"]
    lost = [v for v in votes if v.status.value == "lost"]
    net_points = sum((v.points_delta or 0) for v in votes)
    win_rate = round(len(won) / max(1, len(won) + len(lost)) * 100, 1)
    last_vote = max((v.created_at for v in placed), default=None)

    return {
        "votes_placed": len(placed),
        "votes_won": len(won),
        "votes_lost": len(lost),
        "win_rate": win_rate,
        "net_points": net_points,
        "last_vote_at": last_vote.isoformat() if last_vote else None,
    }


async def get_channel_stats(
    channel_id: int, user_id: int, db: AsyncSession
) -> dict[str, Any]:
    result = await db.execute(
        select(Channel).where(
            Channel.id == channel_id,
            Channel.user_id == user_id,
            Channel.deleted_at.is_(None),
        )
    )
    ch = result.scalar_one_or_none()
    if not ch:
        from app.core.errors import NotFoundError

        raise NotFoundError("Channel not found")

    vote_res = await db.execute(
        select(Vote).where(Vote.channel_id == channel_id, Vote.user_id == user_id)
    )
    return _stats_for_votes(list(vote_res.scalars().all()))


async def get_user_channel_stats(user_id: int, db: AsyncSession) -> dict[str, Any]:
    """Aggregate vote stats for every channel a user owns in two queries.

    Powers the dashboard summary without fanning out one request per channel.
    """
    channel_res = await db.execute(
        select(Channel)
        .where(Channel.user_id == user_id)
        .where(Channel.deleted_at.is_(None))
        .order_by(Channel.created_at, Channel.id)
    )
    channels = list(channel_res.scalars().all())

    vote_res = await db.execute(select(Vote).where(Vote.user_id == user_id))
    votes = list(vote_res.scalars().all())

    by_channel: dict[int, list[Vote]] = {}
    for vote in votes:
        by_channel.setdefault(vote.channel_id, []).append(vote)

    return {
        # Totals intentionally include removed channels' history.
        "totals": _stats_for_votes(votes),
        "channels": [
            {
                "channel_id": channel.id,
                "slug": channel.channel_slug,
                **_stats_for_votes(by_channel.get(channel.id, [])),
            }
            for channel in channels
        ],
    }
