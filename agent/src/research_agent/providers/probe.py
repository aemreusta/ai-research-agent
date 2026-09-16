"""Cheap "does this key work?" calls behind the Settings screen's Test buttons.

Each probe uses the least expensive authenticated endpoint a provider offers - a model listing
or a usage query - so pressing Test costs nothing where that is possible. Brave has no such
endpoint, so its probe spends one search from the free monthly allowance; the UI says so.

A probe never raises and never repeats the key in its detail: the result goes straight to the
browser.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from research_agent.keys import Provider
from research_agent.observability.redaction import redact_text
from research_agent.providers.llm.base import is_auth_failure, short_message

_TIMEOUT = httpx.Timeout(8.0, connect=4.0)


@dataclass(frozen=True, slots=True)
class ProbeResult:
    provider: Provider
    ok: bool
    detail: str


def _request(provider: Provider, key: str) -> httpx.Request:
    match provider:
        case Provider.GEMINI:
            return httpx.Request(
                "GET",
                "https://generativelanguage.googleapis.com/v1beta/models",
                params={"pageSize": 1},
                headers={"x-goog-api-key": key},
            )
        case Provider.OPENAI:
            return httpx.Request(
                "GET",
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {key}"},
            )
        case Provider.TAVILY:
            return httpx.Request(
                "GET",
                "https://api.tavily.com/usage",
                headers={"Authorization": f"Bearer {key}"},
            )
        case Provider.BRAVE:
            return httpx.Request(
                "GET",
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": "test", "count": 1},
                headers={"X-Subscription-Token": key, "Accept": "application/json"},
            )
        case Provider.OLLAMA:
            return httpx.Request("GET", f"{key.rstrip('/')}/api/tags")


def _explain(provider: Provider, response: httpx.Response) -> ProbeResult:
    code = response.status_code
    if code < 300:
        detail = "key accepted"
        if provider is Provider.TAVILY:
            usage = response.json().get("key", {})
            if usage.get("limit"):
                detail = f"key accepted - {usage.get('usage', 0)}/{usage['limit']} credits used"
        if provider is Provider.OLLAMA:
            models = [model.get("name") for model in response.json().get("models", [])]
            detail = f"reachable - models: {', '.join(models) or 'none pulled yet'}"
        return ProbeResult(provider, True, detail)
    if is_auth_failure(code, response.text):
        return ProbeResult(
            provider,
            False,
            f"rejected ({code}): {short_message(response.text) or 'the key is invalid or revoked'}",
        )
    if code == 429:
        # The key authenticated; the account is simply out of quota right now.
        return ProbeResult(provider, True, "key accepted, but rate limited (429) at the moment")
    return ProbeResult(
        provider, False, f"unexpected response ({code}): {short_message(response.text)}"
    )


async def probe(
    provider: Provider, key: str, *, client: httpx.AsyncClient | None = None
) -> ProbeResult:
    """Check one key. Never raises; the detail is safe to show in the browser."""
    request = _request(provider, key)
    owned = client is None
    http = client or httpx.AsyncClient(timeout=_TIMEOUT)
    try:
        response = await http.send(request)
        return _explain(provider, response)
    except httpx.TimeoutException:
        return ProbeResult(provider, False, "timed out")
    except httpx.HTTPError as exc:
        if provider is Provider.OLLAMA:
            return ProbeResult(
                provider, False, "not reachable - start it with the local-llm compose profile"
            )
        return ProbeResult(provider, False, redact_text(f"unreachable: {type(exc).__name__}"))
    finally:
        if owned:
            await http.aclose()
