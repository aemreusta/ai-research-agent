"""Shared fixtures.

Unit tests never touch the network or a database. Anything that needs real Postgres asks for
`db_session` or `db_sessionmaker` and is marked `integration`; those tests skip unless
`TEST_DATABASE_URL` points at a disposable database (`make test-integration` sets it, and so
does the compose `test` profile).
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from research_agent.db.models import Base
from research_agent.db.session import database_url


@pytest.fixture(scope="session")
def migrated_database() -> Iterator[str]:
    """A database with the schema at `head`, migrated once for the whole session."""
    raw = os.environ.get("TEST_DATABASE_URL")
    if not raw:
        pytest.skip("set TEST_DATABASE_URL to run integration tests")

    from alembic import command
    from alembic.config import Config

    repo_root = Path(__file__).resolve().parents[2]
    config = Config(str(repo_root / "alembic.ini"))
    config.set_main_option("script_location", str(repo_root / "migrations"))
    os.environ["DATABASE_URL"] = raw
    command.upgrade(config, "head")
    yield database_url(raw)


@pytest.fixture
async def db_engine(migrated_database: str) -> AsyncIterator[AsyncEngine]:
    """An engine against an empty database: every table is truncated first.

    Truncating beats recreating the schema per test (fast) and beats wrapping each test in a
    rolled-back transaction (the code under test commits deliberately and uses `LISTEN/NOTIFY`,
    neither of which survives an outer transaction).
    """
    engine = create_async_engine(migrated_database)
    tables = ", ".join(f'"{name}"' for name in Base.metadata.tables)
    async with engine.begin() as connection:
        await connection.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    yield engine
    await engine.dispose()


@pytest.fixture
def db_sessionmaker(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(db_engine, expire_on_commit=False, autoflush=False)


@pytest.fixture
async def db_session(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with db_sessionmaker() as session:
        yield session
