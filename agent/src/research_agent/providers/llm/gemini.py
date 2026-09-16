"""Gemini over REST (`generateContent`, `batchEmbedContents`).

Plain HTTP keeps this adapter to a page and keeps the vendor SDK out of the runtime. The key goes
in the `x-goog-api-key` header, never the URL, where proxies and access logs would keep it.
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

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


class GeminiProvider:
    name = "gemini"

    def __init__(
        self, api_key: str, *, client: httpx.AsyncClient | None = None, base_url: str = BASE_URL
    ) -> None:
        self._key = api_key
        self._client = client or httpx.AsyncClient()
        self._base = base_url.rstrip("/")

    def __repr__(self) -> str:
        return "GeminiProvider(<key hidden>)"

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
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        contents = [
            {"role": "model" if m.role == "assistant" else "user", "parts": [{"text": m.content}]}
            for m in messages
            if m.role != "system"
        ]
        generation: dict[str, Any] = {
            "responseMimeType": "application/json",
            "responseJsonSchema": json_schema,
        }
        if "temperature" in params:
            generation["temperature"] = params["temperature"]
        if level := params.get("thinking_level"):
            generation["thinkingConfig"] = {"thinkingLevel": str(level).upper()}
        body: dict[str, Any] = {"contents": contents, "generationConfig": generation}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}

        data, latency = await self._post(f"/models/{model}:generateContent", body, timeout)
        candidates = data.get("candidates") or []
        if not candidates:
            reason = (data.get("promptFeedback") or {}).get("blockReason", "no candidates")
            raise ProviderError(
                ErrorCode.LLM_PROVIDER_ERROR, self.name, f"empty response: {reason}", False
            )
        candidate = candidates[0]
        parts = (candidate.get("content") or {}).get("parts") or []
        # Thought parts are flagged; only the answer text is kept.
        text = "".join(part.get("text", "") for part in parts if not part.get("thought"))
        usage = data.get("usageMetadata") or {}
        return Completion(
            text=text,
            model=model,
            tokens_in=int(usage.get("promptTokenCount", 0)),
            # Thinking tokens are billed as output, so they are counted as output here too.
            tokens_out=int(usage.get("candidatesTokenCount", 0))
            + int(usage.get("thoughtsTokenCount", 0)),
            latency_ms=latency,
            raw_finish_reason=candidate.get("finishReason"),
        )

    async def embed(self, *, model: str, texts: list[str], dimensions: int) -> Embeddings:
        body = {
            "requests": [
                {
                    "model": f"models/{model}",
                    "content": {"parts": [{"text": text}]},
                    "taskType": "SEMANTIC_SIMILARITY",
                    "outputDimensionality": dimensions,
                }
                for text in texts
            ]
        }
        data, latency = await self._post(f"/models/{model}:batchEmbedContents", body, 30.0)
        vectors = [item.get("values", []) for item in data.get("embeddings", [])]
        if len(vectors) != len(texts):
            raise ProviderError(
                ErrorCode.EMBEDDING_UNAVAILABLE, self.name, "embedding count mismatch", False
            )
        # The embeddings endpoint does not report usage; estimate for cost tracking.
        estimated = sum(len(text) for text in texts) // 4
        return Embeddings(vectors=vectors, model=model, tokens_in=estimated, latency_ms=latency)

    async def _post(
        self, path: str, body: dict[str, Any], timeout: float
    ) -> tuple[dict[str, Any], int]:
        started = time.perf_counter()
        try:
            response = await self._client.post(
                f"{self._base}{path}",
                json=body,
                headers={"x-goog-api-key": self._key, "Content-Type": "application/json"},
                timeout=timeout,
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
