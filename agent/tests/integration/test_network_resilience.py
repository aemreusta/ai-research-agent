"""Actual accepted TCP connections that stall, rather than connection-refused simulations."""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator

import httpx

from research_agent.agent.budget import BudgetMeter
from research_agent.agent.runtime import MemoryCache, MemoryCallRecorder, MemoryEventSink
from research_agent.config.schema import BudgetSettings, SearchSettings
from research_agent.errors import ErrorCode
from research_agent.providers.search.base import SearchRequest
from research_agent.providers.search.fake import CorpusPage, FakeSearchProvider
from research_agent.providers.search.gateway import SearchGateway
from research_agent.providers.search.tavily import TavilyProvider


@contextlib.asynccontextmanager
async def stalled_http() -> AsyncIterator[tuple[str, asyncio.Event]]:
    accepted = asyncio.Event()
    release = asyncio.Event()
    tasks: set[asyncio.Task[None]] = set()

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        assert task is not None
        tasks.add(task)
        try:
            await reader.read(4096)
            accepted.set()
            await release.wait()
        finally:
            writer.close()
            await writer.wait_closed()
            tasks.remove(task)

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    try:
        yield f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}", accepted
    finally:
        release.set()
        server.close()
        await server.wait_closed()
        await asyncio.gather(*tasks)


async def test_real_read_timeout_falls_back_and_records_the_correct_cause() -> None:
    async with stalled_http() as (url, accepted), httpx.AsyncClient() as client:
        primary = TavilyProvider("fixture-key", client=client, base_url=url)
        fallback = FakeSearchProvider(
            "brave",
            corpus=[
                CorpusPage(
                    url="https://example.org/research",
                    title="Research",
                    text="The research evidence is available.",
                )
            ],
        )
        events = MemoryEventSink(uuid.uuid4())
        recorder = MemoryCallRecorder()
        gateway = SearchGateway(
            providers={"tavily": primary, "brave": fallback},
            chain=["tavily", "brave"],
            settings=SearchSettings(timeout_seconds=1, retries=0),
            cache=MemoryCache(),
            recorder=recorder,
            meter=BudgetMeter(BudgetSettings()),
        )
        outcome = await asyncio.wait_for(
            gateway.search(SearchRequest("research"), events=events), 5
        )
        assert accepted.is_set(), "connection reached the server; this is a read timeout"
        assert outcome.provider == "brave" and outcome.hits
        assert any(e.get("error_code") == ErrorCode.SEARCH_TIMEOUT.value for e in events.events)
        assert len(recorder.search_calls) == 2
