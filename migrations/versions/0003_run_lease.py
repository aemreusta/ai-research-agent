"""Fence each dispatch with a fresh token; index active watchdog rows."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("lease_id", sa.UUID(), nullable=True))
    op.create_index(
        "ix_runs_watchdog",
        "runs",
        ["status", "deadline_at"],
        postgresql_where=sa.text("status IN ('dispatched', 'running', 'queued')"),
    )


def downgrade() -> None:
    op.drop_index("ix_runs_watchdog", table_name="runs")
    op.drop_column("runs", "lease_id")
