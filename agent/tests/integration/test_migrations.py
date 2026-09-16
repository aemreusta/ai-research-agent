"""The migration and the models must describe the same database."""

from __future__ import annotations

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy.ext.asyncio import AsyncSession

from research_agent.db.models import Base

pytestmark = pytest.mark.integration


async def test_head_matches_the_models_with_no_drift(db_session: AsyncSession) -> None:
    """Catches the classic mistake: a model edited without a matching revision."""

    def diff(connection: object) -> list[object]:
        context = MigrationContext.configure(connection)  # type: ignore[arg-type]
        return list(compare_metadata(context, Base.metadata))

    connection = await db_session.connection()
    differences = await connection.run_sync(diff)
    assert differences == [], f"models and migrations disagree: {differences}"
