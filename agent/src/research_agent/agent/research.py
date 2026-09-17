"""Assemble a run: providers from keys, the dependency bundle, the graph - and execute it.

Two front doors use this module: `ResearchGraphRunner` inside the agent service (Postgres
everywhere, resumable), and the `research run` CLI (in-memory sinks, no database). Both run the
same graph with the same nodes.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

import httpx
import yaml
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from research_agent.agent.budget import BudgetMeter
from research_agent.agent.deps import AgentDeps
from research_agent.agent.graph import GraphState, build_graph, recursion_limit
from research_agent.agent.render import render_json, render_markdown
from research_agent.agent.runner import RunContext, RunOutcome
from research_agent.agent.runtime import (
    Cache,
    CallRecorder,
    DbCache,
    DbCallRecorder,
    EventSink,
)
from research_agent.agent.scoring import DomainTiers
from research_agent.agent.state import ResearchState, StopReason
from research_agent.config.loader import config_hash
from research_agent.config.schema import Settings
from research_agent.contracts import RunStatus
from research_agent.db.repository import RunRepository
from research_agent.errors import AgentError, ErrorCode
from research_agent.gate.config import GateConfig
from research_agent.keys import Provider, ProviderKeys
from research_agent.paths import config_dir
from research_agent.pii.masking import Masker, RegexMasker
from research_agent.prompting.predict import Predictor
from research_agent.prompting.registry import PromptRegistry, RemotePrompts
from research_agent.prompting.skills import load_skills
from research_agent.providers.embeddings import Embedder
from research_agent.providers.fetch import ContentFetcher
from research_agent.providers.llm.base import LLMProvider
from research_agent.providers.llm.catalog import ModelCatalog, catalog
from research_agent.providers.llm.gateway import LLMGateway, Tracer
from research_agent.providers.llm.gemini import GeminiProvider
from research_agent.providers.llm.openai_compat import OpenAICompatibleProvider
from research_agent.providers.search.base import SearchProvider
from research_agent.providers.search.brave import BraveProvider
from research_agent.providers.search.gateway import SearchGateway
from research_agent.providers.search.tavily import TavilyProvider


def _search_settings() -> dict[str, Any]:
    with (config_dir() / "search.yaml").open(encoding="utf-8") as handle:
        loaded: dict[str, Any] = yaml.safe_load(handle)
    return loaded


async def _ollama_alive(base_url: str, client: httpx.AsyncClient) -> bool:
    """Ollama is optional and its URL has a default, so only a live server joins the chain."""
    try:
        response = await client.get(f"{base_url.rstrip('/')}/api/tags", timeout=1.5)
    except httpx.HTTPError:
        return False
    return response.status_code == 200


async def build_llm_providers(
    keys: ProviderKeys, client: httpx.AsyncClient
) -> dict[str, LLMProvider]:
    providers: dict[str, LLMProvider] = {}
    if gemini := keys.get(Provider.GEMINI):
        providers["gemini"] = GeminiProvider(gemini, client=client)
    if openai := keys.get(Provider.OPENAI):
        providers["openai"] = OpenAICompatibleProvider.openai(openai, client=client)
    if (ollama := keys.get(Provider.OLLAMA)) and await _ollama_alive(ollama, client):
        providers["ollama"] = OpenAICompatibleProvider.ollama(ollama, client=client)
    return providers


def build_search_providers(
    keys: ProviderKeys, client: httpx.AsyncClient
) -> dict[str, SearchProvider]:
    config = _search_settings()["providers"]
    providers: dict[str, SearchProvider] = {}
    if tavily := keys.get(Provider.TAVILY):
        providers["tavily"] = TavilyProvider(
            tavily,
            client=client,
            base_url=config["tavily"]["base_url"],
            search_depth=config["tavily"].get("search_depth", "basic"),
            include_raw_content=config["tavily"].get("include_raw_content", "markdown"),
        )
    if brave := keys.get(Provider.BRAVE):
        providers["brave"] = BraveProvider(
            brave,
            client=client,
            base_url=config["brave"]["base_url"],
            extra_snippets=bool(config["brave"].get("extra_snippets", True)),
        )
    return providers


@dataclass
class Toolkit:
    """Provider instances for one run. Tests and the CLI pass fakes here."""

    llm: dict[str, LLMProvider]
    search: dict[str, SearchProvider]
    fetcher: ContentFetcher | None = None
    http: httpx.AsyncClient | None = None
    tracer: Tracer | None = None
    remote_prompts: RemotePrompts | None = None
    catalog: ModelCatalog | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    async def aclose(self) -> None:
        if self.http is not None:
            await self.http.aclose()


async def default_toolkit(keys: ProviderKeys) -> Toolkit:
    http = httpx.AsyncClient(
        follow_redirects=True,
        limits=httpx.Limits(max_connections=40, max_keepalive_connections=20),
        headers={"User-Agent": "research-agent/0.1"},
    )
    return Toolkit(
        llm=await build_llm_providers(keys, http),
        search=build_search_providers(keys, http),
        http=http,
    )


async def build_deps(
    *,
    run_id: uuid.UUID,
    settings: Settings,
    toolkit: Toolkit,
    events: EventSink,
    recorder: CallRecorder,
    cache: Cache,
    masker: Masker | None = None,
    today: date | None = None,
    is_cancelled: Callable[[], Awaitable[bool]] | None = None,
    report_progress: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> AgentDeps:
    models = toolkit.catalog or catalog()
    meter = BudgetMeter(settings.budget)
    llm = LLMGateway(
        providers=toolkit.llm,
        catalog=models,
        settings=settings.llm,
        recorder=recorder,
        meter=meter,
        tracer=toolkit.tracer,
    )
    search = SearchGateway(
        providers=toolkit.search,
        chain=list(_search_settings().get("chain", ["tavily", "brave"])),
        settings=settings.search,
        cache=cache,
        recorder=recorder,
        meter=meter,
        cache_enabled=bool(_search_settings().get("cache", {}).get("enabled", True)),
    )
    registry = PromptRegistry(remote=toolkit.remote_prompts)
    prompts = await registry.resolve_all(events)
    deps = AgentDeps(
        settings=settings,
        llm=llm,
        search=search,
        fetcher=toolkit.fetcher or ContentFetcher(settings.fetch, cache=cache, client=toolkit.http),
        embedder=Embedder(providers=toolkit.llm, catalog=models, cache=cache, meter=meter),
        registry=registry,
        prompts=prompts,
        predictor=Predictor(llm, prompts),
        skills=load_skills(),
        tiers=DomainTiers.load(),
        gate=GateConfig.load(),
        masker=masker or RegexMasker(),
        meter=meter,
        events=events,
        recorder=recorder,
        cache=cache,
        today=today or datetime.now(UTC).date(),
    )
    if is_cancelled is not None:
        deps.is_cancelled = is_cancelled
    if report_progress is not None:
        deps.report_progress = report_progress
    return deps


def preflight(deps: AgentDeps) -> AgentError | None:
    """Fail fast, with an actionable message, when a run cannot possibly work."""
    if not deps.llm.available:
        return AgentError(
            code=ErrorCode.LLM_AUTH,
            node="preflight",
            decision="fail the run before any work",
            outcome="No LLM key: add a Gemini or OpenAI key in Settings "
            "(or GEMINI_API_KEY / OPENAI_API_KEY in .env).",
        )
    if not deps.search.available:
        return AgentError(
            code=ErrorCode.SEARCH_AUTH,
            node="preflight",
            decision="fail the run before any work",
            outcome="No search key: add a Tavily or Brave key in Settings "
            "(or TAVILY_API_KEY / BRAVE_API_KEY in .env).",
        )
    return None


async def execute(
    deps: AgentDeps,
    *,
    run_id: uuid.UUID,
    question: str,
    checkpointer: BaseCheckpointSaver[Any],
    resume: bool = False,
) -> ResearchState:
    graph = build_graph(deps).compile(checkpointer=checkpointer)
    config: RunnableConfig = {
        "configurable": {"thread_id": str(run_id)},
        "recursion_limit": recursion_limit(deps),
    }
    payload: GraphState | None = None
    if resume:
        snapshot = await graph.aget_state(config)
        if not snapshot.next and snapshot.values.get("research"):
            # The process can die after the final checkpoint but before the terminal DB CAS.
            # Settle that completed graph rather than starting another paid research run.
            completed = ResearchState.model_validate(snapshot.values["research"])
            if completed.report is not None and completed.gate_result is not None:
                deps.meter.restore(completed.budget)
                return completed
        if not snapshot.next:
            resume = False
    if not resume:
        initial = ResearchState(
            run_id=run_id, question=question, as_of=deps.today, started_at=datetime.now(UTC)
        )
        payload = {"research": initial.model_dump(mode="json")}
    result = await graph.ainvoke(payload, config)
    return ResearchState.model_validate(result["research"])


def metadata_for(deps: AgentDeps, settings: Settings) -> dict[str, Any]:
    return {
        "config_hash": config_hash(settings.model_dump(mode="json")),
        "models_used": {
            **deps.llm.models_used,
            **({"embedding": deps.embedder.model} if deps.embedder.model else {}),
        },
        "prompt_versions": deps.prompts.references(),
        "search_providers": deps.search.available,
        "llm_providers": deps.llm.available,
    }


def artifacts_for(state: ResearchState, metadata: dict[str, Any]) -> dict[str, tuple[str, str]]:
    import json

    report_json = render_json(state, metadata=metadata)
    return {
        "report_md": ("text/markdown", render_markdown(state, metadata=metadata)),
        "report_json": (
            "application/json",
            json.dumps(report_json, ensure_ascii=False, indent=2, default=str) + "\n",
        ),
        "gate_result": (
            "application/json",
            json.dumps(state.gate_result or {}, ensure_ascii=False, indent=2) + "\n",
        ),
        "state": ("application/json", state.model_dump_json(indent=2) + "\n"),
    }


def outcome_status(state: ResearchState) -> RunStatus:
    verdict = (state.gate_result or {}).get("verdict")
    if verdict == "pass" and state.stop_reason is StopReason.SUFFICIENT:
        return RunStatus.SUCCEEDED
    return RunStatus.SUCCEEDED_WITH_WARNINGS


class ResearchGraphRunner:
    """The agent service's `GraphRunner`: Postgres-backed, resumable, key-aware."""

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        *,
        dsn: str,
        toolkit_factory: Callable[[ProviderKeys], Awaitable[Toolkit]] = default_toolkit,
        masker: Masker | None = None,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._dsn = dsn
        self._toolkit_factory = toolkit_factory
        self._masker = masker

    async def __call__(self, context: RunContext) -> RunOutcome:
        from research_agent.db.checkpoints import lease_checkpointer

        if context.lease_id is None:
            raise ValueError("A service run requires a dispatch lease")

        toolkit = await self._toolkit_factory(context.keys)
        try:
            deps = await build_deps(
                run_id=context.run_id,
                settings=context.settings,
                toolkit=toolkit,
                events=context.events,
                recorder=DbCallRecorder(self._sessionmaker),
                cache=DbCache(self._sessionmaker),
                masker=self._masker,
                is_cancelled=context.is_cancelled,
                report_progress=self._progress(context.run_id, context.lease_id),
            )
            if (problem := preflight(deps)) is not None:
                await context.events.error(problem)
                return RunOutcome(
                    status=RunStatus.FAILED,
                    error_code=problem.code.value,
                    error_message=problem.outcome,
                )
            async with self._sessionmaker() as session:
                await RunRepository(session, lease_id=context.lease_id).update_metadata(
                    context.run_id, prompt_versions=deps.prompts.references()
                )
            if toolkit.tracer is not None and hasattr(toolkit.tracer, "trace_url"):
                toolkit.tracer.run_metadata = {  # type: ignore[attr-defined]
                    "run_id": str(context.run_id),
                    "config_hash": config_hash(context.settings.model_dump(mode="json")),
                    "attempt": context.attempt,
                }
                async with self._sessionmaker() as session:
                    await RunRepository(session, lease_id=context.lease_id).update_metadata(
                        context.run_id, langfuse_trace_url=toolkit.tracer.trace_url()
                    )

            async with lease_checkpointer(
                self._dsn, run_id=context.run_id, lease_id=context.lease_id
            ) as checkpointer:
                state = await execute(
                    deps,
                    run_id=context.run_id,
                    question=context.question,
                    checkpointer=checkpointer,
                    resume=context.attempt > 1,
                )

            metadata = metadata_for(deps, context.settings)
            async with self._sessionmaker() as session:
                await RunRepository(session, lease_id=context.lease_id).update_metadata(
                    context.run_id, models_used=metadata["models_used"], skills_used=state.skills
                )
            return RunOutcome(
                status=outcome_status(state),
                stop_reason=state.stop_reason.value if state.stop_reason else None,
                gate_status=(state.gate_result or {}).get("verdict"),
                artifacts=artifacts_for(state, metadata),
                metrics={
                    "iteration": state.iteration,
                    "searches_used": deps.meter.searches,
                    "tokens_in": deps.meter.tokens_in,
                    "tokens_out": deps.meter.tokens_out,
                    "cost_usd": deps.meter.cost_usd,
                },
            )
        finally:
            # Flush while the HTTP client is still open.
            flush = getattr(toolkit.tracer, "flush", None)
            if callable(flush):
                await flush()
            await toolkit.aclose()

    def _progress(
        self, run_id: uuid.UUID, lease_id: uuid.UUID
    ) -> Callable[[dict[str, Any]], Awaitable[None]]:
        async def report(counters: dict[str, Any]) -> None:
            async with self._sessionmaker() as session:
                await RunRepository(session, lease_id=lease_id).update_counters(run_id, **counters)

        return report


def env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}
