"""Integration tests for the cached channel read endpoints.

These exercise the new aggregate/detail endpoints against the real Postgres and
Redis instances the rest of the suite already requires, so serialization and
cache invalidation are covered end to end.
"""

import pytest
from sqlalchemy import delete

from app.api.v1.channels import channels_stats, get_channel, list_channels
from app.core.cache import cache_get, invalidate_user, user_key
from app.db.models import (
    Channel,
    Prediction,
    PredictionState,
    User,
    Vote,
    VoteStatus,
)
from app.services.predictions import get_user_channel_stats


@pytest.mark.asyncio
async def test_channel_stats_aggregate_detail_and_cache(db_session):
    user = User(kick_user_id=5_550_001, username="stats-tester")
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    channel = Channel(
        user_id=user.id, channel_slug="night-owl", kick_channel_id=42, enabled=True
    )
    db_session.add(channel)
    await db_session.commit()
    await db_session.refresh(channel)

    predictions = [
        Prediction(
            channel_slug="night-owl",
            kick_prediction_id=f"pred-{index}",
            title=f"Prediction {index}",
            outcomes=[{"id": "yes", "title": "Yes"}],
            state=PredictionState.OPEN,
        )
        for index in (1, 2)
    ]
    db_session.add_all(predictions)
    await db_session.commit()
    for prediction in predictions:
        await db_session.refresh(prediction)

    db_session.add_all(
        [
            Vote(
                user_id=user.id,
                channel_id=channel.id,
                prediction_id=predictions[0].id,
                outcome_id="yes",
                amount_requested=100,
                amount_placed=100,
                status=VoteStatus.WON,
                points_delta=250,
            ),
            Vote(
                user_id=user.id,
                channel_id=channel.id,
                prediction_id=predictions[1].id,
                outcome_id="no",
                amount_requested=100,
                amount_placed=100,
                status=VoteStatus.LOST,
                points_delta=-100,
            ),
        ]
    )
    await db_session.commit()

    # Start from a clean slate in case a prior run left keys behind.
    await invalidate_user(user.id)

    try:
        stats = await get_user_channel_stats(user.id, db_session)
        assert stats["totals"] == {
            "votes_placed": 2,
            "votes_won": 1,
            "votes_lost": 1,
            "win_rate": 50.0,
            "net_points": 150,
            "last_vote_at": stats["totals"]["last_vote_at"],
        }
        assert stats["totals"]["last_vote_at"] is not None
        assert len(stats["channels"]) == 1
        assert stats["channels"][0]["channel_id"] == channel.id
        assert stats["channels"][0]["slug"] == "night-owl"

        # The endpoint returns the same payload and caches it.
        payload = await channels_stats(user=user, db=db_session)
        assert payload == stats
        assert await cache_get(user_key(user.id, "channel-stats")) == stats

        # The single-channel endpoint serializes and caches a channel dict.
        detail = await get_channel(channel_id=channel.id, user=user, db=db_session)
        assert detail["slug"] == "night-owl"
        assert detail["live"] is False
        assert detail["active_prediction"]["id"] == predictions[1].id
        assert await cache_get(user_key(user.id, "channel", channel.id)) == detail

        # The list endpoint is cached too.
        assert await cache_get(user_key(user.id, "channels")) is None
        listed = await list_channels(user=user, db=db_session)
        assert [entry["slug"] for entry in listed] == ["night-owl"]
        assert await cache_get(user_key(user.id, "channels")) == listed

        # Invalidation drops every cached read model for the user.
        await invalidate_user(user.id)
        assert await cache_get(user_key(user.id, "channel-stats")) is None
        assert await cache_get(user_key(user.id, "channels")) is None
        assert await cache_get(user_key(user.id, "channel", channel.id)) is None
    finally:
        await db_session.execute(delete(Vote).where(Vote.user_id == user.id))
        await db_session.execute(
            delete(Prediction).where(
                Prediction.id.in_([prediction.id for prediction in predictions])
            )
        )
        await db_session.execute(delete(Channel).where(Channel.user_id == user.id))
        await db_session.execute(delete(User).where(User.id == user.id))
        await db_session.commit()
