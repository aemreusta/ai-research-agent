"""Normalised search results and the provider protocol."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from research_agent.errors import ErrorCode


class SearchHit(BaseModel):
    """One result, whichever provider it came from."""

    model_config = ConfigDict(frozen=True)

    url: str
    title: str = ""
    snippet: str = ""
    content: str | None = Field(
        default=None, description="Full page text when the provider returned it (Tavily raw)."
    )
    published_at: date | None = None
    provider: str
    rank: int
    provider_score: float | None = None


@dataclass(frozen=True, slots=True)
class SearchRequest:
    query: str
    max_results: int = 8
    language: str | None = None  # ISO 639-1, e.g. "tr"
    start_date: date | None = None
    end_date: date | None = None

    def cache_parts(self, provider: str) -> tuple[Any, ...]:
        return (
            provider,
            " ".join(self.query.lower().split()),
            self.max_results,
            self.language,
            self.start_date,
            self.end_date,
        )


@dataclass(slots=True)
class SearchProviderError(Exception):
    code: ErrorCode
    provider: str
    message: str
    retryable: bool
    status: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        suffix = f" (HTTP {self.status})" if self.status else ""
        return f"{self.code.value} from {self.provider}{suffix}: {self.message}"


class SearchProvider(Protocol):
    name: str

    async def search(self, request: SearchRequest, *, timeout: float) -> list[SearchHit]: ...


def classify_search_status(provider: str, status: int, body: str) -> SearchProviderError:
    snippet = body[:300]
    if status in (401, 403):
        return SearchProviderError(ErrorCode.SEARCH_AUTH, provider, snippet, False, status)
    if status == 429 or status == 432 or status == 433:
        # Tavily uses 432/433 for plan and pay-as-you-go limits.
        return SearchProviderError(
            ErrorCode.SEARCH_RATE_LIMIT, provider, snippet, status == 429, status
        )
    if status == 408 or status >= 500:
        return SearchProviderError(ErrorCode.SEARCH_PROVIDER_ERROR, provider, snippet, True, status)
    return SearchProviderError(ErrorCode.SEARCH_PROVIDER_ERROR, provider, snippet, False, status)


def parse_date(value: Any) -> date | None:
    """Providers send ISO timestamps, RFC dates or nothing; anything unparseable is None."""
    if not value or not isinstance(value, str):
        return None
    from email.utils import parsedate_to_datetime

    text = value.strip()
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(text).date()
    except (TypeError, ValueError, IndexError):
        return None
