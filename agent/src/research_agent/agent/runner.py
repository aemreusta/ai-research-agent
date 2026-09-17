"""The boundary between "run this research" and "how research is run".

`agent_server` and the CLI both hold a `GraphRunner`; neither knows what a claim ledger is. The
LangGraph implementation lands in Faz 3-4 and plugs in here, so the service layer can be built,
tested and shipped against `EchoGraphRunner` first - which is also what the end-to-end
infrastructure test uses (heartbeat, requeue, cancel) without spending a token.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from research_agent.config.schema import Settings
from research_agent.contracts import RunStatus
from research_agent.keys import ProviderKeys
from research_agent.observability.events import EventType, EventWriter


class CancelledByRequest(Exception):
    """Raised at a node boundary when `runs.cancel_requested` has been set."""


@dataclass(slots=True)
class RunContext:
    """Everything a run needs, and nothing about how it was scheduled."""

    run_id: uuid.UUID
    question: str
    settings: Settings
    keys: ProviderKeys
    events: EventWriter
    attempt: int = 1
    lease_id: uuid.UUID | None = None
    deadline_at: datetime | None = None
    # Polled between nodes rather than cancelling a task mid-write, so the claim ledger is never
    # left half-built (contracts/agent-api.openapi.yaml, `cancel`).
    is_cancelled: Callable[[], Awaitable[bool]] = field(default_factory=lambda: _never_cancelled)

    async def checkpoint(self) -> None:
        """Call at every node boundary. Raises `CancelledByRequest` if a cancel is pending."""
        if await self.is_cancelled():
            raise CancelledByRequest


async def _never_cancelled() -> bool:
    return False


@dataclass(slots=True)
class RunOutcome:
    """What the service layer writes to the run row when the graph returns."""

    status: RunStatus
    stop_reason: str | None = None
    gate_status: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    artifacts: dict[str, tuple[str, str]] = field(default_factory=dict)
    """`kind -> (content_type, content)`, stored in `run_artifacts`."""

    metrics: dict[str, Any] = field(default_factory=dict)


class GraphRunner(Protocol):
    async def __call__(self, context: RunContext) -> RunOutcome: ...


class EchoGraphRunner:
    """A runner that writes a few events and succeeds.

    Its job is to make the infrastructure testable on its own: the compose stack, the queue, the
    heartbeat, the watchdog and the SSE stream can all be proven correct before a single LLM call
    exists. `delay_seconds` lets a test hold a run "in flight" long enough to kill the agent.
    """

    def __init__(self, *, delay_seconds: float = 0.0, steps: int = 3) -> None:
        self._delay = delay_seconds
        self._steps = steps

    async def __call__(self, context: RunContext) -> RunOutcome:
        await context.events.info(
            EventType.NODE_STARTED,
            f"Echo runner starting for: {context.question}",
            label="Agent",
        )
        for step in range(1, self._steps + 1):
            await context.checkpoint()
            if self._delay:
                await asyncio.sleep(self._delay)
            await context.events.info(
                EventType.NODE_FINISHED,
                f"Step {step} of {self._steps} complete.",
                label="Echo",
                data={"step": step},
            )
        report = f"# Echo report\n\n{context.question}\n"
        return RunOutcome(
            status=RunStatus.SUCCEEDED,
            stop_reason="sufficient",
            gate_status="pass",
            artifacts={"report_md": ("text/markdown", report)},
        )
