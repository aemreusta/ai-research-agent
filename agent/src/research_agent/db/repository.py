"""Run lifecycle operations, with the state machine enforced on every write.

Two things here are load-bearing:

* **`claim()` is the queue.** `FOR UPDATE SKIP LOCKED` inside a single `UPDATE ... RETURNING`
  means several dispatchers can poll the same table and never hand the same run to two agents.
  That is the whole reason this system needs no broker (architecture v0.6 §2).
* **Transitions are checked against `contracts/run_states.yaml`,** not against whatever the
  caller believes. `mark_running` on a finished run raises rather than resurrecting it, which is
  what makes a late `execute` from a retrying dispatcher harmless.

Python owns the schema and the queue semantics; the Go dispatcher issues equivalent SQL against
the same contract, and a contract test keeps the two honest.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import delete, select, text, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from research_agent.contracts import RunStatus, run_state_machine
from research_agent.db.models import Run, RunArtifact, RunEvent, RunSecret
from research_agent.errors import AgentError, AgentException, ErrorCode, LeaseLostError, spec
from research_agent.keys import Provider, ProviderKeys, SecretBox
from research_agent.observability.events import notify_run_queued, notify_status_changed


class IllegalTransitionError(AgentException):
    """A write was attempted that the run state machine does not allow."""

    def __init__(self, run_id: uuid.UUID, source: str, target: RunStatus) -> None:
        super().__init__(
            AgentError(
                code=ErrorCode.UNEXPECTED_EXCEPTION,
                node="repository",
                decision="refuse the write",
                outcome=f"{source} -> {target.value} is not allowed by the run state machine",
                run_id=str(run_id),
            )
        )


class RunNotFoundError(AgentException):
    def __init__(self, run_id: uuid.UUID) -> None:
        super().__init__(
            AgentError(
                code=ErrorCode.UNEXPECTED_EXCEPTION,
                node="repository",
                decision="refuse the write",
                outcome="run does not exist",
                run_id=str(run_id),
            )
        )


# A single statement so the claim is atomic: pick the oldest queued run that nobody else has
# locked, mark it dispatched, and return it. Anything split across two statements would need a
# transaction the Go side cannot share.
_CLAIM = text(
    """
    UPDATE runs
       SET status = 'dispatched',
           dispatched_at = now(),
           attempts = attempts + 1,
           lease_id = gen_random_uuid(),
           agent_id = NULL
     WHERE id = (
            SELECT id FROM runs
             WHERE status = 'queued'
               AND (available_at IS NULL OR available_at <= now())
             ORDER BY created_at
               FOR UPDATE SKIP LOCKED
             LIMIT 1
           )
    RETURNING id
    """
)


class RunRepository:
    """Everything the api, the agent server and the tests need to do to a run row."""

    def __init__(self, session: AsyncSession, *, lease_id: uuid.UUID | None = None) -> None:
        self._session = session
        self._lease_id = lease_id

    def _owned(self, statement: Any) -> Any:
        """Worker repositories bind a lease; API/admin operations use the unbound repository."""
        if self._lease_id is not None:
            return statement.where(Run.lease_id == self._lease_id, Run.status == "running")
        return statement

    async def _write_owned(self, run_id: uuid.UUID, values: dict[str, Any]) -> None:
        result = await self._session.execute(
            self._owned(update(Run).where(Run.id == run_id)).values(**values)
        )
        if self._lease_id is not None and cast("CursorResult[Any]", result).rowcount != 1:
            await self._session.rollback()
            raise LeaseLostError(run_id)
        await self._session.commit()

    # --- reads ----------------------------------------------------------------------------------

    async def get(self, run_id: uuid.UUID) -> Run | None:
        return await self._session.get(Run, run_id, populate_existing=True)

    async def require(self, run_id: uuid.UUID) -> Run:
        run = await self.get(run_id)
        if run is None:
            raise RunNotFoundError(run_id)
        return run

    async def list_recent(self, *, limit: int = 50, offset: int = 0) -> Sequence[Run]:
        statement = select(Run).order_by(Run.created_at.desc()).limit(limit).offset(offset)
        return (await self._session.execute(statement)).scalars().all()

    async def cancel_requested(self, run_id: uuid.UUID) -> bool:
        """Checked at every node boundary, so it reads one column rather than the whole row."""
        statement = self._owned(select(Run.cancel_requested).where(Run.id == run_id))
        value = (await self._session.execute(statement)).scalar_one_or_none()
        if value is None and self._lease_id is not None:
            raise LeaseLostError(run_id)
        return bool(value)

    async def stale_runs(self, *, timeout_seconds: int) -> Sequence[Run]:
        """Running runs whose agent stopped reporting: the watchdog's input (v0.6 §2)."""
        cutoff = datetime.now(UTC) - timedelta(seconds=timeout_seconds)
        statement = select(Run).where(
            Run.status == RunStatus.RUNNING.value, Run.heartbeat_at < cutoff
        )
        return (await self._session.execute(statement)).scalars().all()

    async def overdue_runs(self) -> Sequence[Run]:
        """Runs past their hard deadline. The outer guarantee against a stuck agent."""
        now = datetime.now(UTC)
        statement = select(Run).where(
            Run.status.in_([RunStatus.DISPATCHED.value, RunStatus.RUNNING.value]),
            Run.deadline_at.is_not(None),
            Run.deadline_at < now,
        )
        return (await self._session.execute(statement)).scalars().all()

    # --- writes ---------------------------------------------------------------------------------

    async def create(
        self,
        *,
        question_masked: str,
        config_snapshot: dict[str, Any],
        config_hash: str,
        overrides: dict[str, Any] | None = None,
        question_language: str | None = None,
        key_sources: dict[str, str] | None = None,
        deadline_at: datetime | None = None,
    ) -> Run:
        """Insert a queued run and wake the dispatcher on commit."""
        run = Run(
            question_masked=question_masked,
            question_language=question_language,
            config_snapshot=config_snapshot,
            config_hash=config_hash,
            overrides=overrides or {},
            key_sources=key_sources or {},
            deadline_at=deadline_at,
            status=RunStatus.QUEUED.value,
        )
        self._session.add(run)
        await self._session.flush()
        await notify_run_queued(self._session, run.id)
        await self._session.commit()
        return run

    async def create_local(
        self,
        *,
        question_masked: str,
        config_snapshot: dict[str, Any],
        config_hash: str,
        overrides: dict[str, Any] | None = None,
        key_sources: dict[str, str] | None = None,
    ) -> Run:
        """A run executed in-process by the CLI: recorded as running, never queued.

        It skips the queue on purpose - otherwise the dispatcher would hand the same question to
        an agent as well. It still ends through `finish()`, so the state machine applies.
        """
        now = datetime.now(UTC)
        run = Run(
            question_masked=question_masked,
            config_snapshot=config_snapshot,
            config_hash=config_hash,
            overrides=overrides or {},
            key_sources=key_sources or {},
            status=RunStatus.RUNNING.value,
            agent_id="cli",
            lease_id=uuid.uuid4(),
            attempts=1,
            started_at=now,
            heartbeat_at=now,
            dispatched_at=now,
        )
        self._session.add(run)
        await self._session.commit()
        return run

    async def claim(self, *, dispatcher_id: str) -> Run | None:
        """Take the oldest queued run, or return None when the queue is empty."""
        run_id = (await self._session.execute(_CLAIM)).scalar_one_or_none()
        await self._session.commit()
        if run_id is None:
            return None
        return await self.get(run_id)

    async def mark_running(
        self, run_id: uuid.UUID, *, agent_id: str, deadline_at: datetime | None
    ) -> Run:
        """The agent takes ownership: from here it owes a heartbeat and a terminal status."""
        now = datetime.now(UTC)
        values: dict[str, Any] = {
            "agent_id": agent_id,
            "started_at": now,
            "heartbeat_at": now,
        }
        if deadline_at is not None:
            values["deadline_at"] = deadline_at
        return await self._transition(run_id, RunStatus.RUNNING, values)

    async def heartbeat(self, run_id: uuid.UUID) -> None:
        """Not a transition: a liveness stamp, written every few seconds while a run executes."""
        await self._write_owned(run_id, {"heartbeat_at": datetime.now(UTC)})

    async def update_counters(
        self,
        run_id: uuid.UUID,
        *,
        iteration: int | None = None,
        searches_used: int | None = None,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
        cost_usd: float | None = None,
    ) -> None:
        """Live counters for the UI. They run whether or not the budget gates are on (D11)."""
        values = {
            key: value
            for key, value in {
                "iteration": iteration,
                "searches_used": searches_used,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "cost_usd": cost_usd,
            }.items()
            if value is not None
        }
        if not values:
            return
        await self._write_owned(run_id, values)

    async def update_metadata(
        self,
        run_id: uuid.UUID,
        *,
        prompt_versions: dict[str, Any] | None = None,
        models_used: dict[str, Any] | None = None,
        skills_used: list[str] | None = None,
        langfuse_trace_url: str | None = None,
    ) -> None:
        """Reproducibility metadata, written as soon as it is known (v0.6 §3)."""
        values: dict[str, Any] = {
            key: value
            for key, value in {
                "prompt_versions": prompt_versions,
                "models_used": models_used,
                "skills_used": skills_used,
                "langfuse_trace_url": langfuse_trace_url,
            }.items()
            if value is not None
        }
        if values:
            await self._write_owned(run_id, values)

    async def finish(
        self,
        run_id: uuid.UUID,
        *,
        status: RunStatus,
        stop_reason: str | None = None,
        gate_status: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        artifacts: dict[str, tuple[str, str]] | None = None,
        metrics: dict[str, Any] | None = None,
        emit_finished: bool = False,
    ) -> Run:
        """Publish status, artifacts, counters and the final event in one fenced transaction."""
        ready = bool(artifacts and "report_md" in artifacts and emit_finished)
        counters = dict(metrics or {})
        if set(counters) - {"iteration", "searches_used", "tokens_in", "tokens_out", "cost_usd"}:
            raise ValueError("unknown run counter")

        async def persist() -> None:
            await self._session.execute(delete(RunSecret).where(RunSecret.run_id == run_id))
            for kind, (content_type, content) in (artifacts or {}).items():
                self._session.add(
                    RunArtifact(
                        run_id=run_id,
                        kind=kind,
                        content_type=content_type,
                        content=content,
                        size_bytes=len(content.encode("utf-8")),
                    )
                )
            if emit_finished:
                seq = (
                    await self._session.execute(select(Run.event_seq).where(Run.id == run_id))
                ).scalar_one()
                if ready:
                    self._session.add(
                        RunEvent(
                            run_id=run_id,
                            seq=seq - 1,
                            level="info",
                            node="agent",
                            event_type="report_ready",
                            message="Report ready.",
                            data={"display": "[Agent] Report ready.", "gate_status": gate_status},
                        )
                    )
                message = f"Run finished: {status.value}" + (
                    f" ({stop_reason})" if stop_reason else ""
                )
                self._session.add(
                    RunEvent(
                        run_id=run_id,
                        seq=seq,
                        level="info",
                        node="agent",
                        event_type="run_finished",
                        message=message,
                        error_code=error_code,
                        expected=spec(ErrorCode(error_code)).expected if error_code else None,
                        data={
                            "display": f"[Agent] {message}",
                            "status": status.value,
                            "stop_reason": stop_reason,
                            "gate_status": gate_status,
                            "error_code": error_code,
                        },
                    )
                )

        run = await self._transition(
            run_id,
            status,
            {
                **counters,
                **({"event_seq": Run.event_seq + 1 + int(ready)} if emit_finished else {}),
                "stop_reason": stop_reason,
                "gate_status": gate_status,
                "error_code": error_code,
                "error_message": error_message,
                "finished_at": datetime.now(UTC),
            },
            also=persist,
        )
        return run

    async def requeue(self, run_id: uuid.UUID, *, reason: str, backoff_seconds: float = 0) -> bool:
        """Send a run back to the queue after a lost heartbeat.

        Returns True when it was requeued and False when the attempt budget from the contract is
        spent, in which case the run is failed with `reason` as its error code.
        """
        run = await self.require(run_id)
        if run.attempts >= run_state_machine().max_attempts:
            await self.finish(
                run_id,
                status=RunStatus.FAILED,
                error_code=reason,
                error_message=f"giving up after {run.attempts} attempts",
            )
            return False
        await self._transition(
            run_id,
            RunStatus.QUEUED,
            {
                "agent_id": None,
                "lease_id": None,
                "heartbeat_at": None,
                "dispatched_at": None,
                "available_at": datetime.now(UTC) + timedelta(seconds=backoff_seconds),
            },
        )
        await notify_run_queued(self._session, run_id)
        await self._session.commit()
        return True

    async def request_cancel(self, run_id: uuid.UUID) -> None:
        """Set the flag the agent polls between nodes. The flag is the source of truth."""
        await self._session.execute(
            update(Run).where(Run.id == run_id).values(cancel_requested=True)
        )
        await self._session.commit()

    # --- provider keys --------------------------------------------------------------------------

    async def store_secrets(self, run_id: uuid.UUID, ciphertexts: dict[Provider, str]) -> None:
        """Write Fernet ciphertexts that expire with the run; deleted again in `finish`."""
        if not ciphertexts:
            return
        expires_at = datetime.now(UTC) + timedelta(hours=6)
        self._session.add_all(
            [
                RunSecret(
                    run_id=run_id,
                    provider=provider.value,
                    ciphertext=ciphertext,
                    expires_at=expires_at,
                )
                for provider, ciphertext in ciphertexts.items()
            ]
        )
        await self._session.commit()

    async def load_secrets(self, run_id: uuid.UUID, box: SecretBox) -> ProviderKeys:
        """Decrypt this run's keys and fall back to the environment for anything missing."""
        rows = (
            (await self._session.execute(select(RunSecret).where(RunSecret.run_id == run_id)))
            .scalars()
            .all()
        )
        return ProviderKeys.from_ciphertexts(
            box, {Provider(row.provider): row.ciphertext for row in rows}
        )

    async def purge_expired_secrets(self) -> int:
        """Backstop for a run that died without finishing cleanly."""
        statement = delete(RunSecret).where(RunSecret.expires_at < datetime.now(UTC))
        result = await self._session.execute(statement)
        await self._session.commit()
        # `rowcount` lives on the cursor result a DELETE actually returns; the static type is
        # the general `Result`, which does not know that.
        return int(cast("CursorResult[Any]", result).rowcount or 0)

    # --- internals ------------------------------------------------------------------------------

    async def _transition(
        self,
        run_id: uuid.UUID,
        target: RunStatus,
        values: dict[str, Any],
        *,
        also: Any = None,
    ) -> Run:
        """Move a run to `target` if, and only if, nobody moved it first.

        The update is conditional on the status the check was made against (compare-and-set),
        because the agent and the dispatcher's watchdog act on the same row concurrently. A
        plain read-then-write would let a late `finish` overwrite a requeue, or the reverse.
        """
        run = await self.require(run_id)
        source = RunStatus(run.status)
        if self._lease_id is not None and run.lease_id != self._lease_id:
            raise LeaseLostError(run_id)
        if not run_state_machine().can_transition(source, target):
            raise IllegalTransitionError(run_id, run.status, target)
        statement = update(Run).where(Run.id == run_id, Run.status == source.value)
        if self._lease_id is not None:
            statement = statement.where(Run.lease_id == self._lease_id)
        result = await self._session.execute(statement.values(status=target.value, **values))
        if cast("CursorResult[Any]", result).rowcount != 1:
            await self._session.rollback()
            current = await self.require(run_id)
            raise IllegalTransitionError(run_id, current.status, target)
        if also is not None:
            await also()
        # Status changes wake SSE listeners too; otherwise a run cancelled from the queue would
        # leave its open stream waiting for the next keepalive before it noticed.
        await notify_status_changed(self._session, run_id, target.value)
        await self._session.commit()
        return await self.require(run_id)
