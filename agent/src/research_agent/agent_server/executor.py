"""Owns the runs this replica is executing.

Responsibilities, in the order they matter:

1. **Slots.** One asyncio task per run, bounded by `slots`. When they are full the dispatcher is
   told `503` so it can choose another replica rather than queueing behind this one.
2. **The heartbeat.** A background task stamps `runs.heartbeat_at` every few seconds. If this
   process is killed the stamp goes stale and the dispatcher's watchdog requeues the run, which
   is the mechanism behind crash recovery (`AGENT_HEARTBEAT_LOST`).
3. **The terminal write.** Whatever happens - success, cancel, or a bug - exactly one terminal
   status is written, provider keys are destroyed with it, and a `run_finished` event closes the
   timeline. A run that silently stops existing is the failure mode this class exists to prevent.
4. **Cooperative cancellation.** The graph is asked to stop at a node boundary; the task is never
   cancelled mid-write, so a partially built claim ledger is never persisted.
"""

from __future__ import annotations

import asyncio
import contextlib
import traceback
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from research_agent import __version__
from research_agent.agent.runner import (
    CancelledByRequest,
    GraphRunner,
    RunContext,
    RunOutcome,
)
from research_agent.config.schema import Settings
from research_agent.contracts import RunStatus
from research_agent.db.models import RunArtifact
from research_agent.db.repository import RunRepository
from research_agent.errors import AgentError, AgentException, ErrorCode
from research_agent.keys import ProviderKeys, SecretBox
from research_agent.observability.events import EventType, EventWriter, new_span_id
from research_agent.observability.logging import bind_context, clear_context, get_logger
from research_agent.observability.redaction import redact_text

_logger = get_logger("agent_server")

# How long a cancelled or finishing run may take to wind down before the process gives up on it.
_SHUTDOWN_GRACE_SECONDS = 20.0


class NoCapacityError(AgentException):
    def __init__(self, run_id: uuid.UUID) -> None:
        super().__init__(
            AgentError(
                code=ErrorCode.DISPATCH_DEFERRED,
                node="agent_server",
                decision="reject the dispatch",
                outcome="no free slot on this replica",
                run_id=str(run_id),
            )
        )


class AlreadyExecutingError(AgentException):
    def __init__(self, run_id: uuid.UUID) -> None:
        super().__init__(
            AgentError(
                code=ErrorCode.DISPATCH_DEFERRED,
                node="agent_server",
                decision="reject the duplicate dispatch",
                outcome="this replica is already executing the run",
                run_id=str(run_id),
            )
        )


class RunExecutor:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        *,
        agent_id: str,
        slots: int,
        runner: GraphRunner,
        heartbeat_interval_seconds: float = 10.0,
        secret_box: SecretBox | None = None,
    ) -> None:
        self._sessionmaker = sessionmaker
        self.agent_id = agent_id
        self.slots = slots
        self._runner = runner
        self._heartbeat_interval = heartbeat_interval_seconds
        self._secret_box = secret_box
        self._tasks: dict[uuid.UUID, asyncio.Task[None]] = {}
        self._draining = False

    # --- state the dispatcher polls ------------------------------------------

    @property
    def draining(self) -> bool:
        return self._draining

    @property
    def slots_used(self) -> int:
        return len(self._tasks)

    @property
    def slots_free(self) -> int:
        return 0 if self._draining else max(0, self.slots - len(self._tasks))

    def running_run_ids(self) -> list[uuid.UUID]:
        return list(self._tasks)

    def health(self) -> dict[str, str]:
        return {
            "status": "draining" if self._draining else "ok",
            "agent_id": self.agent_id,
            "version": __version__,
        }

    def capacity(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "slots_total": self.slots,
            "slots_used": self.slots_used,
            "slots_free": self.slots_free,
            "running_run_ids": [str(run_id) for run_id in self._tasks],
        }

    # --- lifecycle -----------------------------------------------------------

    def begin_draining(self) -> None:
        """Stop accepting work. In-flight runs continue until they finish or are cancelled."""
        self._draining = True

    async def execute(
        self,
        run_id: uuid.UUID,
        *,
        attempt: int,
        deadline_at: Any = None,
        dispatcher_id: str | None = None,
    ) -> None:
        """Take ownership of a claimed run and start it in the background.

        Returns as soon as the run row says `running`, which is what makes the dispatcher's `202`
        meaningful: after this returns, the run is this replica's responsibility even if the HTTP
        response never arrives.
        """
        if run_id in self._tasks:
            raise AlreadyExecutingError(run_id)
        if self.slots_free <= 0:
            raise NoCapacityError(run_id)

        async with self._sessionmaker() as session:
            repository = RunRepository(session)
            await repository.require(run_id)
            await repository.mark_running(run_id, agent_id=self.agent_id, deadline_at=deadline_at)

        task = asyncio.create_task(
            self._execute(run_id, attempt=attempt, dispatcher_id=dispatcher_id),
            name=f"run-{run_id}",
        )
        self._tasks[run_id] = task
        task.add_done_callback(lambda _: self._tasks.pop(run_id, None))

    async def cancel(self, run_id: uuid.UUID, *, reason: str | None = None) -> bool:
        """Request a cooperative stop. Returns whether this replica owns the run."""
        async with self._sessionmaker() as session:
            await RunRepository(session).request_cancel(run_id)
        return run_id in self._tasks

    async def wait_for(self, run_id: uuid.UUID, *, timeout: float = 60.0) -> None:  # noqa: ASYNC109
        """Await one in-flight run. Used by shutdown and by tests; nothing polls in production."""
        task = self._tasks.get(run_id)
        if task is not None:
            async with asyncio.timeout(timeout):
                await asyncio.shield(task)

    async def drain(self) -> None:
        """Wait for in-flight runs on shutdown, then let the watchdog handle the remainder.

        Deliberately does not cancel the tasks: a half-written run is worse than a run the
        dispatcher requeues 30 seconds later from its checkpoint.
        """
        self.begin_draining()
        if not self._tasks:
            return
        pending = list(self._tasks.values())
        _logger.info("draining", runs=len(pending))
        with contextlib.suppress(TimeoutError):
            await asyncio.wait(pending, timeout=_SHUTDOWN_GRACE_SECONDS)

    # --- the run itself ------------------------------------------------------

    async def _execute(self, run_id: uuid.UUID, *, attempt: int, dispatcher_id: str | None) -> None:
        span_id = new_span_id()
        bind_context(run_id=str(run_id), span_id=span_id, agent_id=self.agent_id)
        events = EventWriter(self._sessionmaker, run_id=run_id, node="agent", span_id=span_id)
        heartbeat = asyncio.create_task(self._heartbeat(run_id), name=f"heartbeat-{run_id}")

        try:
            context = await self._build_context(run_id, attempt=attempt, events=events)
            await events.info(
                EventType.RUN_STARTED,
                (
                    f"Resuming run from checkpoint (attempt {attempt})."
                    if attempt > 1
                    else "Run started."
                ),
                label="Agent",
                data={"attempt": attempt, "agent_id": self.agent_id, "dispatcher": dispatcher_id},
            )
            outcome = await self._runner(context)
        except CancelledByRequest:
            outcome = RunOutcome(status=RunStatus.CANCELLED, stop_reason="cancelled")
            await events.warn(EventType.RUN_CANCELLED, "Run cancelled at a node boundary.")
        except AgentException as exc:
            await events.error(exc.error)
            outcome = RunOutcome(
                status=RunStatus.FAILED,
                error_code=exc.error.code.value,
                error_message=exc.error.outcome or exc.error.decision,
            )
        except Exception as exc:
            error = AgentError(
                code=ErrorCode.UNEXPECTED_EXCEPTION,
                node="agent",
                decision="fail the run",
                outcome=f"{type(exc).__name__}: {exc}",
                run_id=str(run_id),
                span_id=span_id,
                cause=redact_text(traceback.format_exc()),
            )
            await events.error(error)
            _logger.exception("run failed unexpectedly", **error.log_fields())
            outcome = RunOutcome(
                status=RunStatus.FAILED,
                error_code=error.code.value,
                error_message=error.outcome,
            )
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat

        await self._settle(run_id, outcome, events)
        clear_context()

    async def _build_context(
        self, run_id: uuid.UUID, *, attempt: int, events: EventWriter
    ) -> RunContext:
        async with self._sessionmaker() as session:
            repository = RunRepository(session)
            run = await repository.require(run_id)
            # The snapshot, not today's YAML: a config change mid-flight must not alter a run.
            settings = Settings.model_validate(run.config_snapshot)
            keys = (
                await repository.load_secrets(run_id, self._secret_box)
                if self._secret_box is not None
                else ProviderKeys.resolve()
            )
            question = run.question_masked

        async def is_cancelled() -> bool:
            async with self._sessionmaker() as session:
                return await RunRepository(session).cancel_requested(run_id)

        return RunContext(
            run_id=run_id,
            question=question,
            settings=settings,
            keys=keys,
            events=events,
            attempt=attempt,
            is_cancelled=is_cancelled,
        )

    async def _heartbeat(self, run_id: uuid.UUID) -> None:
        while True:
            await asyncio.sleep(self._heartbeat_interval)
            try:
                async with self._sessionmaker() as session:
                    await RunRepository(session).heartbeat(run_id)
            except Exception:
                _logger.warning("heartbeat failed", run_id=str(run_id))

    async def _settle(self, run_id: uuid.UUID, outcome: RunOutcome, events: EventWriter) -> None:
        """Write the terminal status, the artifacts and the closing event - in that order."""
        try:
            async with self._sessionmaker() as session:
                repository = RunRepository(session)
                for kind, (content_type, content) in outcome.artifacts.items():
                    session.add(
                        RunArtifact(
                            run_id=run_id,
                            kind=kind,
                            content_type=content_type,
                            content=content,
                            size_bytes=len(content.encode("utf-8")),
                        )
                    )
                if outcome.metrics:
                    await repository.update_counters(run_id, **outcome.metrics)
                await repository.finish(
                    run_id,
                    status=outcome.status,
                    stop_reason=outcome.stop_reason,
                    gate_status=outcome.gate_status,
                    error_code=outcome.error_code,
                    error_message=outcome.error_message,
                )
            await events.info(
                EventType.RUN_FINISHED,
                f"Run finished: {outcome.status.value}"
                + (f" ({outcome.stop_reason})" if outcome.stop_reason else ""),
                label="Agent",
                data={
                    "status": outcome.status.value,
                    "stop_reason": outcome.stop_reason,
                    "gate_status": outcome.gate_status,
                    "error_code": outcome.error_code,
                },
            )
        except Exception:
            # Losing the terminal write is the one failure the watchdog must clean up, so it is
            # logged loudly and left to the heartbeat going stale.
            _logger.exception("could not write the terminal status", run_id=str(run_id))
