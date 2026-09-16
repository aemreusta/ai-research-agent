"""What a provider adapter has to implement, and the failures it may report."""

from __future__ import annotations

import contextlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from research_agent.errors import ErrorCode
from research_agent.observability.redaction import redact_text

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


# Invalid keys do not always arrive as 401: Gemini answers 400 API_KEY_INVALID, Brave 422
# SUBSCRIPTION_TOKEN_INVALID. Retrying those would only burn time.
_AUTH_MARKERS = re.compile(
    r"API_KEY_INVALID|API key not valid|SUBSCRIPTION_TOKEN_INVALID|invalid[_ ]api[_ ]key|"
    r"Incorrect API key|invalid.{0,20}(?:token|key)|unauthori[sz]ed",
    re.IGNORECASE,
)


def is_auth_failure(status: int, body: str) -> bool:
    return status in (401, 403) or (status in (400, 422) and bool(_AUTH_MARKERS.search(body)))


def short_message(body: str) -> str:
    """The provider's own error message, without echoing request data or partial keys."""
    message = body
    with contextlib.suppress(ValueError, AttributeError, TypeError):
        data = json.loads(body)
        error = data.get("error", data) if isinstance(data, dict) else {}
        if isinstance(error, dict):
            message = str(error.get("message") or error.get("detail") or error.get("code") or "")
        elif isinstance(error, str):
            message = error
    message = re.sub(r"(?:sk|tvly|AIza|AQ\.|BSA)[-_.A-Za-z0-9*]{4,}", "<key>", message)
    return redact_text(" ".join(message.split()))[:200]


def classify_status(provider: str, status: int, body: str) -> ProviderError:
    """HTTP status -> coded failure. Shared by every adapter so the mapping is one table."""
    snippet = short_message(body)
    if is_auth_failure(status, body):
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
