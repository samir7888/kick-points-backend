"""Test database models and constraints."""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.models import (
    Channel,
    KickToken,
    OAuthState,
    PausedReason,
    Prediction,
    PredictionState,
    Session,
    TokenHealth,
    User,
    UserSettings,
    Vote,
    VoteStatus,
)


class TestUserModel:
    """Test User model and constraints."""

    async def test_create_user(self, db_session):
        """Test creating a user with required fields."""
        user = User(kick_user_id=12345, username="testuser", email="test@example.com")

        db_session.add(user)
        await db_session.flush()

        assert user.id is not None
        assert user.kick_user_id == 12345
        assert user.username == "testuser"
        assert user.email == "test@example.com"
        assert user.is_admin is False
        assert user.premium_until is None
        assert user.created_at is not None

    async def test_kick_user_id_unique_constraint(self, db_session):
        """Test that kick_user_id must be unique."""
        # Create first user
        user1 = User(kick_user_id=12345, username="user1")
        db_session.add(user1)
        await db_session.flush()

        # Try to create second user with same kick_user_id
        user2 = User(kick_user_id=12345, username="user2")
        db_session.add(user2)

        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_user_relationships(self, db_session):
        """Test user relationships are properly configured."""
        user = User(kick_user_id=12345, username="testuser")
        db_session.add(user)
        await db_session.flush()

        # Test that relationships exist but are empty initially
        assert user.sessions == []
        assert user.channels == []
        assert user.votes == []
        assert user.kick_token is None


class TestChannelModel:
    """Test Channel model and constraints."""

    async def test_create_channel(self, db_session):
        """Test creating a channel."""
        user = User(kick_user_id=12345, username="testuser")
        db_session.add(user)
        await db_session.flush()

        channel = Channel(
            user_id=user.id,
            channel_slug="testchannel",
            kick_channel_id=67890,
            vote_amount=50,
        )

        db_session.add(channel)
        await db_session.flush()

        assert channel.id is not None
        assert channel.user_id == user.id
        assert channel.channel_slug == "testchannel"
        assert channel.kick_channel_id == 67890
        assert channel.vote_amount == 50
        assert channel.enabled is True
        assert channel.paused_reason is None

    async def test_user_channel_slug_unique_constraint(self, db_session):
        """Test that user cannot have duplicate channel slugs."""
        user = User(kick_user_id=12345, username="testuser")
        db_session.add(user)
        await db_session.flush()

        # Create first channel
        channel1 = Channel(user_id=user.id, channel_slug="testchannel")
        db_session.add(channel1)
        await db_session.flush()

        # Try to create second channel with same slug for same user
        channel2 = Channel(user_id=user.id, channel_slug="testchannel")
        db_session.add(channel2)

        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_different_users_same_channel_slug(self, db_session):
        """Test that different users can have same channel slug."""
        user1 = User(kick_user_id=12345, username="user1")
        user2 = User(kick_user_id=67890, username="user2")
        db_session.add_all([user1, user2])
        await db_session.flush()

        # Both users can add the same channel
        channel1 = Channel(user_id=user1.id, channel_slug="samechannel")
        channel2 = Channel(user_id=user2.id, channel_slug="samechannel")
        db_session.add_all([channel1, channel2])
        await db_session.flush()  # Should not raise error

        assert channel1.id != channel2.id

    async def test_paused_reason_enum(self, db_session):
        """Test paused reason enum values."""
        user = User(kick_user_id=12345, username="testuser")
        db_session.add(user)
        await db_session.flush()

        channel = Channel(
            user_id=user.id,
            channel_slug="testchannel",
            paused_reason=PausedReason.PLAN_LIMIT,
        )

        db_session.add(channel)
        await db_session.flush()

        assert channel.paused_reason == PausedReason.PLAN_LIMIT


class TestPredictionModel:
    """Test Prediction model and constraints."""

    async def test_create_prediction(self, db_session):
        """Test creating a prediction."""
        prediction = Prediction(
            channel_slug="testchannel",
            kick_prediction_id="pred123",
            title="Test Prediction",
            outcomes=[
                {"id": "1", "title": "Option 1"},
                {"id": "2", "title": "Option 2"},
            ],
            state=PredictionState.OPEN,
        )

        db_session.add(prediction)
        await db_session.flush()

        assert prediction.id is not None
        assert prediction.channel_slug == "testchannel"
        assert prediction.kick_prediction_id == "pred123"
        assert prediction.title == "Test Prediction"
        assert prediction.state == PredictionState.OPEN
        assert len(prediction.outcomes) == 2

    async def test_channel_prediction_unique_constraint(self, db_session):
        """Test that channel + prediction_id must be unique."""
        # Create first prediction
        pred1 = Prediction(
            channel_slug="testchannel",
            kick_prediction_id="pred123",
            title="Test Prediction 1",
            outcomes=[],
        )
        db_session.add(pred1)
        await db_session.flush()

        # Try to create another prediction with same channel + prediction_id
        pred2 = Prediction(
            channel_slug="testchannel",
            kick_prediction_id="pred123",
            title="Test Prediction 2",
            outcomes=[],
        )
        db_session.add(pred2)

        with pytest.raises(IntegrityError):
            await db_session.flush()


class TestVoteModel:
    """Test Vote model and constraints."""

    async def test_create_vote(self, db_session):
        """Test creating a vote."""
        # Create user
        user = User(kick_user_id=12345, username="testuser")
        db_session.add(user)
        await db_session.flush()

        # Create channel
        channel = Channel(user_id=user.id, channel_slug="testchannel")
        db_session.add(channel)
        await db_session.flush()

        # Create prediction
        prediction = Prediction(
            channel_slug="testchannel",
            kick_prediction_id="pred123",
            title="Test Prediction",
            outcomes=[],
        )
        db_session.add(prediction)
        await db_session.flush()

        # Create vote
        vote = Vote(
            user_id=user.id,
            channel_id=channel.id,
            prediction_id=prediction.id,
            outcome_id="outcome1",
            amount_requested=50,
            amount_placed=50,
            status=VoteStatus.PLACED,
        )

        db_session.add(vote)
        await db_session.flush()

        assert vote.id is not None
        assert vote.user_id == user.id
        assert vote.channel_id == channel.id
        assert vote.prediction_id == prediction.id
        assert vote.outcome_id == "outcome1"
        assert vote.amount_requested == 50
        assert vote.amount_placed == 50
        assert vote.status == VoteStatus.PLACED

    async def test_user_prediction_unique_constraint(self, db_session):
        """Test that user can only vote once per prediction."""
        # Create user, channel, prediction
        user = User(kick_user_id=12345, username="testuser")
        db_session.add(user)
        await db_session.flush()

        channel = Channel(user_id=user.id, channel_slug="testchannel")
        db_session.add(channel)
        await db_session.flush()

        prediction = Prediction(
            channel_slug="testchannel",
            kick_prediction_id="pred123",
            title="Test Prediction",
            outcomes=[],
        )
        db_session.add(prediction)
        await db_session.flush()

        # Create first vote
        vote1 = Vote(
            user_id=user.id,
            channel_id=channel.id,
            prediction_id=prediction.id,
            outcome_id="outcome1",
            amount_requested=50,
        )
        db_session.add(vote1)
        await db_session.flush()

        # Try to create second vote for same user + prediction
        vote2 = Vote(
            user_id=user.id,
            channel_id=channel.id,
            prediction_id=prediction.id,
            outcome_id="outcome2",
            amount_requested=25,
        )
        db_session.add(vote2)

        with pytest.raises(IntegrityError):
            await db_session.flush()


class TestOAuthModels:
    """Test OAuth-related models."""

    async def test_oauth_state(self, db_session):
        """Test OAuth state creation."""
        expires_at = datetime.utcnow() + timedelta(minutes=10)

        oauth_state = OAuthState(
            state="random_state_123",
            code_verifier="code_verifier_456",
            expires_at=expires_at,
        )

        db_session.add(oauth_state)
        await db_session.flush()

        assert oauth_state.state == "random_state_123"
        assert oauth_state.code_verifier == "code_verifier_456"
        assert oauth_state.expires_at == expires_at

    async def test_kick_token(self, db_session):
        """Test KickToken creation."""
        user = User(kick_user_id=12345, username="testuser")
        db_session.add(user)
        await db_session.flush()

        expires_at = datetime.utcnow() + timedelta(hours=1)

        kick_token = KickToken(
            user_id=user.id,
            access_token_enc="encrypted_access_token",
            refresh_token_enc="encrypted_refresh_token",
            expires_at=expires_at,
            scopes="read write",
            health=TokenHealth.OK,
        )

        db_session.add(kick_token)
        await db_session.flush()

        assert kick_token.user_id == user.id
        assert kick_token.access_token_enc == "encrypted_access_token"
        assert kick_token.refresh_token_enc == "encrypted_refresh_token"
        assert kick_token.expires_at == expires_at
        assert kick_token.scopes == "read write"
        assert kick_token.health == TokenHealth.OK


class TestSessionModel:
    """Test Session model."""

    async def test_create_session(self, db_session):
        """Test creating a session."""
        user = User(kick_user_id=12345, username="testuser")
        db_session.add(user)
        await db_session.flush()

        expires_at = datetime.utcnow() + timedelta(days=30)

        session = Session(
            id="hashed_session_token",
            user_id=user.id,
            expires_at=expires_at,
            user_agent="Test Browser",
            ip="127.0.0.1",
        )

        db_session.add(session)
        await db_session.flush()

        assert session.id == "hashed_session_token"
        assert session.user_id == user.id
        assert session.expires_at == expires_at
        assert session.user_agent == "Test Browser"
        assert session.ip == "127.0.0.1"


class TestUserSettings:
    """Test UserSettings model."""

    async def test_user_settings(self, db_session):
        """Test UserSettings creation."""
        user = User(kick_user_id=12345, username="testuser")
        db_session.add(user)
        await db_session.flush()

        settings = UserSettings(
            user_id=user.id, default_vote_amount=100, fallback_to_min=True, paused=False
        )

        db_session.add(settings)
        await db_session.flush()

        assert settings.user_id == user.id
        assert settings.default_vote_amount == 100
        assert settings.fallback_to_min is True
        assert settings.paused is False


async def test_database_tables_exist(db_session):
    """Test that all expected tables were created."""
    # Try to query each model to ensure tables exist
    models_to_test = [
        User,
        Channel,
        Prediction,
        Vote,
        Session,
        OAuthState,
        KickToken,
        UserSettings,
    ]

    for model in models_to_test:
        # This will fail if the table doesn't exist
        result = await db_session.execute(select(model).limit(1))
        assert result is not None
