"""The agent service's real path: executor -> ResearchGraphRunner -> Postgres everywhere."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from research_agent.agent.research import ResearchGraphRunner, Toolkit
from research_agent.agent.simulated import simulated_catalog, simulated_llm, simulated_search
from research_agent.agent_server.executor import RunExecutor
from research_agent.config.loader import load_settings
from research_agent.contracts import RunStatus
from research_agent.db.models import LlmCall, RunArtifact, RunEvent, SearchCall
from research_agent.db.repository import RunRepository
from research_agent.db.session import libpq_dsn
from research_agent.keys import ProviderKeys

pytestmark = pytest.mark.integration


async def _toolkit(keys: ProviderKeys) -> Toolkit:
    return Toolkit(
        llm={"simulated": simulated_llm()},
        search={"tavily": simulated_search()},
        catalog=simulated_catalog(),
    )


async def _no_keys(keys: ProviderKeys) -> Toolkit:
    return Toolkit(llm={}, search={})


async def _queue(session: AsyncSession) -> uuid.UUID:
    config = load_settings()
    repo = RunRepository(session)
    run = await repo.create(
        question_masked="What is the EU AI Act implementation timeline?",
        config_snapshot=config.snapshot,
        config_hash=config.config_hash,
    )
    await repo.claim(dispatcher_id="test")
    return run.id


async def test_a_research_run_completes_through_the_service_path(
    migrated_database: str,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    run_id = await _queue(db_session)
    runner = ResearchGraphRunner(
        db_sessionmaker, dsn=libpq_dsn(migrated_database), toolkit_factory=_toolkit
    )
    executor = RunExecutor(
        db_sessionmaker, agent_id="agent-it", slots=1, runner=runner, heartbeat_interval_seconds=0.5
    )
    lease_id = (await RunRepository(db_session).require(run_id)).lease_id
    assert lease_id is not None
    await executor.execute(run_id, attempt=1, lease_id=lease_id)
    await executor.wait_for(run_id, timeout=60)

    run = await RunRepository(db_session).require(run_id)
    assert run.status in {RunStatus.SUCCEEDED.value, RunStatus.SUCCEEDED_WITH_WARNINGS.value}
    assert run.stop_reason and run.gate_status in {"pass", "pass_with_warnings"}
    assert run.prompt_versions["synthesize"]["source"] == "yaml"
    assert run.models_used["reasoning"].startswith("simulated:")
    assert run.iteration >= 1 and run.searches_used >= 1

    kinds = set(
        (
            await db_session.execute(select(RunArtifact.kind).where(RunArtifact.run_id == run_id))
        ).scalars()
    )
    assert kinds == {"report_md", "report_json", "gate_result", "state"}
    for table in (LlmCall, SearchCall):
        count = (
            await db_session.execute(
                select(func.count()).select_from(table).where(table.run_id == run_id)
            )
        ).scalar_one()
        assert count > 0, table.__tablename__
    types = set(
        (
            await db_session.execute(select(RunEvent.event_type).where(RunEvent.run_id == run_id))
        ).scalars()
    )
    assert {"plan_created", "search_called", "gate_result", "report_ready", "run_finished"} <= types


async def test_a_run_without_keys_fails_with_an_actionable_message(
    migrated_database: str,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    run_id = await _queue(db_session)
    runner = ResearchGraphRunner(
        db_sessionmaker, dsn=libpq_dsn(migrated_database), toolkit_factory=_no_keys
    )
    executor = RunExecutor(db_sessionmaker, agent_id="agent-it", slots=1, runner=runner)
    lease_id = (await RunRepository(db_session).require(run_id)).lease_id
    assert lease_id is not None
    await executor.execute(run_id, attempt=1, lease_id=lease_id)
    await executor.wait_for(run_id, timeout=30)
    run = await RunRepository(db_session).require(run_id)
    assert run.status == RunStatus.FAILED.value
    assert run.error_code == "LLM_AUTH"
    assert "Settings" in (run.error_message or "")
