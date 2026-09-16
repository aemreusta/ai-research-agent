"""What a provider adapter has to implement, and the failures it may report."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from research_agent.errors import ErrorCode

Role = Literal["system", "user", "assistant"]


@dataclass(frozen=True, slots=True)
class Message:
    role: Role
    content: str


@dataclass(frozen=True, slots=True)
class Completion:
    """One provider response, before any parsing."""

    text: str
    model: str
    tokens_in: int
    tokens_out: int
    latency_ms: int
    raw_finish_reason: str | None = None


@dataclass(frozen=True, slots=True)
class Embeddings:
    vectors: list[list[float]]
    model: str
    tokens_in: int
    latency_ms: int


@dataclass(slots=True)
class ProviderError(Exception):
    """A provider call failed. `retryable` decides between "try again" and "next provider"."""

    code: ErrorCode
    provider: str
    message: str
    retryable: bool
    status: int | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        suffix = f" (HTTP {self.status})" if self.status else ""
        return f"{self.code.value} from {self.provider}{suffix}: {self.message}"


class LLMProvider(Protocol):
    name: str

    async def complete(
        self,
        *,
        model: str,
        messages: list[Message],
        json_schema: dict[str, Any],
        schema_name: str,
        params: dict[str, Any],
        timeout: float,
    ) -> Completion: ...

    async def embed(self, *, model: str, texts: list[str], dimensions: int) -> Embeddings: ...


def classify_status(provider: str, status: int, body: str) -> ProviderError:
    """HTTP status -> coded failure. Shared by every adapter so the mapping is one table."""
    snippet = body[:300]
    if status in (401, 403):
        return ProviderError(ErrorCode.LLM_AUTH, provider, snippet, retryable=False, status=status)
    if status == 429:
        return ProviderError(
            ErrorCode.LLM_RATE_LIMIT, provider, snippet, retryable=True, status=status
        )
    if status == 408 or status >= 500:
        return ProviderError(
            ErrorCode.LLM_PROVIDER_ERROR, provider, snippet, retryable=True, status=status
        )
    # 400/404/422: the request itself was refused (unknown model, schema rejected). Retrying the
    # same request is pointless; the next provider may accept it.
    return ProviderError(
        ErrorCode.LLM_PROVIDER_ERROR, provider, snippet, retryable=False, status=status
    )
