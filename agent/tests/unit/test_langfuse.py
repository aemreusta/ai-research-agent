"""Langfuse: OTLP export shape, redaction on the way out, prompt round trip, graceful absence."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from research_agent.observability.langfuse import (
    LangfuseClient,
    LangfuseTracer,
    langfuse_settings,
)
from research_agent.prompting.registry import PromptRegistry
from research_agent.providers.llm.base import Message

SETTINGS = {
    "host": "http://langfuse:3000",
    "public_url": "http://localhost:3000",
    "public_key": "pk-lf-x",
    "secret_key": "sk-lf-y",
    "project": "research-agent",
}


def _client(handler: Any) -> LangfuseClient:
    return LangfuseClient(SETTINGS, httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_generations_are_exported_as_otlp_with_cost_and_prompt_link() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    tracer = LangfuseTracer(_client(handler))
    tracer.run_metadata = {"run_id": "r-1", "config_hash": "abc"}
    await tracer.generation(
        name="plan:PlanOutput",
        model="gemini-3.8-flash",
        provider="gemini",
        messages=[Message("user", "my key is sk-proj-abcdefghijklmnopqrstu and TCKN 10000000146")],
        output="{}",
        tokens_in=100,
        tokens_out=20,
        cost_usd=0.0012,
        latency_ms=900,
        prompt_id="plan",
        prompt_version="langfuse:3",
        metadata={"node": "plan", "tier": "reasoning"},
    )
    await tracer.flush()

    request = seen[0]
    assert request.url.path == "/api/public/otel/v1/traces"
    assert request.headers["x-langfuse-ingestion-version"] == "4"
    assert request.headers["authorization"].startswith("Basic ")
    body = json.loads(request.content)
    spans = body["resourceSpans"][0]["scopeSpans"][0]["spans"]
    generation, root = spans
    attrs = {a["key"]: next(iter(a["value"].values())) for a in generation["attributes"]}
    assert attrs["langfuse.observation.type"] == "generation"
    assert json.loads(attrs["langfuse.observation.usage_details"]) == {"input": 100, "output": 20}
    assert json.loads(attrs["langfuse.observation.cost_details"]) == {"total": 0.0012}
    assert attrs["langfuse.observation.prompt.name"] == "plan"
    assert attrs["langfuse.observation.prompt.version"] == "3"
    assert generation["parentSpanId"] == root["spanId"]
    assert len(generation["traceId"]) == 32

    raw = request.content.decode()
    assert "sk-proj-abcdefghijklmnopqrstu" not in raw, "secrets never reach Langfuse"
    assert "10000000146" not in raw, "nor do national ids"


async def test_a_seed_prompt_prompt_round_trips_through_the_prompts_api() -> None:
    stored: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            stored.update(json.loads(request.content))
            return httpx.Response(201, json={})
        if not stored:
            return httpx.Response(404)
        return httpx.Response(
            200, json={"prompt": stored["prompt"], "version": 1, "config": stored["config"]}
        )

    client = _client(handler)
    seed = PromptRegistry().seed("plan")
    assert await client.get_prompt("plan", label="production") is None
    await client.create_prompt(seed, labels=["production"])
    fetched = await client.get_prompt("plan", label="production")
    assert fetched is not None
    assert fetched.instructions == seed.instructions
    assert fetched.template == seed.template
    assert fetched.output_schema_hash == seed.output_schema_hash
    assert fetched.url and "prompts/plan" in fetched.url


async def test_an_export_failure_never_raises() -> None:
    tracer = LangfuseTracer(_client(lambda _: httpx.Response(503)))
    await tracer.generation(
        name="n",
        model="m",
        provider="p",
        messages=[],
        output="",
        tokens_in=0,
        tokens_out=0,
        cost_usd=0,
        latency_ms=1,
        prompt_id=None,
        prompt_version=None,
        metadata={},
    )
    await tracer.flush()


def test_langfuse_is_off_without_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    assert langfuse_settings() is None
    monkeypatch.setenv("LANGFUSE_HOST", "http://x")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    assert langfuse_settings() is None
    monkeypatch.setenv("LANGFUSE_ENABLED", "auto")
    assert langfuse_settings() is not None
