"""Which `GraphRunner` the agent service runs.

`graph` (the default) is the research agent. `echo` keeps the infrastructure testable without
any provider: queue, heartbeat, watchdog and SSE can be proven end to end with it.
"""

from __future__ import annotations

import os

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from research_agent.agent.runner import EchoGraphRunner, GraphRunner


def build_runner(
    name: str | None = None,
    *,
    sessionmaker: async_sessionmaker[AsyncSession] | None = None,
    dsn: str | None = None,
) -> GraphRunner:
    choice = (name or os.environ.get("AGENT_RUNNER") or "graph").lower()
    if choice == "echo":
        delay = float(os.environ.get("AGENT_ECHO_DELAY_SECONDS", "0.5"))
        return EchoGraphRunner(delay_seconds=delay)
    if choice == "graph":
        if sessionmaker is None or dsn is None:
            raise ValueError("the graph runner needs a database (sessionmaker and dsn)")
        from research_agent.agent.research import ResearchGraphRunner
        from research_agent.observability.langfuse import langfuse_toolkit_factory
        from research_agent.services import build_masker

        return ResearchGraphRunner(
            sessionmaker,
            dsn=dsn,
            masker=build_masker(),
            toolkit_factory=langfuse_toolkit_factory(),
        )
    raise ValueError(f"unknown AGENT_RUNNER {choice!r}; expected 'graph' or 'echo'")
