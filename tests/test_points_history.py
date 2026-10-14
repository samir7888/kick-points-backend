"""Tests for the points-growth analytics dashboard.

Covers the snapshot aggregation service, the ``/points/history`` read model and
its cache, and the periodic snapshot worker task end to end.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import delete, select

from app.api.v1.points import points_history
from app.core.cache import cache_get, invalidate_user, user_key
from app.core.security import encrypt_token, utcnow
from app.db.models import (
    Channel,
    KickToken,
    PointsSnapshot,
    User,
)
from app.integrations.kick.fake import FakeKickGateway
from app.services.points import _bucket_start, get_points_history
from app.workers.tasks import points_snapshot_task


async def _make_user(db_session, kick_user_id: int) -> User:
    user = User(kick_user_id=kick_user_id, username=f"user-{kick_user_id}")
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


async def _make_channel(db_session, user: User, slug: str) -> Channel:
    channel = Channel(user_id=user.id, channel_slug=slug, enabled=True)
    db_session.add(channel)
    await db_session.commit()
    await db_session.refresh(channel)
    return channel


@pytest.mark.asyncio
async def test_points_history_aggregates_channels_and_totals(db_session):
    now = utcnow()
    user = await _make_user(db_session, 6_660_001)
    channel_a = await _make_channel(db_session, user, "alpha")
    channel_b = await _make_channel(db_session, user, "bravo")

    def day(offset: int):
        return now - timedelta(days=offset)

    db_session.add_all(
        [
            PointsSnapshot(
                user_id=user.id,
                channel_id=channel_a.id,
                channel_slug="alpha",
                balance=100,
                recorded_at=day(3),
            ),
            PointsSnapshot(
                user_id=user.id,
                channel_id=channel_a.id,
                channel_slug="alpha",
                balance=150,
                recorded_at=day(2),
            ),
            PointsSnapshot(
                user_id=user.id,
                channel_id=channel_b.id,
                channel_slug="bravo",
                balance=50,
                recorded_at=day(2),
            ),
            PointsSnapshot(
                user_id=user.id,
                channel_id=channel_b.id,
                channel_slug="bravo",
                balance=80,
                recorded_at=day(1),
            ),
        ]
    )
    await db_session.commit()

    history = await get_points_history(db_session, user.id, range_key="30d")

    assert history["range"] == "30d"
    assert history["bucket"] == "1d"
    assert history["totals"] == {
        "current": 230,
        "start": 150,
        "growth": 80,
        "growth_pct": pytest.approx(53.3),
        "snapshots": 4,
    }
    assert [point["total"] for point in history["series"]] == [100, 200, 230]
    # Daily buckets land on midnight so the frontend can format the UTC date.
    assert all("T00:00:00" in point["at"] for point in history["series"])

    by_id = {c["channel_id"]: c for c in history["channels"]}
    alpha = by_id[channel_a.id]
    assert (alpha["slug"], alpha["start"], alpha["current"], alpha["growth"]) == (
        "alpha",
        100,
        150,
        50,
    )
    assert [point["balance"] for point in alpha["series"]] == [100, 150]
    bravo = by_id[channel_b.id]
    assert (bravo["slug"], bravo["start"], bravo["current"], bravo["growth"]) == (
        "bravo",
        50,
        80,
        30,
    )


@pytest.mark.asyncio
async def test_points_history_sub_day_ranges_bucket_by_time(db_session):
    now = utcnow()
    user = await _make_user(db_session, 6_660_002)
    channel = await _make_channel(db_session, user, "intraday")

    db_session.add_all(
        [
            PointsSnapshot(
                user_id=user.id,
                channel_id=channel.id,
                channel_slug="intraday",
                balance=10,
                recorded_at=now - timedelta(minutes=50),
            ),
            PointsSnapshot(
                user_id=user.id,
                channel_id=channel.id,
                channel_slug="intraday",
                balance=25,
                recorded_at=now - timedelta(minutes=20),
            ),
            # Outside the last hour — must be excluded from the 1h window.
            PointsSnapshot(
                user_id=user.id,
                channel_id=channel.id,
                channel_slug="intraday",
                balance=99,
                recorded_at=now - timedelta(hours=3),
            ),
        ]
    )
    await db_session.commit()

    hour = await get_points_history(db_session, user.id, range_key="1h")
    assert hour["bucket"] == "5m"
    assert hour["totals"]["start"] == 10
    assert hour["totals"]["current"] == 25
    assert hour["totals"]["snapshots"] == 2
    # Two readings, ~30 minutes apart, always draw a two-point line.
    assert [point["total"] for point in hour["series"]] == [10, 25]

    day = await get_points_history(db_session, user.id, range_key="24h")
    assert day["bucket"] == "1h"
    assert day["totals"]["snapshots"] == 3


def test_bucket_start_floors_to_the_bucket():
    moment = utcnow().replace(minute=37, second=21, microsecond=0)
    assert _bucket_start(moment, timedelta(minutes=5)).minute == 35
    assert _bucket_start(moment, timedelta(hours=1)).minute == 0
    day = _bucket_start(moment, timedelta(days=1))
    assert (day.hour, day.minute, day.second) == (0, 0, 0)


@pytest.mark.asyncio
async def test_points_history_respects_window_and_channel_filter(db_session):
    now = utcnow()
    user = await _make_user(db_session, 6_660_003)
    other_user = await _make_user(db_session, 6_660_004)
    channel = await _make_channel(db_session, user, "window")

    db_session.add_all(
        [
            PointsSnapshot(
                user_id=user.id,
                channel_id=channel.id,
                channel_slug="window",
                balance=10,
                recorded_at=now - timedelta(days=10),
            ),
            PointsSnapshot(
                user_id=user.id,
                channel_id=channel.id,
                channel_slug="window",
                balance=40,
                recorded_at=now - timedelta(days=1),
            ),
        ]
    )
    await db_session.commit()

    # A 7-day window drops the older reading; the channel filter isolates one row.
    recent = await get_points_history(
        db_session, user.id, range_key="7d", channel_id=channel.id
    )
    assert recent["totals"]["start"] == 40
    assert recent["totals"]["current"] == 40
    assert len(recent["channels"]) == 1

    # The other user's data never leaks in.
    other_history = await get_points_history(db_session, other_user.id, range_key="30d")
    assert other_history["totals"]["snapshots"] == 0
    assert other_history["channels"] == []


@pytest.mark.asyncio
async def test_points_history_endpoint_serializes_and_caches(db_session):
    now = utcnow()
    user = await _make_user(db_session, 6_660_005)
    channel = await _make_channel(db_session, user, "cached")

    db_session.add(
        PointsSnapshot(
            user_id=user.id,
            channel_id=channel.id,
            channel_slug="cached",
            balance=777,
            recorded_at=now - timedelta(minutes=30),
        )
    )
    await db_session.commit()

    await invalidate_user(user.id)
    try:
        payload = await points_history(
            range="24h", channel_id=None, user=user, db=db_session
        )
        assert payload["range"] == "24h"
        assert payload["totals"]["current"] == 777
        assert payload["channels"][0]["slug"] == "cached"

        cached = await cache_get(user_key(user.id, "points-history", "24h", "all"))
        assert cached == payload
    finally:
        await invalidate_user(user.id)


@pytest.mark.asyncio
async def test_points_snapshot_task_records_balances(db_session):
    user = await _make_user(db_session, 6_660_006)
    channel = await _make_channel(db_session, user, "worker")

    db_session.add(
        KickToken(
            user_id=user.id,
            access_token_enc=encrypt_token("oauth-token"),
            refresh_token_enc=encrypt_token("oauth-refresh"),
            expires_at=utcnow() + timedelta(hours=1),
            session_token_enc=encrypt_token("site-session-token"),
        )
    )
    await db_session.commit()

    gateway = FakeKickGateway()
    gateway.points_balance = 1234

    await points_snapshot_task({"gateway": gateway})

    result = await db_session.execute(
        select(PointsSnapshot).where(PointsSnapshot.user_id == user.id)
    )
    rows = list(result.scalars().all())
    assert len(rows) == 1
    assert rows[0].channel_id == channel.id
    assert rows[0].channel_slug == "worker"
    assert rows[0].balance == 1234

    # Clean up the worker-written row (the fixture does not know about it).
    await db_session.execute(
        delete(PointsSnapshot).where(PointsSnapshot.user_id == user.id)
    )
    await db_session.commit()
