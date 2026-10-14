"""Database session management."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import redis.asyncio as redis
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from ..core.config import get_settings

_engine = None
_session_maker = None
_redis_client = None


def get_engine():
    """Get or create the async database engine."""
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            str(settings.database_url),
            echo=not settings.is_production,
            future=True,
            # Discard connections that died while idle (e.g. after a Postgres
            # restart) instead of surfacing them as request errors.
            pool_pre_ping=True,
            pool_recycle=1800,
            pool_size=5,
            max_overflow=10,
            pool_timeout=30,
        )
    return _engine


def get_session_maker():
    """Get or create the async session maker."""
    global _session_maker
    if _session_maker is None:
        _session_maker = sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _session_maker


async def get_redis() -> redis.Redis:
    """Return a shared Redis client."""
    global _redis_client
    if _redis_client is None:
        settings = get_settings()
        _redis_client = redis.from_url(str(settings.redis_url), decode_responses=True)
    return _redis_client


async def close_redis() -> None:
    """Close the shared Redis client during process shutdown."""
    global _redis_client
    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None


async def close_database() -> None:
    """Dispose the shared SQLAlchemy connection pool during process shutdown."""
    global _engine, _session_maker
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_maker = None


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency that provides database sessions for FastAPI."""
    session_maker = get_session_maker()
    async with session_maker() as session:
        try:
            yield session
        except BaseException:
            await session.rollback()
            raise
        else:
            try:
                await session.commit()
            except Exception:
                await session.rollback()
                raise
        finally:
            await session.close()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Backward-compatible alias for FastAPI DB dependencies."""
    async for session in get_db_session():
        yield session


def get_session() -> AsyncSession:
    """Compatibility function for legacy code/tests."""
    return get_session_maker()()
