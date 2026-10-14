"""Test database schema and basic model functionality."""

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.db.models import Channel, Prediction, User, Vote


class TestDatabaseSchema:
    """Test basic database functionality without complex async fixtures."""

    async def test_user_creation_and_constraints(self, db_session):
        """Test user creation and unique constraints."""
        # Create a user
        user = User(kick_user_id=12345, username="testuser")
        db_session.add(user)
        await db_session.commit()

        assert user.id is not None
        assert user.kick_user_id == 12345

        # Test unique constraint
        duplicate_user = User(kick_user_id=12345, username="duplicate")
        db_session.add(duplicate_user)

        with pytest.raises(IntegrityError):
            await db_session.commit()

    async def test_channel_relationships(self, db_session):
        """Test channel creation and user relationships."""
        # Create user first
        user = User(kick_user_id=54321, username="channelowner")
        db_session.add(user)
        await db_session.flush()  # Get the user ID

        # Create channel
        channel = Channel(user_id=user.id, channel_slug="testchannel", vote_amount=100)
        db_session.add(channel)
        await db_session.commit()

        assert channel.id is not None
        assert channel.user_id == user.id
        assert channel.channel_slug == "testchannel"
        assert channel.vote_amount == 100
        assert channel.enabled is True


async def test_config_loading():
    """Test that configuration loads correctly."""
    settings = get_settings()

    # Verify required settings exist
    assert settings.database_url is not None
    assert settings.redis_url is not None
    assert settings.secret_key is not None

    # Verify default values
    assert settings.free_channel_limit == 1
    assert settings.premium_channel_limit == 6
    assert settings.min_vote_amount == 10


async def test_enum_values():
    """Test that enum values work correctly."""
    from app.db.models import PausedReason, PredictionState, TokenHealth, VoteStatus

    # Test enum creation
    assert PausedReason.USER == "user"
    assert PausedReason.PLAN_LIMIT == "plan_limit"
    assert PausedReason.TOKEN == "token"

    assert PredictionState.OPEN == "open"
    assert PredictionState.RESOLVED == "resolved"

    assert TokenHealth.OK == "ok"
    assert TokenHealth.NEEDS_REAUTH == "needs_reauth"

    assert VoteStatus.PENDING == "pending"
    assert VoteStatus.PLACED == "placed"
    assert VoteStatus.WON == "won"
