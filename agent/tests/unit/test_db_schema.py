"""Schema-level guarantees that a migration must never quietly lose.

These run without a database: they inspect the SQLAlchemy metadata. The point is to pin the
handful of properties the rest of the system depends on - the run status vocabulary is the one
in the shared contract, events cascade with their run, and the secrets table holds no plaintext.
"""

from __future__ import annotations

import pytest
from sqlalchemy import CheckConstraint, DateTime, UniqueConstraint

from research_agent.contracts import RunStatus
from research_agent.db import models

EXPECTED_TABLES = {
    "runs",
    "run_secrets",
    "run_events",
    "llm_calls",
    "search_calls",
    "run_artifacts",
    "search_cache",
    "embedding_cache",
    "presets",
}


def test_metadata_holds_exactly_the_documented_tables() -> None:
    assert set(models.Base.metadata.tables) == EXPECTED_TABLES


def test_every_table_has_a_primary_key() -> None:
    without = [name for name, t in models.Base.metadata.tables.items() if not t.primary_key.columns]
    assert without == []


def test_run_status_check_constraint_mirrors_the_contract() -> None:
    """The DB vocabulary is generated from `RunStatus`, so a new state cannot skip the schema."""
    runs = models.Base.metadata.tables["runs"]
    checks = [c for c in runs.constraints if isinstance(c, CheckConstraint)]
    status_check = next(c for c in checks if c.name == "ck_runs_status")
    sql = str(status_check.sqltext)
    for status in RunStatus:
        assert f"'{status.value}'" in sql


def test_run_events_are_ordered_per_run() -> None:
    events = models.Base.metadata.tables["run_events"]
    uniques = [c for c in events.constraints if isinstance(c, UniqueConstraint)]
    assert any({col.name for col in c.columns} == {"run_id", "seq"} for c in uniques)


@pytest.mark.parametrize(
    "table",
    ["run_secrets", "run_events", "llm_calls", "search_calls", "run_artifacts"],
)
def test_run_scoped_tables_cascade_with_their_run(table: str) -> None:
    """Deleting a run must not leave orphan telemetry or, worse, orphan encrypted keys."""
    column = models.Base.metadata.tables[table].c["run_id"]
    foreign_key = next(iter(column.foreign_keys))
    assert foreign_key.column.table.name == "runs"
    assert foreign_key.ondelete == "CASCADE"


def test_run_secrets_stores_ciphertext_only() -> None:
    columns = set(models.Base.metadata.tables["run_secrets"].c.keys())
    assert "ciphertext" in columns
    assert not columns & {"key", "api_key", "plaintext", "secret", "value"}


def test_all_timestamps_are_timezone_aware() -> None:
    """Naive timestamps make heartbeat and deadline arithmetic wrong across processes."""
    naive = [
        f"{table.name}.{column.name}"
        for table in models.Base.metadata.tables.values()
        for column in table.c
        if isinstance(column.type, DateTime) and not column.type.timezone
    ]
    assert naive == []


def test_queue_claim_has_an_index_to_claim_from() -> None:
    """The dispatcher claims with `status='queued' ORDER BY created_at FOR UPDATE SKIP LOCKED`."""
    runs = models.Base.metadata.tables["runs"]
    indexed = [{c.name for c in index.columns} for index in runs.indexes]
    assert {"status", "created_at"} in indexed
