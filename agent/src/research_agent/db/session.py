"""Engine and session plumbing, plus the one place that normalises `DATABASE_URL`.

Three consumers need the same database from three angles: SQLAlchemy async (api, agent),
Alembic (sync, the `migrate` one-shot container) and libraries that want a bare libpq DSN
(the LangGraph Postgres checkpointer, and `LISTEN/NOTIFY`). Rather than ask the operator for
three spellings of the same thing, `.env` carries one URL and this module derives the rest.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache

from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from research_agent.errors import AgentError, AgentException, ErrorCode

# One driver for the whole system (D36): SQLAlchemy async, Alembic and psycopg's own connection
# objects all speak psycopg 3, so `DATABASE_URL` is rewritten to it whatever the operator wrote.
_DRIVER = "psycopg"


class DatabaseNotConfiguredError(AgentException):
    def __init__(self, detail: str) -> None:
        super().__init__(
            AgentError(
                code=ErrorCode.CONFIG_INVALID,
                node="db",
                decision="refuse to start",
                outcome=detail,
            )
        )


def _url(raw: str | None = None) -> URL:
    value = raw or os.environ.get("DATABASE_URL")
    if not value:
        raise DatabaseNotConfiguredError("DATABASE_URL is not set")
    url = make_url(value)
    if not url.get_backend_name().startswith("postgresql"):
        raise DatabaseNotConfiguredError(f"DATABASE_URL must be PostgreSQL, got {url.drivername}")
    return url.set(drivername=f"postgresql+{_DRIVER}")


def database_url(raw: str | None = None) -> str:
    """The URL SQLAlchemy uses, async or sync alike (psycopg 3 serves both)."""
    return _url(raw).render_as_string(hide_password=False)


def sync_database_url(raw: str | None = None) -> str:
    """Same thing, spelled for Alembic. Kept separate so the intent is visible at call sites."""
    return database_url(raw)


def libpq_dsn(raw: str | None = None) -> str:
    """A driver-less URL for libraries that hand the string straight to psycopg."""
    return _url(raw).set(drivername="postgresql").render_as_string(hide_password=False)


@lru_cache(maxsize=1)
def engine() -> AsyncEngine:
    """Process-wide engine.

    `pool_pre_ping` matters here: the agent holds connections across long LLM calls and Postgres
    restarts during a `docker compose up` are routine.
    """
    return create_async_engine(
        database_url(),
        pool_pre_ping=True,
        pool_size=int(os.environ.get("APP_DB_POOL_SIZE", "5")),
        max_overflow=int(os.environ.get("APP_DB_MAX_OVERFLOW", "5")),
        future=True,
    )


@lru_cache(maxsize=1)
def session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine(), expire_on_commit=False, autoflush=False)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """A transaction that commits on success and rolls back on any exception."""
    async with session_factory()() as session:
        try:
            yield session
        except BaseException:
            await session.rollback()
            raise
        else:
            await session.commit()


async def dispose() -> None:
    """Close the pool. Called from the FastAPI lifespan and the CLI, so tests do not leak."""
    if engine.cache_info().currsize:
        await engine().dispose()
    engine.cache_clear()
    session_factory.cache_clear()
