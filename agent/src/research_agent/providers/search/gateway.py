"""`SearchGateway`: cache, retries, provider fallback and accounting for every search.

Budget note: each *logical* search counts once against `max_searches`, whether it was served
from cache or needed a fallback. That keeps the agent's decisions identical between a cold run
and a cached re-run of the same question - a reproducibility property, not an accident.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from research_agent.agent.budget import BudgetMeter
from research_agent.agent.runtime import Cache, CallRecorder, EventSink, SearchCallRecord, cache_key
from research_agent.config.schema import SearchSettings
from research_agent.errors import AgentError, ErrorCode
from research_agent.observability.events import EventType, new_span_id
from research_agent.observability.logging import best_effort
from research_agent.providers.search.base import (
    SearchHit,
    SearchProvider,
    SearchProviderError,
    SearchRequest,
)

Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class SearchOutcome:
    query: str
    hits: list[SearchHit]
    provider: str | None
    cache_hit: bool
    error_code: ErrorCode | None = None

    @property
    def failed(self) -> bool:
        return self.error_code is not None and self.error_code is not ErrorCode.SEARCH_EMPTY


class SearchGateway:
    def __init__(
        self,
        *,
        providers: dict[str, SearchProvider],
        chain: list[str],
        settings: SearchSettings,
        cache: Cache,
        recorder: CallRecorder,
        meter: BudgetMeter,
        cache_enabled: bool = True,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._chain = [name for name in chain if name in providers]
        self._providers = providers
        self._settings = settings
        self._cache = cache
        self._recorder = recorder
        self._meter = meter
        self._cache_enabled = cache_enabled
        self._sleep = sleep

    @property
    def available(self) -> list[str]:
        return list(self._chain)

    async def search(
        self,
        request: SearchRequest,
        *,
        events: EventSink,
        subquestion_id: str | None = None,
        preferred: str | None = None,
    ) -> SearchOutcome:
        """Run one logical search. Never raises: failure is an outcome with an error code."""
        self._meter.add_search()
        order = list(self._chain)
        if preferred is not None and preferred in order:
            order.remove(preferred)
            order.insert(0, preferred)
        if not order:
            await events.error(
                AgentError(
                    code=ErrorCode.SEARCH_AUTH,
                    node=events.node,
                    decision="skip query",
                    outcome="no search provider has a key - add Tavily or Brave in Settings",
                )
            )
            return SearchOutcome(request.query, [], None, False, ErrorCode.SEARCH_AUTH)

        last_code: ErrorCode = ErrorCode.SEARCH_PROVIDER_ERROR
        for index, name in enumerate(order):
            next_name = order[index + 1] if index + 1 < len(order) else None
            key = cache_key("search", *request.cache_parts(name))

            if self._cache_enabled:
                cached = await self._safe_cache_get(key)
                if cached is not None:
                    hits = [SearchHit.model_validate(hit) for hit in cached.get("hits", [])]
                    await self._record(
                        events, name, request, subquestion_id, "ok", len(hits), 0, True
                    )
                    if hits:
                        await self._announce(events, name, request.query, hits, cached=True)
                        return SearchOutcome(request.query, hits, name, True)

            try:
                hits, latency = await self._call(name, request, events, next_name)
            except SearchProviderError as exc:
                last_code = exc.code
                await self._record(
                    events, name, request, subquestion_id, "failed", 0, 0, False, exc.code
                )
                continue

            await self._record(
                events, name, request, subquestion_id, "ok", len(hits), latency, False
            )
            if self._cache_enabled:
                await self._safe_cache_put(key, name, request.query, hits)
            if hits:
                await self._announce(events, name, request.query, hits, cached=False)
                return SearchOutcome(request.query, hits, name, False)

            last_code = ErrorCode.SEARCH_EMPTY
            await events.error(
                AgentError(
                    code=ErrorCode.SEARCH_EMPTY,
                    node=events.node,
                    decision=f"try {next_name}" if next_name else "record no results",
                    outcome=f"0 results for {request.query!r}",
                    provider=name,
                )
            )

        return SearchOutcome(request.query, [], None, False, last_code)

    async def _call(
        self,
        name: str,
        request: SearchRequest,
        events: EventSink,
        next_name: str | None,
    ) -> tuple[list[SearchHit], int]:
        provider = self._providers[name]
        max_attempts = self._settings.retries + 1
        for attempt in range(1, max_attempts + 1):
            started = time.perf_counter()
            try:
                hits = await provider.search(request, timeout=float(self._settings.timeout_seconds))
                return hits, int((time.perf_counter() - started) * 1000)
            except SearchProviderError as exc:
                if exc.retryable and attempt < max_attempts:
                    delay = min(8.0, 1.0 * 2 ** (attempt - 1))
                    await events.error(
                        AgentError(
                            code=exc.code,
                            node=events.node,
                            decision=f"retry in {delay:.0f}s",
                            outcome=f"attempt {attempt}/{max_attempts}",
                            provider=name,
                            attempt=attempt,
                            cause=exc.message,
                        )
                    )
                    await self._sleep(delay)
                    continue
                decision = f"fallback->{next_name}" if next_name else "mark query FAILED"
                if exc.code is ErrorCode.SEARCH_AUTH:
                    decision = f"key rejected; {decision}"
                await events.error(
                    AgentError(
                        code=exc.code,
                        node=events.node,
                        decision=decision,
                        outcome=f"{name} gave up after {attempt} attempt(s)",
                        provider=name,
                        attempt=attempt,
                        cause=exc.message,
                    )
                )
                raise
        raise AssertionError("unreachable")  # pragma: no cover

    async def _announce(
        self, events: EventSink, provider: str, query: str, hits: list[SearchHit], *, cached: bool
    ) -> None:
        source = "cache" if cached else provider
        await events.info(
            EventType.SEARCH_CALLED,
            f"Received {len(hits)} results.",
            label="Search",
            data={
                "query": query,
                "provider": provider,
                "cache_hit": cached,
                "results": len(hits),
                "source": source,
                "top": [hit.url for hit in hits[:3]],
            },
        )

    async def _record(
        self,
        events: EventSink,
        provider: str,
        request: SearchRequest,
        subquestion_id: str | None,
        status: str,
        count: int,
        latency: int,
        cache_hit: bool,
        code: ErrorCode | None = None,
    ) -> None:
        with best_effort("recording a search call"):
            await self._recorder.search_call(
                SearchCallRecord(
                    run_id=events.run_id,
                    span_id=new_span_id(),
                    node=events.node,
                    iteration=events.iteration,
                    provider=provider,
                    query=request.query,
                    subquestion_id=subquestion_id,
                    status=status,
                    result_count=count,
                    latency_ms=latency,
                    cache_hit=cache_hit,
                    error_code=code.value if code else None,
                )
            )

    async def _safe_cache_get(self, key: str) -> dict[str, Any] | None:
        with best_effort("reading the search cache"):
            return await self._cache.get_response(key)
        return None

    async def _safe_cache_put(
        self, key: str, provider: str, query: str, hits: list[SearchHit]
    ) -> None:
        with best_effort("writing the search cache"):
            await self._cache.put_response(
                key,
                provider=provider,
                query=query,
                response={"hits": [hit.model_dump(mode="json") for hit in hits]},
                ttl_seconds=self._settings.cache_ttl_seconds,
            )
