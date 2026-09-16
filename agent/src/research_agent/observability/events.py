"""The run timeline: append-only rows in `run_events`, plus a NOTIFY so the UI sees them live.

Three properties this module exists to guarantee (architecture v0.6 §14):

1. **One timeline for two processes.** The Go dispatcher writes `node=dispatcher` rows into the
   same table, so a reviewer reads claim, dispatch, heartbeat loss and every agent decision in a
   single ordered list.
2. **Gap-free per-run ordering.** `seq` comes from a counter on the run row, not from a global
   sequence, because SSE resumes with `Last-Event-ID` and a client that reconnects must be able
   to ask for "everything after 41" and get it.
3. **Events outlive their caller.** Each write commits on its own connection. An event recorded
   just before a node raised is exactly the event you need afterwards, so it must not be rolled
   back along with the node's own work.

Everything written here passes through `redact` first: this is telemetry boundary B2 (§12).
"""

from __future__ import annotations

import json
import secrets
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from enum import StrEnum
from typing import Any, Final

import psycopg
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from research_agent.db.session import libpq_dsn
from research_agent.errors import AgentError
from research_agent.observability.logging import get_logger
from research_agent.observability.redaction import redact, redact_text

# The dispatcher LISTENs on the queue channel; SSE handlers LISTEN on the events channel.
QUEUE_CHANNEL: Final = "run_queued"
EVENTS_CHANNEL: Final = "run_events"

_logger = get_logger("events")


class EventLevel(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARN = "warn"
    ERROR = "error"


class EventType(StrEnum):
    """Well-known timeline entries.

    The UI groups and colours by this value, so it is a small closed vocabulary rather than free
    text. Node-specific detail belongs in `data`.
    """

    # lifecycle
    RUN_CREATED = "run_created"
    RUN_STARTED = "run_started"
    RUN_FINISHED = "run_finished"
    RUN_CANCELLED = "run_cancelled"
    ITERATION_STARTED = "iteration_started"
    NODE_STARTED = "node_started"
    NODE_FINISHED = "node_finished"
    # reasoning - each carries the model's `rationale` (v0.6 §5)
    DECISION = "decision"
    PLAN_CREATED = "plan_created"
    QUERIES_GENERATED = "queries_generated"
    SOURCE_SCORED = "source_scored"
    CLAIM_EXTRACTED = "claim_extracted"
    CONTRADICTION_FOUND = "contradiction_found"
    COVERAGE_ASSESSED = "coverage_assessed"
    SKILLS_ACTIVATED = "skills_activated"
    # calls
    LLM_CALLED = "llm_called"
    SEARCH_CALLED = "search_called"
    FETCH_CALLED = "fetch_called"
    # output
    SYNTHESIS_DONE = "synthesis_done"
    GATE_RESULT = "gate_result"
    REPORT_READY = "report_ready"
    # control plane (written by the Go dispatcher too)
    RUN_CLAIMED = "run_claimed"
    RUN_DISPATCHED = "run_dispatched"
    HEARTBEAT_LOST = "heartbeat_lost"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    DISPATCH_DEFERRED = "dispatch_deferred"
    # failures
    ERROR = "error"


def new_span_id() -> str:
    """A 64-bit span id in OpenTelemetry's hex form, so traces can be correlated later."""
    return secrets.token_hex(8)


def new_trace_id() -> str:
    return secrets.token_hex(16)


@asynccontextmanager
async def listen(channel: str, *, dsn: str | None = None) -> AsyncIterator[AsyncIterator[Any]]:
    """Subscribe to a NOTIFY channel on a dedicated connection.

    A listening connection cannot be pooled or shared - it sits idle waiting - so this opens its
    own. Used by the SSE handler to wake on new events and by the dispatcher's queue watcher.
    Notifications that arrive before the caller starts iterating are buffered by the server, so
    nothing between `LISTEN` and the first `async for` is lost.
    """
    connection = await psycopg.AsyncConnection.connect(dsn or libpq_dsn(), autocommit=True)
    try:
        await connection.execute(f"LISTEN {psycopg.sql.Identifier(channel).as_string(connection)}")
        yield connection.notifies()
    finally:
        await connection.close()


async def notify_run_queued(session: AsyncSession, run_id: uuid.UUID) -> None:
    """Wake the dispatcher.

    Delivered on commit, so the dispatcher never sees a run it cannot yet claim. Its 5-second
    poll is the backstop for a missed notification (v0.6 §2).
    """
    await session.execute(
        text("SELECT pg_notify(:channel, :payload)"),
        {"channel": QUEUE_CHANNEL, "payload": json.dumps({"run_id": str(run_id)})},
    )


# One statement: bump the run's counter, insert the row, return the sequence number. Taking the
# counter from the run row serialises writers per run - which is what makes `seq` gap-free - while
# runs stay independent of each other.
_INSERT_EVENT = text(
    """
    WITH next AS (
        UPDATE runs SET event_seq = event_seq + 1
        WHERE id = :run_id
        RETURNING event_seq
    )
    INSERT INTO run_events (
        run_id, seq, level, node, event_type, message, data, iteration,
        span_id, parent_span_id, latency_ms, tokens_in, tokens_out, cost_usd,
        error_code, expected
    )
    SELECT
        :run_id, next.event_seq, :level, :node, :event_type, :message, :data, :iteration,
        :span_id, :parent_span_id, :latency_ms, :tokens_in, :tokens_out, :cost_usd,
        :error_code, :expected
    FROM next
    RETURNING seq
    """
)


class EventWriter:
    """Writes one run's timeline. Cheap to copy with `child()` when entering a node."""

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        *,
        run_id: uuid.UUID,
        node: str,
        iteration: int | None = None,
        span_id: str | None = None,
        parent_span_id: str | None = None,
    ) -> None:
        self._sessionmaker = sessionmaker
        self.run_id = run_id
        self.node = node
        self.iteration = iteration
        self.span_id = span_id
        self.parent_span_id = parent_span_id

    def child(
        self,
        node: str,
        *,
        iteration: int | None = None,
        span_id: str | None = None,
    ) -> EventWriter:
        """A writer for a nested span: the current span becomes the parent."""
        return EventWriter(
            self._sessionmaker,
            run_id=self.run_id,
            node=node,
            iteration=self.iteration if iteration is None else iteration,
            span_id=span_id or new_span_id(),
            parent_span_id=self.span_id,
        )

    async def emit(
        self,
        event_type: EventType | str,
        message: str,
        *,
        level: EventLevel = EventLevel.INFO,
        data: dict[str, Any] | None = None,
        label: str | None = None,
        iteration: int | None = None,
        latency_ms: int | None = None,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
        cost_usd: float | None = None,
        error_code: str | None = None,
        expected: bool | None = None,
    ) -> int:
        """Store one event and return its sequence number."""
        payload: dict[str, Any] = dict(data or {})
        if label:
            # The console format the case asks for: `[Planner] Created 4 research tasks.`
            payload["display"] = f"[{label}] {message}"
        redacted_message = redact_text(message)
        redacted_data: dict[str, Any] = redact(payload)

        parameters = {
            "run_id": self.run_id,
            "level": level.value,
            "node": self.node,
            "event_type": str(event_type),
            "message": redacted_message,
            "data": json.dumps(redacted_data, ensure_ascii=False, default=str),
            "iteration": self.iteration if iteration is None else iteration,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "latency_ms": latency_ms,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost_usd": cost_usd,
            "error_code": error_code,
            "expected": expected,
        }
        async with self._sessionmaker() as session, session.begin():
            seq = (await session.execute(_INSERT_EVENT, parameters)).scalar_one()
            await session.execute(
                text("SELECT pg_notify(:channel, :payload)"),
                {
                    "channel": EVENTS_CHANNEL,
                    "payload": json.dumps({"run_id": str(self.run_id), "seq": seq}),
                },
            )

        _logger.info(
            str(event_type),
            run_id=str(self.run_id),
            span_id=self.span_id,
            node=self.node,
            seq=seq,
            message=redacted_message,
            **redacted_data,
        )
        return int(seq)

    async def debug(self, event_type: EventType | str, message: str, **kwargs: Any) -> int:
        return await self.emit(event_type, message, level=EventLevel.DEBUG, **kwargs)

    async def info(self, event_type: EventType | str, message: str, **kwargs: Any) -> int:
        return await self.emit(event_type, message, level=EventLevel.INFO, **kwargs)

    async def warn(self, event_type: EventType | str, message: str, **kwargs: Any) -> int:
        return await self.emit(event_type, message, level=EventLevel.WARN, **kwargs)

    async def error(self, error: AgentError, **kwargs: Any) -> int:
        """Record a coded failure as `error -> decision -> outcome` on one row (v0.6 §13).

        Expected failures are warnings: the system handled them and carried on. Only a bug
        (`expected=False`) is an error, and that one also fails the run.
        """
        message = f"{error.code.value}: {error.decision}"
        if error.outcome:
            message = f"{message} -> {error.outcome}"
        data: dict[str, Any] = {
            "decision": error.decision,
            "outcome": error.outcome,
            "category": error.category.value,
            "retryable": error.retryable,
            "provider": error.provider,
            "attempt": error.attempt,
            "cause": error.cause,
        }
        data.update(kwargs.pop("data", {}) or {})
        return await self.emit(
            EventType.ERROR,
            message,
            level=EventLevel.WARN if error.expected else EventLevel.ERROR,
            data=data,
            iteration=error.iteration,
            error_code=error.code.value,
            expected=error.expected,
            **kwargs,
        )
