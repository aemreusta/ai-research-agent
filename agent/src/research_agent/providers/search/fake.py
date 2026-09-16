"""A search provider over an in-memory corpus, for tests, scenarios and the key-less demo."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date

from pydantic import BaseModel

from research_agent.providers.search.base import SearchHit, SearchProviderError, SearchRequest


class CorpusPage(BaseModel):
    url: str
    title: str
    text: str
    published_at: date | None = None
    keywords: list[str] = []


def _tokens(text: str) -> set[str]:
    return {token for token in re.findall(r"\w+", text.lower()) if len(token) > 2}


class FakeSearchProvider:
    def __init__(
        self,
        name: str = "tavily",
        *,
        corpus: list[CorpusPage] | None = None,
        failures: list[SearchProviderError] | None = None,
        responder: Callable[[SearchRequest], list[SearchHit]] | None = None,
        include_content: bool = True,
    ) -> None:
        self.name = name
        self.corpus = corpus or []
        self._failures = list(failures or [])
        self._responder = responder
        self._include_content = include_content
        self.requests: list[SearchRequest] = []

    async def search(self, request: SearchRequest, *, timeout: float) -> list[SearchHit]:
        self.requests.append(request)
        if self._failures:
            raise self._failures.pop(0)
        if self._responder is not None:
            return self._responder(request)
        wanted = _tokens(request.query)
        scored = []
        for page in self.corpus:
            overlap = len(wanted & (_tokens(page.title + " " + page.text) | set(page.keywords)))
            if overlap:
                scored.append((overlap, page))
        scored.sort(key=lambda pair: (-pair[0], pair[1].url))
        return [
            SearchHit(
                url=page.url,
                title=page.title,
                snippet=page.text[:280],
                content=page.text if self._include_content else None,
                published_at=page.published_at,
                provider=self.name,
                rank=rank,
                provider_score=min(1.0, overlap / max(1, len(wanted))),
            )
            for rank, (overlap, page) in enumerate(scored[: request.max_results], start=1)
        ]
