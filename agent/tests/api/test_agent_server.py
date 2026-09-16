"""The data-plane HTTP surface, tested against the behaviour `agent-api.openapi.yaml` promises."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from research_agent.agent.runner import EchoGraphRunner, RunContext, RunOutcome
from research_agent.agent_server.app import create_agent_app
from research_agent.agent_server.executor import RunExecutor
from research_agent.config.loader import load_settings
from research_agent.contracts import RunStatus
from research_agent.db.models import RunArtifact, RunEvent
from research_agent.db.repository import RunRepository

pytestmark = pytest.mark.integration


async def _queued_and_claimed(session: AsyncSession, question: str = "Question?") -> uuid.UUID:
    """Runs arrive at the agent already claimed by the dispatcher."""
    repo = RunRepository(session)
    config = load_settings()
    run = await repo.create(
        question_masked=question,
        config_snapshot=config.snapshot,
        config_hash=config.config_hash,
    )
    await repo.claim(dispatcher_id="test-dispatcher")
    return run.id


def _executor(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    runner: object | None = None,
    slots: int = 2,
) -> RunExecutor:
    return RunExecutor(
        sessionmaker,
        agent_id="agent-test",
        slots=slots,
        runner=runner or EchoGraphRunner(),  # type: ignore[arg-type]
        heartbeat_interval_seconds=0.02,
    )


async def _client(executor: RunExecutor) -> AsyncIterator[httpx.AsyncClient]:
    app = create_agent_app(executor)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://agent") as client:
        yield client


async def test_healthz_reports_the_replica_identity(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    executor = _executor(db_sessionmaker)
    async for client in _client(executor):
        response = await client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["agent_id"] == "agent-test"
    assert body["version"]


async def test_capacity_counts_free_slots(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    executor = _executor(db_sessionmaker, slots=3)
    async for client in _client(executor):
        body = (await client.get("/capacity")).json()
    assert body["slots_total"] == 3
    assert body["slots_used"] == 0
    assert body["slots_free"] == 3


async def test_execute_accepts_and_runs_the_graph(
    db_session: AsyncSession, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    run_id = await _queued_and_claimed(db_session)
    executor = _executor(db_sessionmaker)

    async for client in _client(executor):
        response = await client.post(f"/v1/runs/{run_id}/execute", json={"attempt": 1})
        assert response.status_code == 202
        assert response.json()["accepted"] is True
        await executor.wait_for(run_id, timeout=10)

    run = await RunRepository(db_session).require(run_id)
    assert run.status == RunStatus.SUCCEEDED.value
    assert run.stop_reason == "sufficient"
    assert run.agent_id == "agent-test"
    assert run.finished_at is not None

    events = (await db_session.execute(select(RunEvent).order_by(RunEvent.seq))).scalars().all()
    assert events[0].event_type == "run_started"
    assert events[-1].event_type == "run_finished"

    artifact = (await db_session.execute(select(RunArtifact))).scalars().one()
    assert artifact.kind == "report_md"
    assert artifact.size_bytes == len(artifact.content.encode())


async def test_executing_the_same_run_twice_is_refused(
    db_session: AsyncSession, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """A dispatcher retry after a lost response must not start a second execution."""
    run_id = await _queued_and_claimed(db_session)
    executor = _executor(db_sessionmaker, runner=EchoGraphRunner(delay_seconds=0.2))

    async for client in _client(executor):
        first = await client.post(f"/v1/runs/{run_id}/execute", json={"attempt": 1})
        second = await client.post(f"/v1/runs/{run_id}/execute", json={"attempt": 1})
        assert first.status_code == 202
        assert second.status_code == 409
        assert second.json()["error_code"] == "DISPATCH_DEFERRED"
        await executor.wait_for(run_id, timeout=10)


async def test_execute_without_a_free_slot_is_503(
    db_session: AsyncSession, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    busy = await _queued_and_claimed(db_session, "busy")
    executor = _executor(db_sessionmaker, runner=EchoGraphRunner(delay_seconds=0.3), slots=1)

    async for client in _client(executor):
        assert (
            await client.post(f"/v1/runs/{busy}/execute", json={"attempt": 1})
        ).status_code == 202
        waiting = await _queued_and_claimed(db_session, "waiting")
        response = await client.post(f"/v1/runs/{waiting}/execute", json={"attempt": 1})
        assert response.status_code == 503
        assert response.json()["error_code"] == "DISPATCH_DEFERRED"
        await executor.wait_for(busy, timeout=10)


async def test_execute_for_an_unknown_run_is_404(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    executor = _executor(db_sessionmaker)
    async for client in _client(executor):
        response = await client.post(f"/v1/runs/{uuid.uuid4()}/execute", json={"attempt": 1})
    assert response.status_code == 404


async def test_cancel_stops_the_run_at_the_next_node_boundary(
    db_session: AsyncSession, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    run_id = await _queued_and_claimed(db_session)
    executor = _executor(db_sessionmaker, runner=EchoGraphRunner(delay_seconds=0.15, steps=20))

    async for client in _client(executor):
        await client.post(f"/v1/runs/{run_id}/execute", json={"attempt": 1})
        await asyncio.sleep(0.1)
        response = await client.post(f"/v1/runs/{run_id}/cancel", json={"reason": "user_requested"})
        assert response.status_code == 202
        assert response.json()["was_running"] is True
        await executor.wait_for(run_id, timeout=10)

    run = await RunRepository(db_session).require(run_id)
    assert run.status == RunStatus.CANCELLED.value
    assert run.stop_reason == "cancelled"


async def test_cancelling_a_run_this_replica_does_not_own_is_still_accepted(
    db_session: AsyncSession, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """The database flag is the source of truth, not the HTTP call."""
    run_id = await _queued_and_claimed(db_session)
    executor = _executor(db_sessionmaker)

    async for client in _client(executor):
        response = await client.post(f"/v1/runs/{run_id}/cancel", json={})
    assert response.status_code == 202
    assert response.json()["was_running"] is False
    assert await RunRepository(db_session).cancel_requested(run_id) is True


async def test_the_heartbeat_keeps_ticking_while_a_run_executes(
    db_session: AsyncSession, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """Without this the watchdog would requeue every run that takes longer than 30 seconds."""
    run_id = await _queued_and_claimed(db_session)
    executor = _executor(db_sessionmaker, runner=EchoGraphRunner(delay_seconds=0.12, steps=5))
    repo = RunRepository(db_session)

    async for client in _client(executor):
        await client.post(f"/v1/runs/{run_id}/execute", json={"attempt": 1})
        await asyncio.sleep(0.1)
        first = (await repo.require(run_id)).heartbeat_at
        await asyncio.sleep(0.2)
        second = (await repo.require(run_id)).heartbeat_at
        await executor.wait_for(run_id, timeout=10)

    assert first is not None and second is not None and second > first


async def test_a_bug_in_the_graph_fails_the_run_with_a_stack_trace(
    db_session: AsyncSession, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """Unexpected errors are the one case that must be loud (v0.6 §13.1)."""

    async def exploding(context: RunContext) -> RunOutcome:
        raise ZeroDivisionError("boom")

    run_id = await _queued_and_claimed(db_session)
    executor = _executor(db_sessionmaker, runner=exploding)

    async for client in _client(executor):
        await client.post(f"/v1/runs/{run_id}/execute", json={"attempt": 1})
        await executor.wait_for(run_id, timeout=10)

    run = await RunRepository(db_session).require(run_id)
    assert run.status == RunStatus.FAILED.value
    assert run.error_code == "UNEXPECTED_EXCEPTION"

    events = (await db_session.execute(select(RunEvent))).scalars().all()
    failure = next(event for event in events if event.error_code == "UNEXPECTED_EXCEPTION")
    assert failure.expected is False
    assert failure.level == "error"
    assert "ZeroDivisionError" in failure.data["cause"]


async def test_a_second_attempt_is_recorded_as_a_resume(
    db_session: AsyncSession, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    run_id = await _queued_and_claimed(db_session)
    executor = _executor(db_sessionmaker)

    async for client in _client(executor):
        await client.post(f"/v1/runs/{run_id}/execute", json={"attempt": 2})
        await executor.wait_for(run_id, timeout=10)

    events = (await db_session.execute(select(RunEvent).order_by(RunEvent.seq))).scalars().all()
    assert any(event.data.get("attempt") == 2 for event in events)


async def test_draining_marks_the_replica_unavailable(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Between SIGTERM and process exit the dispatcher must stop choosing this replica."""
    executor = _executor(db_sessionmaker)
    async for client in _client(executor):
        executor.begin_draining()
        response = await client.get("/healthz")
        assert response.status_code == 503
        assert response.json()["status"] == "draining"
        run_id = uuid.uuid4()
        assert (
            await client.post(f"/v1/runs/{run_id}/execute", json={"attempt": 1})
        ).status_code == 503
