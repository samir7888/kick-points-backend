"""Test configuration and fixtures.

The suite talks to real Postgres/Redis instances, and its fixtures delete rows
(users, channels, votes, settings) between tests. It must therefore never run
against the database the development backend is serving, or every test run
would log the developer out and erase their channels.

To keep the two apart this module derives a dedicated ``*_test`` database from
the configured ``DATABASE_URL`` and points the application at it before any app
module is imported. Override with ``TEST_DATABASE_URL`` when the derived name
does not fit, or set ``ALLOW_DEV_TEST_DB=1`` to knowingly target another
database (not recommended).
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncGenerator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

BACKEND_ROOT = Path(__file__).resolve().parents[1]
_MAINTENANCE_DATABASE = "postgres"


def _configured_database_url() -> str:
    """Return the database URL the app itself would use (env var or `.env`)."""
    from app.core.config import Settings

    return os.getenv("DATABASE_URL") or str(Settings().database_url)


def _derive_test_database_url(dev_url: str) -> str:
    url = make_url(dev_url)
    name = url.database or "kick_predictions"
    return url.set(database=f"{name}_test").render_as_string(hide_password=False)


# Capture the development URL first, then redirect the whole application (and
# its cached settings) to the isolated test database. Order matters: importing
# `app` modules triggers `get_settings()`.
DEV_DATABASE_URL = _configured_database_url()
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL") or _derive_test_database_url(
    DEV_DATABASE_URL
)
os.environ["DATABASE_URL"] = TEST_DATABASE_URL


from app.db.models import Channel, User, Vote  # noqa: E402
from app.main import create_app  # noqa: E402


def _assert_safe_test_database() -> None:
    """Refuse to run when the target database looks like a real one."""
    if os.getenv("ALLOW_DEV_TEST_DB") == "1":
        return
    name = make_url(TEST_DATABASE_URL).database or ""
    dev_name = make_url(DEV_DATABASE_URL).database or ""
    if name == dev_name or not name.endswith("_test"):
        raise RuntimeError(
            "Refusing to run tests: the fixtures delete users, channels and "
            f"votes, and TEST_DATABASE_URL points at '{name}'. Use a dedicated "
            "'*_test' database (or set ALLOW_DEV_TEST_DB=1 to override)."
        )


async def _create_test_database_if_missing() -> None:
    """Create the test database on the same server when it does not exist yet."""
    import asyncpg

    url = make_url(TEST_DATABASE_URL)
    name = url.database
    admin_dsn = (
        url.set(database=_MAINTENANCE_DATABASE)
        .render_as_string(hide_password=False)
        .replace("+asyncpg", "")
    )
    connection = await asyncpg.connect(admin_dsn)
    try:
        exists = await connection.fetchval(
            "select 1 from pg_database where datname = $1", name
        )
        if not exists:
            await connection.execute(f'CREATE DATABASE "{name}"')
    finally:
        await connection.close()


def _apply_migrations() -> None:
    """Bring the test database schema up to date with Alembic."""
    from alembic import command
    from alembic.config import Config

    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    config.set_main_option("prepend_sys_path", str(BACKEND_ROOT))
    command.upgrade(config, "head")


@pytest.fixture(scope="session", autouse=True)
def prepare_test_database() -> Iterator[None]:
    """Ensure an isolated, migrated test database exists for the whole run."""
    _assert_safe_test_database()
    asyncio.run(_create_test_database_if_missing())
    _apply_migrations()
    yield


@pytest.fixture(autouse=True)
def reset_redis_client() -> Iterator[None]:
    """Drop the process-wide Redis client between tests.

    pytest-asyncio runs each async test in its own event loop, and the shared
    client is bound to the loop that created it. Resetting avoids reusing a
    client across loops, which would otherwise make cache/readiness checks fail.
    """
    from app.db import session as db_session

    db_session._redis_client = None
    yield
    db_session._redis_client = None


@pytest_asyncio.fixture
async def test_engine():
    """Create a fresh async database engine for each test."""
    engine = create_async_engine(
        TEST_DATABASE_URL,
        echo=False,
        future=True,
    )

    yield engine

    await engine.dispose()


@pytest_asyncio.fixture
async def test_session_maker(test_engine):
    """Create a session factory bound to the test engine."""
    return sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


@pytest_asyncio.fixture
async def db_session(test_session_maker) -> AsyncGenerator[AsyncSession, None]:
    """Provide a database session for each test and clear generated rows."""
    async with test_session_maker() as session:
        try:
            await session.rollback()
            await session.execute(delete(Vote))
            await session.execute(delete(Channel))
            await session.execute(delete(User))
            await session.commit()
            yield session
        finally:
            await session.rollback()
            await session.execute(delete(Vote))
            await session.execute(delete(Channel))
            await session.execute(delete(User))
            await session.commit()
            await session.close()


@pytest.fixture
def test_app():
    """Create test FastAPI app."""
    return create_app()


@pytest.fixture
def test_client(test_app):
    """Create test client."""
    return TestClient(test_app)


@pytest.fixture
def override_get_db_session(db_session):
    """Override the get_db_session dependency."""

    async def _get_test_db_session():
        yield db_session

    return _get_test_db_session
