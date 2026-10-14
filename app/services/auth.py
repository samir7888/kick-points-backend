"""Authentication and session management service."""

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..core.config import get_settings
from ..core.security import decrypt_token, encrypt_token, hash_session_token
from ..db.models import KickToken, OAuthState, Session, User, UserSettings


class AuthService:
    """Service for authentication and session management."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.settings = get_settings()

    async def create_or_update_user(
        self,
        kick_user_id: int,
        username: str,
        email: str | None = None,
    ) -> User:
        """Create a new user or update an existing user based on Kick user ID."""
        stmt = select(User).where(User.kick_user_id == kick_user_id)
        result = await self.session.execute(stmt)
        existing_user = result.scalar_one_or_none()

        if existing_user:
            existing_user.username = username
            if email:
                existing_user.email = email
            existing_user.last_login_at = datetime.now(UTC)
            await self.session.commit()
            return existing_user

        user = User(
            kick_user_id=kick_user_id,
            username=username,
            email=email,
            last_login_at=datetime.now(UTC),
            is_admin=kick_user_id in self.settings.admin_kick_user_ids_list,
        )

        self.session.add(user)
        await self.session.commit()
        return user

    async def create_session(
        self,
        user: User,
        user_agent: str | None = None,
        ip: str | None = None,
    ) -> str:
        """Create a new session for the user and return the raw session token."""
        session_token = secrets.token_urlsafe(32)
        session_hash = hash_session_token(session_token)

        session = Session(
            id=session_hash,
            user_id=user.id,
            expires_at=datetime.now(UTC)
            + timedelta(days=self.settings.session_ttl_days),
            user_agent=user_agent,
            ip=ip,
        )

        self.session.add(session)
        await self.session.commit()
        return session_token

    async def get_user_by_session_token(self, session_token: str) -> User | None:
        """Get user by session token, return None if invalid or expired."""
        session_hash = hash_session_token(session_token)

        stmt = (
            select(Session)
            .where(Session.id == session_hash)
            .where(Session.expires_at > datetime.now(UTC))
            .options(selectinload(Session.user))
        )
        result = await self.session.execute(stmt)
        session = result.scalar_one_or_none()

        if not session:
            return None
        if session.user.deleted_at:
            return None
        session.expires_at = datetime.now(UTC) + timedelta(
            days=self.settings.session_ttl_days
        )
        return session.user

    async def extend_session(self, session_token: str) -> bool:
        """Extend the expiry time for a session."""
        session_hash = hash_session_token(session_token)
        stmt = select(Session).where(Session.id == session_hash)
        result = await self.session.execute(stmt)
        session = result.scalar_one_or_none()

        if not session:
            return False

        session.expires_at = datetime.now(UTC) + timedelta(
            days=self.settings.session_ttl_days
        )
        await self.session.commit()
        return True

    async def invalidate_session(self, session_token: str) -> bool:
        """Invalidate a session by deleting it."""
        session_hash = hash_session_token(session_token)
        stmt = select(Session).where(Session.id == session_hash)
        result = await self.session.execute(stmt)
        session = result.scalar_one_or_none()

        if not session:
            return False

        await self.session.delete(session)
        await self.session.commit()
        return True

    async def invalidate_all_user_sessions(self, user_id: int) -> int:
        """Invalidate all sessions for a user."""
        stmt = select(Session).where(Session.user_id == user_id)
        result = await self.session.execute(stmt)
        sessions = result.scalars().all()

        for session in sessions:
            await self.session.delete(session)

        await self.session.commit()
        return len(sessions)

    async def cleanup_expired_sessions(self) -> int:
        """Clean up expired sessions."""
        stmt = select(Session).where(Session.expires_at <= datetime.now(UTC))
        result = await self.session.execute(stmt)
        expired_sessions = result.scalars().all()

        for session in expired_sessions:
            await self.session.delete(session)

        await self.session.commit()
        return len(expired_sessions)


async def create_oauth_state(db: AsyncSession) -> tuple[str, str, str]:
    """Create an OAuth state and PKCE verifier pair."""
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(32)
    expires_at = datetime.now(UTC) + timedelta(minutes=10)

    db.add(OAuthState(state=state, code_verifier=verifier, expires_at=expires_at))
    await db.flush()

    from ..integrations.kick.oauth import KickOAuth

    oauth = KickOAuth()
    try:
        authorize_url = oauth.generate_authorization_url(state, verifier)
    finally:
        await oauth.close()
    return state, verifier, authorize_url


async def create_session(
    user_id: int,
    db: AsyncSession,
    *,
    user_agent: str | None = None,
    ip: str | None = None,
) -> str:
    """Create a session token for a user and persist it in the database."""
    user = await db.get(User, user_id)
    if user is None:
        raise ValueError("user_not_found")
    session_token = secrets.token_urlsafe(32)
    session_hash = hash_session_token(session_token)

    session_settings = get_settings()
    db.add(
        Session(
            id=session_hash,
            user_id=user.id,
            expires_at=datetime.now(UTC)
            + timedelta(days=session_settings.session_ttl_days),
            user_agent=user_agent,
            ip=ip,
        )
    )
    await db.flush()
    return session_token


async def consume_oauth_state(state: str, db: AsyncSession) -> str:
    """Consume and validate a one-time OAuth state."""
    now = datetime.now(UTC)
    result = await db.execute(
        delete(OAuthState)
        .where(OAuthState.state == state, OAuthState.expires_at > now)
        .returning(OAuthState.code_verifier)
    )
    verifier = result.scalar_one_or_none()
    if verifier is not None:
        return verifier

    await db.execute(delete(OAuthState).where(OAuthState.state == state))
    await db.flush()
    raise ValueError("invalid_or_expired_oauth_state")


async def upsert_user_from_kick(
    kick_user_data: dict[str, Any],
    token_data: dict[str, Any],
    db: AsyncSession,
) -> User:
    """Create or update a local user from Kick OAuth payload and tokens."""
    user_id = int(kick_user_data["id"])
    username = str(kick_user_data["username"])
    email = kick_user_data.get("email")
    now = datetime.now(UTC)
    values = {
        "kick_user_id": user_id,
        "username": username,
        "email": email,
        "last_login_at": now,
        "is_admin": user_id in get_settings().admin_kick_user_ids_list,
    }
    insert_statement = (
        sqlite_insert(User)
        if db.get_bind().dialect.name == "sqlite"
        else pg_insert(User)
    )
    update_values = {"username": username, "last_login_at": now}
    if email:
        update_values["email"] = email
    upsert = (
        insert_statement.values(**values)
        .on_conflict_do_update(
            index_elements=[User.kick_user_id],
            set_=update_values,
        )
        .returning(User.id)
    )
    user_id_result = await db.execute(upsert)
    local_user_id = user_id_result.scalar_one()
    user = await db.get(User, local_user_id)
    if user is None:
        raise RuntimeError("User disappeared after OAuth upsert")

    access_token = str(token_data["access_token"])
    refresh_token = str(token_data.get("refresh_token") or "")
    expires_at = datetime.now(UTC) + timedelta(
        seconds=int(token_data.get("expires_in", 3600))
    )

    from ..db.models import TokenHealth

    token_values = {
        "user_id": user.id,
        "access_token_enc": encrypt_token(access_token),
        "refresh_token_enc": encrypt_token(refresh_token),
        "expires_at": expires_at,
        "scopes": token_data.get("scope", ""),
        "health": TokenHealth.OK,
    }
    token_insert = (
        sqlite_insert(KickToken)
        if db.get_bind().dialect.name == "sqlite"
        else pg_insert(KickToken)
    )
    await db.execute(
        token_insert.values(**token_values).on_conflict_do_update(
            index_elements=[KickToken.user_id],
            set_={
                "access_token_enc": token_values["access_token_enc"],
                "refresh_token_enc": token_values["refresh_token_enc"],
                "expires_at": expires_at,
                "scopes": token_values["scopes"],
                "health": TokenHealth.OK,
            },
        )
    )

    await db.flush()
    return user


async def get_user_from_session(session_token: str, db: AsyncSession) -> User | None:
    """Fetch the user for a session token."""
    if not session_token:
        return None
    session_hash = hash_session_token(session_token)
    stmt = (
        select(Session)
        .where(Session.id == session_hash)
        .where(Session.expires_at > datetime.now(UTC))
        .options(selectinload(Session.user))
    )
    result = await db.execute(stmt)
    session = result.scalar_one_or_none()
    if not session:
        return None
    if session.user.deleted_at:
        return None
    session.expires_at = datetime.now(UTC) + timedelta(
        days=get_settings().session_ttl_days
    )
    # Commit now so the row lock on the shared session row is not held for the
    # remainder of the request. Keeping it open serializes every request that
    # reuses the same session and, together with connection-pool pressure, can
    # stall the whole process.
    await db.commit()
    return session.user


async def invalidate_session(session_token: str, db: AsyncSession) -> bool:
    """Invalidate a single user session by raw token."""
    return await delete_session(session_token, db)


async def invalidate_all_user_sessions(user_id: int, db: AsyncSession) -> int:
    """Invalidate every session for a user."""
    stmt = delete(Session).where(Session.user_id == user_id)
    result = await db.execute(stmt)
    return int(result.rowcount or 0)


async def delete_session(session_token: str, db: AsyncSession) -> bool:
    """Delete a session by raw token."""
    if not session_token:
        return False
    session_hash = hash_session_token(session_token)
    stmt = select(Session).where(Session.id == session_hash)
    result = await db.execute(stmt)
    session = result.scalar_one_or_none()
    if not session:
        return False
    await db.delete(session)
    await db.flush()
    return True


async def get_decrypted_token(user_id: int, db: AsyncSession) -> str:
    """Read and decrypt the stored Kick OAuth access token."""
    result = await db.execute(select(KickToken).where(KickToken.user_id == user_id))
    kt = result.scalar_one_or_none()
    if not kt:
        raise ValueError("no_kick_token")
    return decrypt_token(kt.access_token_enc)


async def ensure_user_settings(user_id: int, db: AsyncSession) -> UserSettings:
    """Ensure a user settings record exists for a given user."""
    result = await db.execute(
        select(UserSettings).where(UserSettings.user_id == user_id)
    )
    us = result.scalar_one_or_none()
    if us is not None:
        return us

    settings = get_settings()
    us = UserSettings(
        user_id=user_id,
        default_vote_amount=settings.min_vote_amount,
        fallback_to_min=True,
        paused=False,
    )
    db.add(us)
    await db.flush()
    return us


async def get_user_settings(user_id: int, db: AsyncSession) -> UserSettings:
    """Compatibility helper for settings access."""
    return await ensure_user_settings(user_id, db)


async def create_session_for_user(
    user_id: int,
    db: AsyncSession,
    *,
    user_agent: str | None = None,
    ip: str | None = None,
) -> str:
    """Compatibility helper to create a session for a user ID."""
    user = await db.get(User, user_id)
    if user is None:
        raise ValueError("user_not_found")
    return await create_session(user_id, db, user_agent=user_agent, ip=ip)


async def cleanup_user_sessions(user_id: int, db: AsyncSession) -> int:
    """Compatibility helper to remove all sessions for a user."""
    return await invalidate_all_user_sessions(user_id, db)


async def delete_sessions_for_user(user_id: int, db: AsyncSession) -> int:
    """Compatibility helper to clear all sessions for a user."""
    stmt = delete(Session).where(Session.user_id == user_id)
    result = await db.execute(stmt)
    return int(result.rowcount or 0)
