"""A scripted LLM provider for tests, scenario runs and the key-less demo.

Responses are looked up by schema name (the output model's class name), so a test states
"when asked for a `ResearchPlan`, answer this" without caring which node asks or in what order.
A response may be a model instance, a dict, raw text (to exercise repair), a `ProviderError`
(to exercise retries and fallback), or a callable that sees the conversation.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import defaultdict, deque
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from research_agent.errors import ErrorCode
from research_agent.providers.llm.base import Completion, Embeddings, Message, ProviderError

Response = BaseModel | dict[str, Any] | str | ProviderError | Callable[[list[Message]], Any]


class FakeLLMProvider:
    def __init__(
        self,
        name: str = "gemini",
        *,
        script: dict[str, list[Response] | Response] | None = None,
        default: Callable[[str, list[Message]], Any] | None = None,
    ) -> None:
        self.name = name
        self._queues: dict[str, deque[Response]] = defaultdict(deque)
        self._sticky: dict[str, Response] = {}
        self._default = default
        self.calls: list[tuple[str, list[Message]]] = []
        for key, value in (script or {}).items():
            self.add(key, value)

    def add(self, schema_name: str, response: list[Response] | Response) -> None:
        """A list is consumed in order; a single value answers every call."""
        if isinstance(response, list):
            self._queues[schema_name].extend(response)
        else:
            self._sticky[schema_name] = response

    def calls_for(self, schema_name: str) -> list[list[Message]]:
        return [messages for name, messages in self.calls if name == schema_name]

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
        self.calls.append((schema_name, list(messages)))
        if self._queues[schema_name]:
            response: Any = self._queues[schema_name].popleft()
        elif schema_name in self._sticky:
            response = self._sticky[schema_name]
        elif self._default is not None:
            response = self._default(schema_name, messages)
        else:
            raise ProviderError(
                code=ErrorCode.LLM_PROVIDER_ERROR,
                provider=self.name,
                message=f"FakeLLMProvider has no script for {schema_name}",
                retryable=False,
            )
        if callable(response) and not isinstance(response, BaseModel):
            response = response(messages)
        if isinstance(response, ProviderError):
            raise response
        if isinstance(response, BaseModel):
            text = response.model_dump_json()
        elif isinstance(response, dict):
            text = json.dumps(response, ensure_ascii=False)
        else:
            text = str(response)
        prompt_chars = sum(len(message.content) for message in messages)
        return Completion(
            text=text,
            model=model,
            tokens_in=max(1, prompt_chars // 4),
            tokens_out=max(1, len(text) // 4),
            latency_ms=1,
        )

    async def embed(self, *, model: str, texts: list[str], dimensions: int) -> Embeddings:
        return Embeddings(
            vectors=[bag_of_words_vector(text, dimensions) for text in texts],
            model=model,
            tokens_in=sum(len(text) for text in texts) // 4,
            latency_ms=1,
        )


def bag_of_words_vector(text: str, dimensions: int = 64) -> list[float]:
    """A deterministic stand-in embedding: similar wording -> similar vector."""
    vector = [0.0] * dimensions
    for token in re.findall(r"\w+", text.lower()):
        bucket = (
            int(hashlib.md5(token.encode(), usedforsecurity=False).hexdigest(), 16) % dimensions
        )
        vector[bucket] += 1.0
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]
