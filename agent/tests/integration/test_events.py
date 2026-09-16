"""The event store is the primary trace (v0.6 §14), so its guarantees are worth pinning down."""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from research_agent.db.models import Run, RunEvent
from research_agent.db.session import libpq_dsn
from research_agent.errors import AgentError, ErrorCode
from research_agent.observability.events import (
    EVENTS_CHANNEL,
    QUEUE_CHANNEL,
    EventType,
    EventWriter,
    listen,
    notify_run_queued,
)

pytestmark = pytest.mark.integration


async def _make_run(session: AsyncSession) -> uuid.UUID:
    run = Run(
        question_masked="What changed in the EU AI Act timeline?",
        config_snapshot={"budget": {"max_iterations": 4}},
        config_hash="0" * 64,
    )
    session.add(run)
    await session.commit()
    return run.id


async def test_events_are_numbered_from_one_and_in_order(
    db_session: AsyncSession, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    run_id = await _make_run(db_session)
    writer = EventWriter(db_sessionmaker, run_id=run_id, node="planner")

    await writer.info(EventType.NODE_STARTED, "Created 4 research tasks.")
    await writer.info(EventType.NODE_FINISHED, "Planning done.")

    rows = (await db_session.execute(select(RunEvent).order_by(RunEvent.seq))).scalars().all()
    assert [row.seq for row in rows] == [1, 2]
    assert [row.message for row in rows] == ["Created 4 research tasks.", "Planning done."]
    assert {row.node for row in rows} == {"planner"}


async def test_concurrent_writers_never_reuse_a_sequence_number(
    db_session: AsyncSession, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """The agent and the dispatcher write to the same timeline at the same time."""
    run_id = await _make_run(db_session)
    writers = [EventWriter(db_sessionmaker, run_id=run_id, node=f"w{i}") for i in range(4)]

    await asyncio.gather(
        *(w.info(EventType.NODE_STARTED, f"from {w.node}") for w in writers for _ in range(5))
    )

    seqs = (await db_session.execute(select(RunEvent.seq).order_by(RunEvent.seq))).scalars().all()
    assert seqs == list(range(1, 21))


async def test_the_event_payload_is_redacted_before_it_is_stored(
    db_session: AsyncSession, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """Telemetry boundary B2 (v0.6 §12): a leaked key must not survive in the event store."""
    run_id = await _make_run(db_session)
    writer = EventWriter(db_sessionmaker, run_id=run_id, node="search")

    await writer.info(
        EventType.SEARCH_CALLED,
        "calling tavily with tvly-abcdefghijklmnopqrstuvwxyz012345",
        data={"api_key": "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789"},
    )

    event = (await db_session.execute(select(RunEvent))).scalars().one()
    assert "tvly-abcdefghijklmnopqrstuvwxyz012345" not in event.message
    assert "<TAVILY_API_KEY>" in event.message
    assert event.data["api_key"] == "<REDACTED>"


async def test_an_agent_error_becomes_an_auditable_event(
    db_session: AsyncSession, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """error -> decision -> outcome has to be readable off a single row (v0.6 §13)."""
    run_id = await _make_run(db_session)
    writer = EventWriter(db_sessionmaker, run_id=run_id, node="search", iteration=2)

    await writer.error(
        AgentError(
            code=ErrorCode.SEARCH_TIMEOUT,
            node="search",
            decision="fallback->brave",
            outcome="OK 7 results",
            provider="tavily",
            attempt=2,
        )
    )

    event = (await db_session.execute(select(RunEvent))).scalars().one()
    assert event.error_code == ErrorCode.SEARCH_TIMEOUT.value
    assert event.expected is True
    assert event.level == "warn"
    assert event.iteration == 2
    assert event.data["decision"] == "fallback->brave"
    assert event.data["outcome"] == "OK 7 results"
    assert "SEARCH_TIMEOUT" in event.message and "fallback->brave" in event.message


async def test_an_unexpected_error_is_logged_at_error_level(
    db_session: AsyncSession, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    run_id = await _make_run(db_session)
    writer = EventWriter(db_sessionmaker, run_id=run_id, node="synthesize")

    await writer.error(
        AgentError(
            code=ErrorCode.UNEXPECTED_EXCEPTION,
            node="synthesize",
            decision="fail the run",
            cause="Traceback (most recent call last): ...",
        )
    )

    event = (await db_session.execute(select(RunEvent))).scalars().one()
    assert event.level == "error"
    assert event.expected is False


async def test_writing_an_event_notifies_listeners(
    migrated_database: str,
    db_session: AsyncSession,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """SSE clients are woken by NOTIFY rather than polling (v0.6 §15.1)."""
    run_id = await _make_run(db_session)
    writer = EventWriter(db_sessionmaker, run_id=run_id, node="planner")

    async with listen(EVENTS_CHANNEL, dsn=libpq_dsn(migrated_database)) as notifications:
        await writer.info(EventType.NODE_STARTED, "Created 4 research tasks.")
        received = await asyncio.wait_for(anext(notifications), timeout=5)

    assert json.loads(received.payload) == {"run_id": str(run_id), "seq": 1}


async def test_queueing_a_run_notifies_the_dispatcher(
    migrated_database: str, db_session: AsyncSession
) -> None:
    run_id = await _make_run(db_session)

    async with listen(QUEUE_CHANNEL, dsn=libpq_dsn(migrated_database)) as notifications:
        await notify_run_queued(db_session, run_id)
        await db_session.commit()
        received = await asyncio.wait_for(anext(notifications), timeout=5)

    assert json.loads(received.payload) == {"run_id": str(run_id)}


async def test_the_case_log_format_is_preserved(
    db_session: AsyncSession, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """The case asks for `[Planner] Created 4 research tasks.` in the console and the UI."""
    run_id = await _make_run(db_session)
    writer = EventWriter(db_sessionmaker, run_id=run_id, node="plan")
    await writer.info(EventType.NODE_FINISHED, "Created 4 research tasks.", label="Planner")

    event = (await db_session.execute(select(RunEvent))).scalars().one()
    assert event.data["display"] == "[Planner] Created 4 research tasks."


async def test_events_survive_a_failing_caller(
    db_session: AsyncSession, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """An event written before a crash must still be there: it commits on its own connection."""
    run_id = await _make_run(db_session)
    writer = EventWriter(db_sessionmaker, run_id=run_id, node="search")

    async with db_sessionmaker() as doomed:
        await doomed.execute(text("SELECT 1"))
        await writer.info(EventType.SEARCH_CALLED, "query 1")
        await doomed.rollback()

    count = (await db_session.execute(select(RunEvent))).scalars().all()
    assert len(count) == 1
