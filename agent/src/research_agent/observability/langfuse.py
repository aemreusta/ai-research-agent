"""Langfuse (self-hosted): generations with cost, and the prompt registry's remote source.

Postgres is the primary trace store; Langfuse is the second view - prompt-version-level cost
and latency, datasets, and the prompt editing UI (architecture v0.6 §14, §20.2). Everything here
is optional: with `LANGFUSE_ENABLED=false`, or when the server does not answer, runs proceed
exactly the same and the registry uses the repo seeds.

Traces go to Langfuse's OpenTelemetry endpoint as OTLP/JSON (self-hosted v4 rejects the legacy
batch ingestion API), prompts through the public prompts API - both over httpx rather than the
SDK, for the same reason the LLM adapters do: one HTTP stack, and every payload passes through
our redaction before it leaves the process.
"""

from __future__ import annotations

import json
import os
import secrets
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import httpx

from research_agent.keys import ProviderKeys
from research_agent.observability.logging import best_effort, get_logger
from research_agent.observability.redaction import redact
from research_agent.prompting.registry import PromptVersion
from research_agent.providers.llm.base import Message

_log = get_logger("langfuse")


def langfuse_settings() -> dict[str, str] | None:
    enabled = os.environ.get("LANGFUSE_ENABLED", "auto").lower()
    host = os.environ.get("LANGFUSE_HOST", "")
    public = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
    secret = os.environ.get("LANGFUSE_SECRET_KEY", "")
    if enabled in {"0", "false", "no", "off"} or not (host and public and secret):
        return None
    return {
        "host": host.rstrip("/"),
        "public_url": os.environ.get("LANGFUSE_PUBLIC_URL", host).rstrip("/"),
        "public_key": public,
        "secret_key": secret,
        "project": os.environ.get("LANGFUSE_INIT_PROJECT_ID", "research-agent"),
    }


class LangfuseClient:
    def __init__(self, settings: dict[str, str], client: httpx.AsyncClient) -> None:
        self._settings = settings
        self._client = client
        self._auth = (settings["public_key"], settings["secret_key"])

    async def alive(self) -> bool:
        try:
            response = await self._client.get(
                f"{self._settings['host']}/api/public/health", timeout=2.0
            )
        except httpx.HTTPError:
            return False
        return response.status_code == 200

    async def export_spans(self, spans: list[dict[str, Any]]) -> None:
        payload = {
            "resourceSpans": [
                {
                    "resource": {"attributes": _attributes({"service.name": "research-agent"})},
                    "scopeSpans": [{"scope": {"name": "research_agent"}, "spans": spans}],
                }
            ]
        }
        response = await self._client.post(
            f"{self._settings['host']}/api/public/otel/v1/traces",
            json=payload,
            auth=self._auth,
            timeout=10.0,
            headers={"x-langfuse-ingestion-version": "4"},
        )
        if response.status_code >= 300:
            raise RuntimeError(
                f"langfuse otel export {response.status_code}: {response.text[:200]}"
            )

    async def get_prompt(self, name: str, *, label: str) -> PromptVersion | None:
        response = await self._client.get(
            f"{self._settings['host']}/api/public/v2/prompts/{name}",
            params={"label": label},
            auth=self._auth,
            timeout=3.0,
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        data = response.json()
        body = data.get("prompt")
        config = data.get("config") or {}
        if not isinstance(body, str):
            return None  # chat prompts are not part of this registry's contract
        instructions, _, template = body.partition("\n---template---\n")
        return PromptVersion(
            id=name,
            version=str(data.get("version")),
            source="langfuse",
            instructions=instructions,
            template=template,
            demos=config.get("demos") or [],
            output_schema_hash=str(config.get("output_schema_hash", "")),
            label=label,
            seed_version=str(config["seed_version"]) if config.get("seed_version") else None,
            url=f"{self._settings['public_url']}/project/{self._settings['project']}/prompts/{name}",
        )

    async def create_prompt(self, version: PromptVersion, *, labels: list[str]) -> None:
        response = await self._client.post(
            f"{self._settings['host']}/api/public/v2/prompts",
            json={
                "name": version.id,
                "type": "text",
                "prompt": f"{version.instructions}\n---template---\n{version.template}",
                "labels": labels,
                "config": {
                    "demos": version.demos,
                    "output_schema_hash": version.output_schema_hash,
                    "source": "seed",
                    "seed_version": version.version,
                },
                "commitMessage": f"seed from config/prompts/{version.id}.yaml v{version.version}",
            },
            auth=self._auth,
            timeout=10.0,
        )
        if response.status_code >= 300:
            raise RuntimeError(f"create prompt {version.id}: {response.status_code}")

    def trace_url(self, trace_id: str) -> str:
        return (
            f"{self._settings['public_url']}/project/{self._settings['project']}/traces/{trace_id}"
        )


def _attr_value(value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, str):
        return {"stringValue": value}
    return {"stringValue": json.dumps(value, ensure_ascii=False, default=str)}


def _attributes(values: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"key": key, "value": _attr_value(value)}
        for key, value in values.items()
        if value is not None
    ]


def _nanos(moment: datetime) -> str:
    return str(int(moment.timestamp() * 1_000_000_000))


class LangfuseTracer:
    """One run = one trace: a root span plus a generation span per LLM call."""

    def __init__(self, client: LangfuseClient, trace_id: str | None = None) -> None:
        self._client = client
        self._trace_id = (trace_id or uuid.uuid4().hex).replace("-", "")[:32]
        self._root_id = secrets.token_hex(8)
        self._started = datetime.now(UTC)
        self._spans: list[dict[str, Any]] = []
        self.run_metadata: dict[str, Any] = {}

    def trace_url(self) -> str:
        return self._client.trace_url(self._trace_id)

    async def generation(
        self,
        *,
        name: str,
        model: str,
        provider: str,
        messages: list[Message],
        output: str,
        tokens_in: int,
        tokens_out: int,
        cost_usd: float,
        latency_ms: int,
        prompt_id: str | None,
        prompt_version: str | None,
        metadata: dict[str, Any],
    ) -> None:
        end = datetime.now(UTC)
        start = datetime.fromtimestamp(end.timestamp() - latency_ms / 1000, UTC)
        version = None
        if prompt_version and prompt_version.startswith("langfuse:"):
            version = int(prompt_version.split(":", 1)[1])
        attributes = redact(
            {
                "langfuse.observation.type": "generation",
                "langfuse.observation.model.name": model,
                "gen_ai.system": provider,
                "langfuse.observation.input": json.dumps(
                    [{"role": m.role, "content": m.content[:8000]} for m in messages],
                    ensure_ascii=False,
                ),
                "langfuse.observation.output": output[:8000],
                "langfuse.observation.usage_details": json.dumps(
                    {"input": tokens_in, "output": tokens_out}
                ),
                "langfuse.observation.cost_details": json.dumps({"total": round(cost_usd, 8)}),
                "langfuse.observation.metadata.node": metadata.get("node"),
                "langfuse.observation.metadata.tier": metadata.get("tier"),
                "langfuse.observation.metadata.iteration": metadata.get("iteration"),
                "langfuse.observation.metadata.prompt_source": prompt_version,
            }
        )
        if prompt_id and version is not None:
            attributes["langfuse.observation.prompt.name"] = prompt_id
            attributes["langfuse.observation.prompt.version"] = version
        self._spans.append(
            {
                "traceId": self._trace_id,
                "spanId": secrets.token_hex(8),
                "parentSpanId": self._root_id,
                "name": name,
                "kind": 3,
                "startTimeUnixNano": _nanos(start),
                "endTimeUnixNano": _nanos(end),
                "attributes": _attributes(attributes),
            }
        )
        if len(self._spans) >= 20:
            await self._send(final=False)

    async def _send(self, *, final: bool) -> None:
        spans, self._spans = self._spans, []
        if final:
            spans.append(
                {
                    "traceId": self._trace_id,
                    "spanId": self._root_id,
                    "name": "research-run",
                    "kind": 1,
                    "startTimeUnixNano": _nanos(self._started),
                    "endTimeUnixNano": _nanos(datetime.now(UTC)),
                    "attributes": _attributes(
                        {
                            "langfuse.trace.name": "research-run",
                            "langfuse.session.id": self.run_metadata.get("run_id"),
                            **{
                                f"langfuse.trace.metadata.{k}": v
                                for k, v in self.run_metadata.items()
                            },
                        }
                    ),
                }
            )
        if spans:
            with best_effort("sending traces to Langfuse"):
                await self._client.export_spans(spans)

    async def flush(self) -> None:
        await self._send(final=True)


def langfuse_toolkit_factory() -> Callable[[ProviderKeys], Awaitable[Any]]:
    """Wrap the default toolkit with Langfuse tracing and prompts when Langfuse is reachable."""
    from research_agent.agent.research import default_toolkit

    async def factory(keys: ProviderKeys) -> Any:
        toolkit = await default_toolkit(keys)
        settings = langfuse_settings()
        if settings is None or toolkit.http is None:
            return toolkit
        client = LangfuseClient(settings, toolkit.http)
        if not await client.alive():
            _log.warning("langfuse unreachable; continuing with Postgres traces and YAML prompts")
            return toolkit
        toolkit.tracer = LangfuseTracer(client)
        toolkit.remote_prompts = client
        return toolkit

    return factory


async def sync_seed_prompts() -> int:
    """Create every YAML seed in Langfuse that is not there yet (run by `migrate`)."""
    from research_agent.prompting.registry import load_seeds, seed_directory

    settings = langfuse_settings()
    if settings is None:
        return 0
    created = 0
    async with httpx.AsyncClient() as http:
        client = LangfuseClient(settings, http)
        if not await client.alive():
            return 0
        for seed in load_seeds(seed_directory()).values():
            existing = await client.get_prompt(seed.id, label="production")
            if existing is None:
                await client.create_prompt(seed, labels=["production", "seed"])
                created += 1
    return created
