"""The run repository is where the queue semantics live, so the tests are about concurrency."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from research_agent.contracts import RunStatus
from research_agent.db.models import Run, RunSecret
from research_agent.db.repository import IllegalTransitionError, RunRepository
from research_agent.keys import Provider, ProviderKeys, SecretBox

pytestmark = pytest.mark.integration

FERNET = Fernet.generate_key().decode()


async def _create(repo: RunRepository, question: str = "Question?") -> uuid.UUID:
    run = await repo.create(
        question_masked=question,
        config_snapshot={"budget": {"max_iterations": 4}},
        config_hash="a" * 64,
    )
    return run.id


async def test_a_created_run_is_queued_and_waiting(db_session: AsyncSession) -> None:
    repo = RunRepository(db_session)
    run_id = await _create(repo)
    run = await repo.get(run_id)
    assert run is not None
    assert run.status == RunStatus.QUEUED.value
    assert run.attempts == 0
    assert run.event_seq == 0


async def test_claim_takes_the_oldest_queued_run(db_session: AsyncSession) -> None:
    repo = RunRepository(db_session)
    first = await _create(repo, "first")
    await _create(repo, "second")

    claimed = await repo.claim(dispatcher_id="d1")
    assert claimed is not None
    assert claimed.id == first
    assert claimed.status == RunStatus.DISPATCHED.value
    assert claimed.attempts == 1


async def test_claim_returns_none_on_an_empty_queue(db_session: AsyncSession) -> None:
    assert await RunRepository(db_session).claim(dispatcher_id="d1") is None


async def test_two_dispatchers_never_claim_the_same_run(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """`FOR UPDATE SKIP LOCKED` is the whole reason there is no broker (v0.6 §2)."""
    async with db_sessionmaker() as setup:
        repo = RunRepository(setup)
        ids = {await _create(repo, f"q{i}") for i in range(6)}

    async def claim_all(dispatcher_id: str) -> list[uuid.UUID]:
        taken = []
        async with db_sessionmaker() as session:
            repo = RunRepository(session)
            while (run := await repo.claim(dispatcher_id=dispatcher_id)) is not None:
                taken.append(run.id)
        return taken

    results = await asyncio.gather(*(claim_all(f"d{i}") for i in range(3)))
    claimed = [run_id for batch in results for run_id in batch]
    assert sorted(claimed) == sorted(ids)
    assert len(claimed) == len(set(claimed)), "a run was claimed twice"


async def test_the_agent_takes_ownership_and_heartbeats(db_session: AsyncSession) -> None:
    repo = RunRepository(db_session)
    run_id = await _create(repo)
    await repo.claim(dispatcher_id="d1")

    await repo.mark_running(run_id, agent_id="agent-1", deadline_at=None)
    run = await repo.get(run_id)
    assert run is not None and run.status == RunStatus.RUNNING.value
    assert run.agent_id == "agent-1"
    assert run.started_at is not None and run.heartbeat_at is not None

    before = run.heartbeat_at
    await asyncio.sleep(0.01)
    await repo.heartbeat(run_id)
    run = await repo.get(run_id)
    assert run is not None and run.heartbeat_at is not None and run.heartbeat_at > before


async def test_an_illegal_transition_is_refused(db_session: AsyncSession) -> None:
    """The contract, not the caller, decides what may follow what."""
    repo = RunRepository(db_session)
    run_id = await _create(repo)
    with pytest.raises(IllegalTransitionError):
        await repo.mark_running(run_id, agent_id="agent-1", deadline_at=None)


async def test_a_finished_run_cannot_be_restarted(db_session: AsyncSession) -> None:
    repo = RunRepository(db_session)
    run_id = await _create(repo)
    await repo.claim(dispatcher_id="d1")
    await repo.mark_running(run_id, agent_id="agent-1", deadline_at=None)
    await repo.finish(run_id, status=RunStatus.SUCCEEDED, stop_reason="sufficient")

    with pytest.raises(IllegalTransitionError):
        await repo.mark_running(run_id, agent_id="agent-2", deadline_at=None)


async def test_finishing_a_run_deletes_its_provider_keys(db_session: AsyncSession) -> None:
    """Keys live exactly as long as the run does (v0.6 §15.3)."""
    repo = RunRepository(db_session)
    run_id = await _create(repo)
    box = SecretBox(FERNET)
    await repo.store_secrets(
        run_id, ProviderKeys.resolve({Provider.TAVILY: "tvly-abcdefgh"}).encrypted(box)
    )
    assert (await db_session.execute(select(RunSecret))).scalars().all()

    await repo.claim(dispatcher_id="d1")
    await repo.mark_running(run_id, agent_id="agent-1", deadline_at=None)
    await repo.finish(run_id, status=RunStatus.FAILED, error_code="LLM_AUTH")

    assert (await db_session.execute(select(RunSecret))).scalars().all() == []


async def test_stored_keys_round_trip_through_the_database(db_session: AsyncSession) -> None:
    repo = RunRepository(db_session)
    run_id = await _create(repo)
    box = SecretBox(FERNET)
    await repo.store_secrets(
        run_id, ProviderKeys.resolve({Provider.TAVILY: "tvly-abcdefgh"}).encrypted(box)
    )

    loaded = await repo.load_secrets(run_id, box)
    assert loaded.get(Provider.TAVILY) == "tvly-abcdefgh"


async def test_requeue_after_a_lost_heartbeat_respects_max_attempts(
    db_session: AsyncSession,
) -> None:
    """Two requeues, then the run fails: `max_attempts` in the contract is the budget."""
    repo = RunRepository(db_session)
    run_id = await _create(repo)

    for _ in range(3):
        claimed = await repo.claim(dispatcher_id="d1")
        assert claimed is not None
        await repo.mark_running(run_id, agent_id="agent-1", deadline_at=None)
        assert await repo.requeue(run_id, reason="AGENT_HEARTBEAT_LOST") is (claimed.attempts < 3)

    run = await repo.get(run_id)
    assert run is not None
    assert run.status == RunStatus.FAILED.value
    assert run.error_code == "AGENT_HEARTBEAT_LOST"
    assert run.attempts == 3


async def test_stale_runs_are_the_ones_whose_heartbeat_lapsed(db_session: AsyncSession) -> None:
    repo = RunRepository(db_session)
    fresh = await _create(repo, "fresh")
    stale = await _create(repo, "stale")
    for run_id in (fresh, stale):
        await repo.claim(dispatcher_id="d1")
        await repo.mark_running(run_id, agent_id="agent-1", deadline_at=None)

    await db_session.execute(
        update(Run)
        .where(Run.id == stale)
        .values(heartbeat_at=datetime.now(UTC) - timedelta(seconds=120))
    )
    await db_session.commit()

    assert [run.id for run in await repo.stale_runs(timeout_seconds=30)] == [stale]


async def test_a_cancel_request_is_visible_to_the_agent(db_session: AsyncSession) -> None:
    repo = RunRepository(db_session)
    run_id = await _create(repo)
    assert await repo.cancel_requested(run_id) is False

    await repo.request_cancel(run_id)
    assert await repo.cancel_requested(run_id) is True


async def test_cancelling_a_queued_run_settles_it_immediately(db_session: AsyncSession) -> None:
    """A run that never reached an agent has nobody to stop it, so the API finishes it."""
    repo = RunRepository(db_session)
    run_id = await _create(repo)
    await repo.request_cancel(run_id)
    await repo.finish(run_id, status=RunStatus.CANCELLED, stop_reason="cancelled")

    run = await repo.get(run_id)
    assert run is not None and run.status == RunStatus.CANCELLED.value
    assert run.finished_at is not None
