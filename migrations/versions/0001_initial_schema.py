"""Initial schema: queue, event store, telemetry, caches and artifacts.

Generated from `research_agent.db.models`, which is the readable description of these tables -
including why each one exists. Nothing here is hand-written except this docstring, so the models
and the database cannot drift.

The LangGraph checkpoint tables are not created here: `langgraph-checkpoint-postgres` owns them
and creates them on first use (architecture v0.6 §6).

Revision ID: 0001
Revises:
Create Date: 2026-09-16 08:49:25.048179+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "embedding_cache",
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("vector", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("key_hash", name=op.f("pk_embedding_cache")),
    )
    op.create_table(
        "presets",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("overrides", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_presets")),
        sa.UniqueConstraint("name", name=op.f("uq_presets_name")),
    )
    op.create_table(
        "runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("question_masked", sa.Text(), nullable=False),
        sa.Column("question_language", sa.String(length=8), nullable=True),
        sa.Column("config_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("config_hash", sa.String(length=64), nullable=False),
        sa.Column("overrides", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("prompt_versions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("models_used", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("skills_used", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("key_sources", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("stop_reason", sa.String(length=32), nullable=True),
        sa.Column("gate_status", sa.String(length=32), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("iteration", sa.Integer(), nullable=False),
        sa.Column("searches_used", sa.Integer(), nullable=False),
        sa.Column("tokens_in", sa.Integer(), nullable=False),
        sa.Column("tokens_out", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(precision=14, scale=6), nullable=False),
        sa.Column("event_seq", sa.Integer(), nullable=False),
        sa.Column("agent_id", sa.String(length=128), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("langfuse_trace_url", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued', 'dispatched', 'running', 'succeeded', "
            "'succeeded_with_warnings', 'failed', 'cancelled')",
            name=op.f("ck_runs_status"),
        ),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_runs_attempts_non_negative")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_runs")),
    )
    op.create_index("ix_runs_heartbeat_at", "runs", ["heartbeat_at"], unique=False)
    op.create_index("ix_runs_status_created_at", "runs", ["status", "created_at"], unique=False)
    op.create_table(
        "search_cache",
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("response", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key_hash", name=op.f("pk_search_cache")),
    )
    op.create_index(
        op.f("ix_search_cache_expires_at"), "search_cache", ["expires_at"], unique=False
    )
    op.create_table(
        "llm_calls",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("span_id", sa.String(length=32), nullable=False),
        sa.Column("node", sa.String(length=64), nullable=False),
        sa.Column("iteration", sa.Integer(), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("tier", sa.String(length=16), nullable=False),
        sa.Column("prompt_id", sa.String(length=64), nullable=True),
        sa.Column("prompt_version", sa.String(length=32), nullable=True),
        sa.Column("tokens_in", sa.Integer(), nullable=False),
        sa.Column("tokens_out", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(precision=14, scale=6), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("fallback_from", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("request_redacted", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("response_redacted", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["runs.id"], name=op.f("fk_llm_calls_run_id"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_llm_calls")),
    )
    op.create_index(op.f("ix_llm_calls_run_id"), "llm_calls", ["run_id"], unique=False)
    op.create_table(
        "run_artifacts",
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("content_type", sa.String(length=64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["runs.id"], name=op.f("fk_run_artifacts_run_id"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("run_id", "kind", name=op.f("pk_run_artifacts")),
    )
    op.create_table(
        "run_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column(
            "ts", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("level", sa.String(length=16), nullable=False),
        sa.Column("node", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("iteration", sa.Integer(), nullable=True),
        sa.Column("span_id", sa.String(length=32), nullable=True),
        sa.Column("parent_span_id", sa.String(length=32), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("tokens_in", sa.Integer(), nullable=True),
        sa.Column("tokens_out", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(precision=14, scale=6), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("expected", sa.Boolean(), nullable=True),
        sa.CheckConstraint("seq > 0", name=op.f("ck_run_events_seq_positive")),
        sa.ForeignKeyConstraint(
            ["run_id"], ["runs.id"], name=op.f("fk_run_events_run_id"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_run_events")),
        sa.UniqueConstraint("run_id", "seq", name="uq_run_events_run_id_seq"),
    )
    op.create_index("ix_run_events_run_id_seq", "run_events", ["run_id", "seq"], unique=False)
    op.create_table(
        "run_secrets",
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["runs.id"], name=op.f("fk_run_secrets_run_id"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("run_id", "provider", name=op.f("pk_run_secrets")),
    )
    op.create_table(
        "search_calls",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("span_id", sa.String(length=32), nullable=False),
        sa.Column("node", sa.String(length=64), nullable=False),
        sa.Column("iteration", sa.Integer(), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("subquestion_id", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("result_count", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("cache_hit", sa.Boolean(), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["runs.id"], name=op.f("fk_search_calls_run_id"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_search_calls")),
    )
    op.create_index(op.f("ix_search_calls_run_id"), "search_calls", ["run_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_search_calls_run_id"), table_name="search_calls")
    op.drop_table("search_calls")
    op.drop_table("run_secrets")
    op.drop_index("ix_run_events_run_id_seq", table_name="run_events")
    op.drop_table("run_events")
    op.drop_table("run_artifacts")
    op.drop_index(op.f("ix_llm_calls_run_id"), table_name="llm_calls")
    op.drop_table("llm_calls")
    op.drop_index(op.f("ix_search_cache_expires_at"), table_name="search_cache")
    op.drop_table("search_cache")
    op.drop_index("ix_runs_status_created_at", table_name="runs")
    op.drop_index("ix_runs_heartbeat_at", table_name="runs")
    op.drop_table("runs")
    op.drop_table("presets")
    op.drop_table("embedding_cache")
