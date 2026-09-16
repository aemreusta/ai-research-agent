"""The migration and the models must describe the same database."""

from __future__ import annotations

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy.ext.asyncio import AsyncSession

from research_agent.db.models import Base, owned_by_us

pytestmark = pytest.mark.integration


async def test_head_matches_the_models_with_no_drift(db_session: AsyncSession) -> None:
    """Catches the classic mistake: a model edited without a matching revision."""

    def diff(connection: object) -> list[object]:
        context = MigrationContext.configure(
            connection,  # type: ignore[arg-type]
            opts={"include_name": lambda name, type_, _: owned_by_us(name, type_)},
        )
        return list(compare_metadata(context, Base.metadata))

    connection = await db_session.connection()
    differences = await connection.run_sync(diff)
    assert differences == [], f"models and migrations disagree: {differences}"


def test_langgraph_tables_are_never_ours_to_drop() -> None:
    assert not owned_by_us("checkpoints", "table")
    assert not owned_by_us("checkpoint_writes", "table")
    assert owned_by_us("runs", "table")
