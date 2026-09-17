"""Adapters: the request each provider receives and how its answer is read back."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from research_agent.errors import ErrorCode
from research_agent.providers.llm.base import Message, ProviderError
from research_agent.providers.llm.gemini import GeminiProvider
from research_agent.providers.llm.openai_compat import OpenAICompatibleProvider

SCHEMA = {
    "type": "object",
    "properties": {"x": {"type": "string"}},
    "required": ["x"],
    "additionalProperties": False,
}
MESSAGES = [Message("system", "be terse"), Message("user", "hello")]


def client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _complete(provider: Any, **overrides: Any) -> Any:
    arguments: dict[str, Any] = {
        "model": "m",
        "messages": MESSAGES,
        "json_schema": SCHEMA,
        "schema_name": "Out",
        "params": {},
        "timeout": 5.0,
    }
    arguments.update(overrides)
    return await provider.complete(**arguments)


# --- Gemini --------------------------------------------------------------------


async def test_gemini_request_shape() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "thinking...", "thought": True},
                                {"text": '{"x": "hi"}'},
                            ]
                        },
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 10,
                    "candidatesTokenCount": 4,
                    "thoughtsTokenCount": 6,
                },
            },
        )

    provider = GeminiProvider("AIza-test", client=client(handler))
    result = await _complete(
        provider, model="gemini-3.1-flash-lite", params={"thinking_level": "low"}
    )

    request = seen[0]
    body = json.loads(request.content)
    assert request.url.path.endswith("/models/gemini-3.1-flash-lite:generateContent")
    assert request.headers["x-goog-api-key"] == "AIza-test"
    assert "AIza-test" not in str(request.url)
    assert body["systemInstruction"]["parts"][0]["text"] == "be terse"
    assert body["contents"] == [{"role": "user", "parts": [{"text": "hello"}]}]
    assert body["generationConfig"]["responseJsonSchema"] == SCHEMA
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert body["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "LOW"}

    assert result.text == '{"x": "hi"}', "thought parts must not leak into the answer"
    assert result.tokens_in == 10
    assert result.tokens_out == 10, "thinking tokens are billed as output"


async def test_gemini_blocked_prompt_is_a_non_retryable_error() -> None:
    provider = GeminiProvider(
        "k",
        client=client(
            lambda _: httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}})
        ),
    )
    with pytest.raises(ProviderError) as caught:
        await _complete(provider)
    assert caught.value.retryable is False
    assert "SAFETY" in caught.value.message


async def test_gemini_25_pro_uses_native_thinking_instead_of_unsupported_level() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert "thinkingConfig" not in body["generationConfig"]
        return httpx.Response(
            200, json={"candidates": [{"content": {"parts": [{"text": '{"x":"ok"}'}]}}]}
        )

    await _complete(
        GeminiProvider("test", client=client(handler)),
        model="gemini-2.5-pro",
        params={"thinking_level": "medium"},
    )


async def test_gemini_embeddings() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, json={"embeddings": [{"values": [0.1, 0.2]}, {"values": [0.3, 0.4]}]}
        )

    provider = GeminiProvider("k", client=client(handler))
    result = await provider.embed(model="gemini-embedding-001", texts=["a", "b"], dimensions=768)
    body = json.loads(seen[0].content)
    assert seen[0].url.path.endswith(":batchEmbedContents")
    assert body["requests"][0]["taskType"] == "SEMANTIC_SIMILARITY"
    assert body["requests"][0]["outputDimensionality"] == 768
    assert result.vectors == [[0.1, 0.2], [0.3, 0.4]]


# --- OpenAI-compatible -------------------------------------------------------------


async def test_openai_request_shape() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "model": "gpt-5.6-luna",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": '{"x": "hi"}'},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 7},
            },
        )

    provider = OpenAICompatibleProvider.openai("sk-test", client=client(handler))
    result = await _complete(provider, model="gpt-5.6-luna", params={"reasoning_effort": "low"})

    body = json.loads(seen[0].content)
    assert seen[0].url.path == "/v1/chat/completions"
    assert seen[0].headers["Authorization"] == "Bearer sk-test"
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["response_format"]["json_schema"]["schema"] == SCHEMA
    assert body["reasoning_effort"] == "low"
    assert body["messages"][0] == {"role": "system", "content": "be terse"}
    assert (result.text, result.tokens_in, result.tokens_out) == ('{"x": "hi"}', 12, 7)


async def test_a_refusal_is_not_treated_as_an_answer() -> None:
    provider = OpenAICompatibleProvider.openai(
        "sk",
        client=client(
            lambda _: httpx.Response(
                200, json={"choices": [{"message": {"content": None, "refusal": "I can't"}}]}
            )
        ),
    )
    with pytest.raises(ProviderError) as caught:
        await _complete(provider)
    assert "refused" in caught.value.message


async def test_ollama_sends_no_authorization_header() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}], "usage": {}})

    provider = OpenAICompatibleProvider.ollama("http://ollama:11434", client=client(handler))
    await _complete(provider)
    assert str(seen[0].url) == "http://ollama:11434/v1/chat/completions"
    assert "Authorization" not in seen[0].headers


# --- shared error mapping ------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [
        (401, ErrorCode.LLM_AUTH, False),
        (403, ErrorCode.LLM_AUTH, False),
        (429, ErrorCode.LLM_RATE_LIMIT, True),
        (500, ErrorCode.LLM_PROVIDER_ERROR, True),
        (503, ErrorCode.LLM_PROVIDER_ERROR, True),
        (400, ErrorCode.LLM_PROVIDER_ERROR, False),
        (404, ErrorCode.LLM_PROVIDER_ERROR, False),
    ],
)
@pytest.mark.parametrize("factory", ["gemini", "openai"])
async def test_http_failures_map_to_codes(
    status: int, code: ErrorCode, retryable: bool, factory: str
) -> None:
    http = client(lambda _: httpx.Response(status, text="nope"))
    provider: Any = (
        GeminiProvider("k", client=http)
        if factory == "gemini"
        else OpenAICompatibleProvider.openai("k", client=http)
    )
    with pytest.raises(ProviderError) as caught:
        await _complete(provider)
    assert caught.value.code is code
    assert caught.value.retryable is retryable
    assert caught.value.status == status


async def test_a_timeout_is_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(ProviderError) as caught:
        await _complete(GeminiProvider("k", client=client(handler)))
    assert caught.value.code is ErrorCode.LLM_TIMEOUT
    assert caught.value.retryable


def test_adapters_do_not_show_keys_in_repr() -> None:
    assert "secret" not in repr(GeminiProvider("secret"))
    assert "secret" not in repr(OpenAICompatibleProvider.openai("secret"))
