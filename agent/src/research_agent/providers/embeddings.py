"""Claim embeddings for L3 clustering (D6).

One model per run: once a provider is chosen its vectors are the only ones compared, because
vectors from different models live in different spaces. If that provider fails mid-run the rest
of the run falls back to lexical similarity (`EMBEDDING_UNAVAILABLE`) instead of mixing spaces.
Vectors are cached by `sha256(model + text)`, so re-runs and resumed runs pay nothing.
"""

from __future__ import annotations

import time

from research_agent.agent.budget import BudgetMeter
from research_agent.agent.runtime import Cache, EventSink, cache_key
from research_agent.errors import AgentError, ErrorCode
from research_agent.observability.events import EventType
from research_agent.observability.logging import best_effort
from research_agent.providers.llm.base import LLMProvider, ProviderError
from research_agent.providers.llm.catalog import ModelCatalog, Tier

_BATCH = 64


class Embedder:
    def __init__(
        self,
        *,
        providers: dict[str, LLMProvider],
        catalog: ModelCatalog,
        cache: Cache,
        meter: BudgetMeter,
    ) -> None:
        self._catalog = catalog
        self._cache = cache
        self._meter = meter
        self._provider: LLMProvider | None = None
        self._model: str | None = None
        for name in catalog.chain:
            model = catalog.model_for(Tier.EMBEDDING, name)
            if name in providers and model:
                self._provider, self._model = providers[name], model
                break
        self.disabled_reason: str | None = None if self._provider else "no embedding provider"

    @property
    def model(self) -> str | None:
        return self._model if self.disabled_reason is None else None

    async def embed(self, texts: list[str], events: EventSink) -> dict[str, list[float]] | None:
        """Vectors keyed by text, or None when the run is on lexical similarity."""
        if self._provider is None or self._model is None or self.disabled_reason:
            return None
        unique = list(dict.fromkeys(texts))
        keys = {text: cache_key("embedding", self._model, text) for text in unique}
        cached: dict[str, list[float]] = {}
        with best_effort("reading the embedding cache"):
            cached = await self._cache.get_embeddings(list(keys.values()))
        vectors = {text: cached[key] for text, key in keys.items() if key in cached}
        missing = [text for text in unique if text not in vectors]

        started = time.perf_counter()
        fresh: dict[str, list[float]] = {}
        try:
            for start in range(0, len(missing), _BATCH):
                batch = missing[start : start + _BATCH]
                result = await self._provider.embed(
                    model=self._model, texts=batch, dimensions=self._catalog.embedding_dimensions
                )
                cost = self._catalog.cost(self._model, result.tokens_in, 0)
                self._meter.add_llm(tokens_in=result.tokens_in, tokens_out=0, cost_usd=cost)
                fresh.update(zip(batch, result.vectors, strict=True))
        except ProviderError as exc:
            self.disabled_reason = str(exc)
            await events.error(
                AgentError(
                    code=ErrorCode.EMBEDDING_UNAVAILABLE,
                    node=events.node,
                    decision="use lexical similarity for the rest of the run",
                    outcome="clustering continues without embeddings",
                    provider=self._provider.name,
                    cause=exc.message,
                )
            )
            return None

        if fresh:
            with best_effort("writing the embedding cache"):
                await self._cache.put_embeddings(
                    self._model, {keys[text]: vector for text, vector in fresh.items()}
                )
        vectors.update(fresh)
        if missing:
            await events.debug(
                EventType.LLM_CALLED,
                f"Embedded {len(missing)} texts with {self._model} "
                f"({len(unique) - len(missing)} from cache).",
                latency_ms=int((time.perf_counter() - started) * 1000),
                data={"model": self._model, "fresh": len(missing), "cached": len(cached)},
            )
        return vectors
