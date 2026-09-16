"""Key probes: right endpoint, honest verdict, never an echo of the key."""

from __future__ import annotations

import httpx
import pytest

from research_agent.keys import Provider
from research_agent.providers.probe import probe

KEY = "tvly-abcdefghijklmnopqrstuvwx"


def _client(
    status: int, body: object | None = None, seen: list[httpx.Request] | None = None
) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, json=body if body is not None else {})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_tavily_uses_the_free_usage_endpoint() -> None:
    seen: list[httpx.Request] = []
    async with _client(200, {"key": {"usage": 3, "limit": 1000}}, seen) as client:
        result = await probe(Provider.TAVILY, KEY, client=client)
    assert result.ok
    assert seen[0].url.path == "/usage"
    assert seen[0].headers["Authorization"] == f"Bearer {KEY}"
    assert "3/1000" in result.detail


@pytest.mark.parametrize("status", [401, 403])
async def test_a_rejected_key_is_reported_as_invalid(status: int) -> None:
    async with _client(status) as client:
        result = await probe(Provider.OPENAI, "sk-bad-key-value-abcdefgh", client=client)
    assert not result.ok
    assert "invalid" in result.detail


async def test_rate_limited_still_means_the_key_authenticated() -> None:
    async with _client(429) as client:
        result = await probe(Provider.GEMINI, "AIza-key", client=client)
    assert result.ok
    assert "rate limited" in result.detail


async def test_gemini_sends_the_key_in_a_header_not_the_url() -> None:
    """A key in the query string ends up in proxy and access logs."""
    seen: list[httpx.Request] = []
    async with _client(200, {"models": []}, seen) as client:
        await probe(Provider.GEMINI, "AIza-secret", client=client)
    assert "AIza-secret" not in str(seen[0].url)
    assert seen[0].headers["x-goog-api-key"] == "AIza-secret"


async def test_the_detail_never_repeats_the_key() -> None:
    for status in (200, 401, 429, 500):
        async with _client(status) as client:
            result = await probe(Provider.OPENAI, "sk-leaky-key-abcdefghijk", client=client)
        assert "sk-leaky-key-abcdefghijk" not in result.detail


async def test_a_network_failure_is_a_result_not_an_exception() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await probe(Provider.BRAVE, "BSA-key", client=client)
    assert not result.ok
    assert "unreachable" in result.detail


async def test_ollama_lists_its_models() -> None:
    async with _client(200, {"models": [{"name": "qwen3:8b"}]}) as client:
        result = await probe(Provider.OLLAMA, "http://ollama:11434", client=client)
    assert result.ok
    assert "qwen3:8b" in result.detail


async def test_gemini_and_brave_style_invalid_keys_are_reported_as_rejected() -> None:
    """Real bodies: Gemini answers 400 API_KEY_INVALID, Brave 422 SUBSCRIPTION_TOKEN_INVALID."""
    bodies = {
        Provider.GEMINI: (
            400,
            {
                "error": {
                    "code": 400,
                    "message": "API key not valid. Please pass a valid API key.",
                    "details": [{"reason": "API_KEY_INVALID"}],
                }
            },
        ),
        Provider.BRAVE: (
            422,
            {
                "type": "ErrorResponse",
                "error": {
                    "status": 422,
                    "detail": "The provided API key is invalid.",
                    "code": "SUBSCRIPTION_TOKEN_INVALID",
                },
            },
        ),
    }
    for provider, (status, body) in bodies.items():
        async with _client(status, body) as client:
            result = await probe(provider, "whatever-key", client=client)
        assert not result.ok
        assert result.detail.startswith(f"rejected ({status})")
        assert "invalid" in result.detail.lower() or "not valid" in result.detail.lower()
