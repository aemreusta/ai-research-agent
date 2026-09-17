"""A revoked worker must never publish into a newer attempt, including its checkpoints."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import empty_checkpoint
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from research_agent.agent.runner import RunContext, RunOutcome
from research_agent.agent_server.executor import RunExecutor
from research_agent.config.loader import load_settings
from research_agent.contracts import RunStatus
from research_agent.db.checkpoints import lease_checkpointer
from research_agent.db.models import Run, RunArtifact, RunEvent
from research_agent.db.repository import IllegalTransitionError, RunRepository
from research_agent.db.session import libpq_dsn
from research_agent.errors import LeaseLostError
from research_agent.observability.events import EventType, EventWriter

pytestmark = pytest.mark.integration


async def claimed(session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    config = load_settings()
    repo = RunRepository(session)
    await repo.create(
        question_masked="Lease test",
        config_snapshot=config.snapshot,
        config_hash=config.config_hash,
    )
    run = await repo.claim(dispatcher_id="test")
    assert run is not None and run.lease_id is not None
    return run.id, run.lease_id


@pytest.mark.parametrize("operation", ["heartbeat", "counters", "metadata", "finish", "events"])
async def test_revoked_worker_cannot_write_into_the_new_attempt(
    db_session: AsyncSession, db_sessionmaker: async_sessionmaker[AsyncSession], operation: str
) -> None:
    run_id, old_lease = await claimed(db_session)
    admin = RunRepository(db_session)
    await admin.mark_running(run_id, agent_id="old", deadline_at=None)
    await admin.requeue(run_id, reason="AGENT_HEARTBEAT_LOST")
    current = await admin.claim(dispatcher_id="recovery")
    assert current is not None and current.lease_id != old_lease
    await admin.mark_running(run_id, agent_id="new", deadline_at=None)

    async with db_sessionmaker() as session:
        old = RunRepository(session, lease_id=old_lease)
        with pytest.raises(LeaseLostError):
            match operation:
                case "heartbeat":
                    await old.heartbeat(run_id)
                case "counters":
                    await old.update_counters(run_id, tokens_in=777)
                case "metadata":
                    await old.update_metadata(run_id, skills_used=["stale"])
                case "finish":
                    await old.finish(
                        run_id,
                        status=RunStatus.SUCCEEDED,
                        artifacts={"report_md": ("text/markdown", "stale")},
                        emit_finished=True,
                    )
                case "events":
                    await EventWriter(
                        db_sessionmaker, run_id=run_id, node="old", lease_id=old_lease
                    ).info(EventType.REPORT_READY, "stale")
    final = await admin.require(run_id)
    assert final.status == "running" and final.agent_id == "new" and final.tokens_in == 0
    assert (await db_session.execute(select(RunArtifact))).scalars().all() == []
    assert (await db_session.execute(select(RunEvent))).scalars().all() == []


async def test_checkpoint_revocation_preserves_last_committed_state(
    migrated_database: str, db_session: AsyncSession
) -> None:
    run_id, old_lease = await claimed(db_session)
    admin = RunRepository(db_session)
    await admin.mark_running(run_id, agent_id="old", deadline_at=None)
    config: RunnableConfig = {"configurable": {"thread_id": str(run_id), "checkpoint_ns": ""}}
    checkpoint = empty_checkpoint()
    async with lease_checkpointer(
        libpq_dsn(migrated_database), run_id=run_id, lease_id=old_lease
    ) as old:
        saved = await old.aput(config, checkpoint, {"source": "input", "step": -1}, {})
        await admin.requeue(run_id, reason="AGENT_HEARTBEAT_LOST")
        current = await admin.claim(dispatcher_id="recovery")
        assert current is not None and current.lease_id is not None
        new_lease = current.lease_id
        await admin.mark_running(run_id, agent_id="new", deadline_at=None)
        with pytest.raises(LeaseLostError):
            await old.aput_writes(saved, [("research", {"bad": "late"})], "old-task")
        with pytest.raises(LeaseLostError):
            await old.aput(config, empty_checkpoint(), {"source": "loop", "step": 2}, {})

    async with lease_checkpointer(
        libpq_dsn(migrated_database), run_id=run_id, lease_id=new_lease
    ) as new:
        recovered = await new.aget_tuple(config)
        assert recovered is not None
        assert recovered.checkpoint["id"] == checkpoint["id"]
        assert recovered.pending_writes == []
        await new.aput_writes(saved, [("research", {"good": "new"})], "new-task")
        recovered = await new.aget_tuple(config)
        assert recovered is not None and recovered.pending_writes
        assert recovered.pending_writes[0][0] == "new-task"


async def test_deadline_stops_a_hung_but_heartbeating_worker(
    db_session: AsyncSession, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    run_id, lease_id = await claimed(db_session)
    await db_session.execute(
        update(Run)
        .where(Run.id == run_id)
        .values(deadline_at=datetime.now(UTC) + timedelta(seconds=0.3))
    )
    await db_session.commit()
    stopped = asyncio.Event()

    async def hung(context: RunContext) -> RunOutcome:
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
        raise AssertionError("unreachable")

    executor = RunExecutor(
        db_sessionmaker, agent_id="test", slots=1, runner=hung, heartbeat_interval_seconds=0.02
    )
    await executor.execute(
        run_id,
        attempt=1,
        lease_id=lease_id,
        deadline_at=datetime.now(UTC) + timedelta(days=1),  # request cannot extend the DB deadline
    )
    await executor.wait_for(run_id, timeout=5)
    final = await RunRepository(db_session).require(run_id)
    assert stopped.is_set() and executor.slots_free == 1
    assert final.status == "failed" and final.error_code == "DEADLINE_EXCEEDED"
    events = (await db_session.execute(select(RunEvent).order_by(RunEvent.seq))).scalars().all()
    assert events[-1].event_type == "run_finished"


async def test_two_replicas_cannot_accept_the_same_lease(
    db_session: AsyncSession, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    run_id, lease_id = await claimed(db_session)
    release = asyncio.Event()

    async def delayed(context: RunContext) -> RunOutcome:
        await release.wait()
        return RunOutcome(status=RunStatus.SUCCEEDED)

    replicas = [
        RunExecutor(db_sessionmaker, agent_id=f"a{i}", slots=1, runner=delayed) for i in range(2)
    ]
    results = await asyncio.gather(
        *(e.execute(run_id, attempt=1, lease_id=lease_id) for e in replicas), return_exceptions=True
    )
    assert sum(result is None for result in results) == 1
    assert sum(isinstance(result, IllegalTransitionError) for result in results) == 1
    release.set()
    await asyncio.gather(*(e.wait_for(run_id, timeout=5) for e in replicas))


async def test_event_data_cannot_collide_with_logger_keyword_arguments(
    db_session: AsyncSession, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    run_id, lease_id = await claimed(db_session)
    await RunRepository(db_session).mark_running(run_id, agent_id="test", deadline_at=None)
    await EventWriter(db_sessionmaker, run_id=run_id, node="test", lease_id=lease_id).info(
        EventType.DECISION, "collision", data={"event_type": "nested", "run_id": "nested"}
    )
    event = (await db_session.execute(select(RunEvent))).scalar_one()
    assert event.data["event_type"] == "nested" and event.event_type == "decision"
