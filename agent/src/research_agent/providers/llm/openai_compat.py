"""OpenAI Chat Completions, and anything that speaks it (Ollama's `/v1` endpoint).

Structured output uses `response_format: json_schema` with `strict: true`; the schema passed in
is already normalised for strict mode (`providers.llm.schema`).
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from research_agent.errors import ErrorCode
from research_agent.providers.llm.base import (
    Completion,
    Embeddings,
    Message,
    ProviderError,
    classify_status,
)

OPENAI_BASE_URL = "https://api.openai.com/v1"


class OpenAICompatibleProvider:
    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        api_key: str | None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.name = name
        self._base = base_url.rstrip("/")
        self._key = api_key
        self._client = client or httpx.AsyncClient()

    @classmethod
    def openai(
        cls, api_key: str, *, client: httpx.AsyncClient | None = None
    ) -> OpenAICompatibleProvider:
        return cls(name="openai", base_url=OPENAI_BASE_URL, api_key=api_key, client=client)

    @classmethod
    def ollama(
        cls, base_url: str, *, client: httpx.AsyncClient | None = None
    ) -> OpenAICompatibleProvider:
        return cls(
            name="ollama", base_url=f"{base_url.rstrip('/')}/v1", api_key=None, client=client
        )

    def __repr__(self) -> str:
        return f"OpenAICompatibleProvider(name={self.name!r}, base_url={self._base!r})"

    async def complete(
        self,
        *,
        model: str,
        messages: list[Message],
        json_schema: dict[str, Any],
        schema_name: str,
        params: dict[str, Any],
        timeout: float,
    ) -> Completion:
        body: dict[str, Any] = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": json_schema},
            },
        }
        if effort := params.get("reasoning_effort"):
            body["reasoning_effort"] = effort
        if "temperature" in params:
            body["temperature"] = params["temperature"]

        data, latency = await self._post("/chat/completions", body, timeout)
        choices = data.get("choices") or []
        if not choices:
            raise ProviderError(ErrorCode.LLM_PROVIDER_ERROR, self.name, "no choices", False)
        message = choices[0].get("message") or {}
        if refusal := message.get("refusal"):
            raise ProviderError(
                ErrorCode.LLM_PROVIDER_ERROR, self.name, f"refused: {refusal}"[:300], False
            )
        usage = data.get("usage") or {}
        return Completion(
            text=message.get("content") or "",
            model=data.get("model") or model,
            tokens_in=int(usage.get("prompt_tokens", 0)),
            tokens_out=int(usage.get("completion_tokens", 0)),
            latency_ms=latency,
            raw_finish_reason=choices[0].get("finish_reason"),
        )

    async def embed(self, *, model: str, texts: list[str], dimensions: int) -> Embeddings:
        body: dict[str, Any] = {"model": model, "input": texts}
        if self.name == "openai":
            body["dimensions"] = dimensions
        data, latency = await self._post("/embeddings", body, 30.0)
        rows = sorted(data.get("data", []), key=lambda row: row.get("index", 0))
        vectors = [row.get("embedding", []) for row in rows]
        if len(vectors) != len(texts):
            raise ProviderError(
                ErrorCode.EMBEDDING_UNAVAILABLE, self.name, "embedding count mismatch", False
            )
        usage = data.get("usage") or {}
        return Embeddings(
            vectors=vectors,
            model=model,
            tokens_in=int(usage.get("prompt_tokens", 0)),
            latency_ms=latency,
        )

    async def _post(
        self, path: str, body: dict[str, Any], timeout: float
    ) -> tuple[dict[str, Any], int]:
        headers = {"Content-Type": "application/json"}
        if self._key:
            headers["Authorization"] = f"Bearer {self._key}"
        started = time.perf_counter()
        try:
            response = await self._client.post(
                f"{self._base}{path}", json=body, headers=headers, timeout=timeout
            )
        except httpx.TimeoutException as exc:
            raise ProviderError(ErrorCode.LLM_TIMEOUT, self.name, "timed out", True) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                ErrorCode.LLM_PROVIDER_ERROR, self.name, type(exc).__name__, True
            ) from exc
        latency = int((time.perf_counter() - started) * 1000)
        if response.status_code >= 300:
            raise classify_status(self.name, response.status_code, response.text)
        payload: dict[str, Any] = response.json()
        return payload, latency
