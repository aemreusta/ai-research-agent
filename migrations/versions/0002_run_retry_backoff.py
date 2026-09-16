"""Retry backoff: a requeued run is not claimable before `runs.available_at`.

Set by the watchdog when it requeues after a lost heartbeat (config/dispatcher.yaml
`retry.backoff_seconds`), and honoured by both claim implementations (Python and Go).

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-16 09:14:30.533154+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("available_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "available_at")
