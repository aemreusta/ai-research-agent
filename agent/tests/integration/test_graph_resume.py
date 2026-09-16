"""Resume with the real Postgres checkpointer - the mechanism behind AGENT_HEARTBEAT_LOST recovery."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

import httpx
import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from research_agent.agent.research import Toolkit, build_deps, execute
from research_agent.agent.runtime import MemoryCache, MemoryCallRecorder, MemoryEventSink
from research_agent.agent.simulated import DEMO_CORPUS, simulated_llm
from research_agent.config.loader import load_settings
from research_agent.db.session import libpq_dsn
from research_agent.providers.fetch import ContentFetcher
from research_agent.providers.search.fake import FakeSearchProvider

pytestmark = pytest.mark.integration


class Crashing(FakeSearchProvider):
    armed = True

    async def search(self, request: Any, *, timeout: float) -> Any:
        if Crashing.armed:
            Crashing.armed = False
            raise RuntimeError("the agent process died here")
        return await super().search(request, timeout=timeout)


async def _attempt(run_id: uuid.UUID, dsn: str, *, resume: bool) -> tuple[Any, MemoryEventSink]:
    settings = load_settings().settings
    events = MemoryEventSink(run_id)
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(404)))
    toolkit = Toolkit(
        llm={"gemini": simulated_llm("gemini")},
        search={"tavily": Crashing("tavily", corpus=DEMO_CORPUS)},
        fetcher=ContentFetcher(settings.fetch, client=client),
    )
    deps = await build_deps(
        run_id=run_id,
        settings=settings,
        toolkit=toolkit,
        events=events,
        recorder=MemoryCallRecorder(),
        cache=MemoryCache(),
        today=date(2026, 9, 16),
    )
    async with AsyncPostgresSaver.from_conn_string(dsn) as checkpointer:
        await checkpointer.setup()
        state = await execute(
            deps,
            run_id=run_id,
            question="What is the EU AI Act timeline?",
            checkpointer=checkpointer,
            resume=resume,
        )
    return state, events


async def test_a_second_attempt_continues_from_the_postgres_checkpoint(
    migrated_database: str,
) -> None:
    dsn = libpq_dsn(migrated_database)
    run_id = uuid.uuid4()
    Crashing.armed = True
    with pytest.raises(RuntimeError, match="died"):
        await _attempt(run_id, dsn, resume=False)

    state, events = await _attempt(run_id, dsn, resume=True)
    assert state.report is not None and state.gate_result is not None
    started = [e["node"] for e in events.events if e["event_type"] == "node_started"]
    assert started[0] == "search", "the attempt resumes at the node that crashed"
    assert "plan" not in started
