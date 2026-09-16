"""Full-text fetch for the top-K documents that survived snippet triage (D7).

Only called when the search provider did not already return the page text. Bounded in every
direction: a timeout, one retry, a byte cap, HTML and plain text only. Failure is not an error
for the run - the document continues with its snippet (`FETCH_FAILED`).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date

import httpx
import trafilatura

from research_agent.agent.runtime import Cache, cache_key
from research_agent.config.schema import FetchSettings
from research_agent.observability.logging import best_effort
from research_agent.providers.search.base import parse_date

_USER_AGENT = "research-agent/0.1 (+https://github.com/aemreusta/apilex-test-case)"
_TEXTUAL = ("text/html", "application/xhtml+xml", "text/plain")


@dataclass(frozen=True, slots=True)
class FetchedPage:
    url: str
    ok: bool
    text: str = ""
    title: str | None = None
    published_at: date | None = None
    error: str | None = None


class ContentFetcher:
    def __init__(
        self,
        settings: FetchSettings,
        *,
        cache: Cache | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._cache = cache
        self._client = client or httpx.AsyncClient(
            follow_redirects=True, headers={"User-Agent": _USER_AGENT}
        )

    async def fetch(self, url: str) -> FetchedPage:
        key = cache_key("fetch", url)
        if self._cache is not None:
            cached = None
            with best_effort("reading the fetch cache"):
                cached = await self._cache.get_response(key)
            if cached:
                return FetchedPage(
                    url=url,
                    ok=True,
                    text=str(cached.get("text", "")),
                    title=cached.get("title"),
                    published_at=parse_date(cached.get("published_at")),
                )

        last_error = "unknown"
        for attempt in range(self._settings.retries + 1):
            try:
                page = await self._fetch_once(url)
            except (httpx.HTTPError, ValueError) as exc:
                last_error = type(exc).__name__
                if attempt < self._settings.retries:
                    await asyncio.sleep(0.5 * (attempt + 1))
                continue
            if page.ok and self._cache is not None:
                with best_effort("writing the fetch cache"):
                    await self._cache.put_response(
                        key,
                        provider="fetch",
                        query=url,
                        response={
                            "text": page.text,
                            "title": page.title,
                            "published_at": page.published_at.isoformat()
                            if page.published_at
                            else None,
                        },
                        ttl_seconds=86_400,
                    )
            return page
        return FetchedPage(url=url, ok=False, error=last_error)

    async def _fetch_once(self, url: str) -> FetchedPage:
        limit = self._settings.max_content_bytes
        async with self._client.stream(
            "GET", url, timeout=float(self._settings.timeout_seconds)
        ) as response:
            if response.status_code >= 400:
                return FetchedPage(url=url, ok=False, error=f"HTTP {response.status_code}")
            content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
            if content_type and not content_type.startswith(_TEXTUAL):
                return FetchedPage(url=url, ok=False, error=f"unsupported type {content_type}")
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > limit:
                    break
                chunks.append(chunk)
            raw = b"".join(chunks)
            encoding = response.encoding or "utf-8"

        body = raw.decode(encoding, errors="replace")
        if content_type == "text/plain":
            return FetchedPage(url=url, ok=bool(body.strip()), text=body.strip())

        # CPU-bound; keep it off the event loop.
        extracted = await asyncio.to_thread(_extract, body, url)
        if extracted is None or not extracted.get("text"):
            return FetchedPage(url=url, ok=False, error="no extractable text")
        return FetchedPage(
            url=url,
            ok=True,
            text=str(extracted["text"]),
            title=extracted.get("title"),
            published_at=parse_date(extracted.get("date")),
        )


def _extract(html: str, url: str) -> dict[str, str] | None:
    document = trafilatura.bare_extraction(
        html, url=url, include_comments=False, include_tables=True, with_metadata=True
    )
    if document is None:
        return None
    data = document.as_dict() if hasattr(document, "as_dict") else dict(document)
    return {key: value for key, value in data.items() if isinstance(value, str)}
