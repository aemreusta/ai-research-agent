"""Tavily `POST /search`. Raw page content comes back in the same call when available (D7)."""

from __future__ import annotations

import httpx

from research_agent.errors import ErrorCode
from research_agent.providers.search.base import (
    SearchHit,
    SearchProviderError,
    SearchRequest,
    classify_search_status,
    parse_date,
)

_COUNTRIES = {"tr": "turkey", "de": "germany", "fr": "france", "en": None}
_MAX_CONTENT_CHARS = 60_000


class TavilyProvider:
    name = "tavily"

    def __init__(
        self,
        api_key: str,
        *,
        client: httpx.AsyncClient | None = None,
        base_url: str = "https://api.tavily.com",
        search_depth: str = "basic",
        include_raw_content: str | bool = "markdown",
    ) -> None:
        self._key = api_key
        self._client = client or httpx.AsyncClient()
        self._base = base_url.rstrip("/")
        self._depth = search_depth
        self._raw = include_raw_content

    def __repr__(self) -> str:
        return "TavilyProvider(<key hidden>)"

    async def search(self, request: SearchRequest, *, timeout: float) -> list[SearchHit]:
        body: dict[str, object] = {
            "query": request.query,
            "search_depth": self._depth,
            "max_results": request.max_results,
            "include_raw_content": self._raw,
            "include_answer": False,
            "topic": "general",
        }
        if country := _COUNTRIES.get(request.language or ""):
            body["country"] = country
        if request.start_date:
            body["start_date"] = request.start_date.isoformat()
        if request.end_date:
            body["end_date"] = request.end_date.isoformat()
        try:
            response = await self._client.post(
                f"{self._base}/search",
                json=body,
                headers={"Authorization": f"Bearer {self._key}"},
                timeout=timeout,
            )
        except httpx.TimeoutException as exc:
            raise SearchProviderError(
                ErrorCode.SEARCH_TIMEOUT, self.name, "timed out", True
            ) from exc
        except httpx.HTTPError as exc:
            raise SearchProviderError(
                ErrorCode.SEARCH_PROVIDER_ERROR, self.name, type(exc).__name__, True
            ) from exc
        if response.status_code >= 300:
            raise classify_search_status(self.name, response.status_code, response.text)

        hits = []
        for rank, item in enumerate(response.json().get("results") or [], start=1):
            url = item.get("url")
            if not url:
                continue
            raw = item.get("raw_content")
            hits.append(
                SearchHit(
                    url=url,
                    title=item.get("title") or "",
                    snippet=item.get("content") or "",
                    content=raw[:_MAX_CONTENT_CHARS] if isinstance(raw, str) and raw else None,
                    published_at=parse_date(item.get("published_date")),
                    provider=self.name,
                    rank=rank,
                    provider_score=item.get("score"),
                )
            )
        return hits
