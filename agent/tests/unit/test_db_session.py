"""`DATABASE_URL` normalisation: one spelling in `.env`, three consumers."""

from __future__ import annotations

import pytest

from research_agent.db.session import (
    DatabaseNotConfiguredError,
    database_url,
    libpq_dsn,
    sync_database_url,
)
from research_agent.errors import ErrorCode

BARE = "postgresql://research:pw@postgres:5432/research"


@pytest.mark.parametrize(
    "raw",
    [
        BARE,
        "postgresql+asyncpg://research:pw@postgres:5432/research",
        "postgresql+psycopg://research:pw@postgres:5432/research",
    ],
)
def test_any_postgres_spelling_normalises_to_the_one_driver(raw: str) -> None:
    assert database_url(raw) == "postgresql+psycopg://research:pw@postgres:5432/research"
    assert sync_database_url(raw) == database_url(raw)


def test_libpq_dsn_drops_the_driver_for_psycopg_and_listen_notify() -> None:
    assert libpq_dsn("postgresql+asyncpg://research:pw@postgres:5432/research") == BARE


def test_password_survives_normalisation() -> None:
    """`render_as_string` hides passwords by default - that would produce an unusable URL."""
    assert "pw" in database_url(BARE)


def test_missing_url_is_a_config_error_not_a_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(DatabaseNotConfiguredError) as caught:
        database_url()
    assert caught.value.error.code is ErrorCode.CONFIG_INVALID


def test_non_postgres_url_is_rejected() -> None:
    with pytest.raises(DatabaseNotConfiguredError):
        database_url("sqlite+aiosqlite:///./local.db")
