"""Server-sent events for the live run view.

The sequence matters more than the code here. A naive implementation queries the events table,
then subscribes - and loses everything written in between. So this subscribes first, backfills
second, and only then waits for notifications:

    LISTEN run_events  ->  backfill seq > last_event_id  ->  wait for NOTIFY  ->  drain  -> ...

`Last-Event-ID` is the browser's own `EventSource` reconnect header, so a dropped connection
resumes instead of replaying a whole run (architecture v0.6 §15.1). Postgres buffers
notifications for a listening connection, which is what makes the ordering above safe.

The stream ends when the run is terminal and the backlog is drained; a keepalive comment goes out
every few seconds so proxies do not close an idle connection mid-run.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from research_agent.contracts import RunStatus, run_state_machine
from research_agent.db.models import Run, RunEvent
from research_agent.observability.events import EVENTS_CHANNEL, listen

KEEPALIVE_SECONDS = 10.0
_BATCH = 200


def _frame(event: RunEvent) -> str:
    """One SSE frame. `id:` is what comes back as `Last-Event-ID` on reconnect."""
    payload = {
        "seq": event.seq,
        "ts": event.ts.isoformat(),
        "level": event.level,
        "node": event.node,
        "event_type": event.event_type,
        "message": event.message,
        "data": event.data,
        "iteration": event.iteration,
        "span_id": event.span_id,
        "parent_span_id": event.parent_span_id,
        "latency_ms": event.latency_ms,
        "tokens_in": event.tokens_in,
        "tokens_out": event.tokens_out,
        "cost_usd": float(event.cost_usd) if event.cost_usd is not None else None,
        "error_code": event.error_code,
        "expected": event.expected,
    }
    body = json.dumps(payload, ensure_ascii=False, default=str)
    return f"id: {event.seq}\nevent: run_event\ndata: {body}\n\n"


def _control(name: str, payload: dict[str, Any]) -> str:
    body = json.dumps(payload, ensure_ascii=False, default=str)
    return f"event: {name}\ndata: {body}\n\n"


async def _fetch(session: AsyncSession, run_id: uuid.UUID, after: int) -> list[RunEvent]:
    statement = (
        select(RunEvent)
        .where(RunEvent.run_id == run_id, RunEvent.seq > after)
        .order_by(RunEvent.seq)
        .limit(_BATCH)
    )
    return list((await session.execute(statement)).scalars().all())


async def _status(session: AsyncSession, run_id: uuid.UUID) -> str | None:
    return (await session.execute(select(Run.status).where(Run.id == run_id))).scalar_one_or_none()


async def stream_events(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    run_id: uuid.UUID,
    last_event_id: int = 0,
    dsn: str | None = None,
) -> AsyncIterator[str]:
    """Yield SSE frames for one run until it reaches a terminal state."""
    cursor = last_event_id
    machine = run_state_machine()
    status: str | None = None

    # Subscribe before reading, so nothing written during the backfill is lost.
    async with listen(EVENTS_CHANNEL, dsn=dsn) as notifications:
        while True:
            async with sessionmaker() as session:
                batch = await _fetch(session, run_id, cursor)
                status = await _status(session, run_id)
            for event in batch:
                cursor = event.seq
                yield _frame(event)
            if len(batch) < _BATCH:
                break

        if status is None:
            yield _control("error", {"error_code": "NOT_FOUND", "run_id": str(run_id)})
            return
        if machine.is_terminal(RunStatus(status)):
            yield _control("run_closed", {"run_id": str(run_id), "status": status})
            return

        while True:
            try:
                async with asyncio.timeout(KEEPALIVE_SECONDS):
                    await anext(notifications)
            except TimeoutError:
                # Proxies close idle connections; a comment frame is invisible to EventSource.
                yield ": keepalive\n\n"

            async with sessionmaker() as session:
                batch = await _fetch(session, run_id, cursor)
                status = await _status(session, run_id)
            for event in batch:
                cursor = event.seq
                yield _frame(event)

            if status is not None and machine.is_terminal(RunStatus(status)):
                yield _control("run_closed", {"run_id": str(run_id), "status": status})
                return
