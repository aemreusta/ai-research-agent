"""Brave Web Search `GET /web/search`: snippets only, so its hits go through the fetcher."""

from __future__ import annotations

import html
import re

import httpx

from research_agent.errors import ErrorCode
from research_agent.providers.search.base import (
    SearchHit,
    SearchProviderError,
    SearchRequest,
    classify_search_status,
    parse_date,
)

_TAGS = re.compile(r"<[^>]+>")


def _plain(text: str | None) -> str:
    return html.unescape(_TAGS.sub("", text or "")).strip()


class BraveProvider:
    name = "brave"

    def __init__(
        self,
        api_key: str,
        *,
        client: httpx.AsyncClient | None = None,
        base_url: str = "https://api.search.brave.com/res/v1",
        extra_snippets: bool = True,
    ) -> None:
        self._key = api_key
        self._client = client or httpx.AsyncClient()
        self._base = base_url.rstrip("/")
        self._extra = extra_snippets

    def __repr__(self) -> str:
        return "BraveProvider(<key hidden>)"

    async def search(self, request: SearchRequest, *, timeout: float) -> list[SearchHit]:
        params: dict[str, str | int] = {"q": request.query, "count": min(request.max_results, 20)}
        if request.language:
            params["search_lang"] = request.language
            if request.language == "tr":
                params["country"] = "TR"
        if self._extra:
            params["extra_snippets"] = "true"
        try:
            response = await self._client.get(
                f"{self._base}/web/search",
                params=params,
                headers={"X-Subscription-Token": self._key, "Accept": "application/json"},
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

        results = ((response.json().get("web") or {}).get("results")) or []
        hits = []
        for rank, item in enumerate(results, start=1):
            url = item.get("url")
            if not url:
                continue
            snippet = " ".join(
                _plain(part)
                for part in [item.get("description"), *(item.get("extra_snippets") or [])]
                if part
            )
            hits.append(
                SearchHit(
                    url=url,
                    title=_plain(item.get("title")),
                    snippet=snippet,
                    published_at=parse_date(item.get("page_age") or item.get("age")),
                    provider=self.name,
                    rank=rank,
                )
            )
        return hits
