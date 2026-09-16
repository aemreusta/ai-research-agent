"""Everything a node may use, in one object. Built per run by the runner or the CLI."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from research_agent.agent.budget import BudgetMeter
from research_agent.agent.runtime import Cache, CallRecorder, EventSink
from research_agent.agent.scoring import DomainTiers
from research_agent.config.schema import Settings
from research_agent.gate.config import GateConfig
from research_agent.pii.masking import Masker
from research_agent.prompting.predict import Predictor
from research_agent.prompting.registry import PromptRegistry, RunPrompts
from research_agent.prompting.skills import Skill
from research_agent.providers.embeddings import Embedder
from research_agent.providers.fetch import ContentFetcher
from research_agent.providers.llm.gateway import LLMGateway
from research_agent.providers.search.gateway import SearchGateway


async def _not_cancelled() -> bool:
    return False


async def _no_progress(_: dict[str, Any]) -> None:
    return None


@dataclass
class AgentDeps:
    settings: Settings
    llm: LLMGateway
    search: SearchGateway
    fetcher: ContentFetcher
    embedder: Embedder
    registry: PromptRegistry
    prompts: RunPrompts
    predictor: Predictor
    skills: dict[str, Skill]
    tiers: DomainTiers
    gate: GateConfig
    masker: Masker
    meter: BudgetMeter
    events: EventSink
    recorder: CallRecorder
    cache: Cache
    today: date
    is_cancelled: Callable[[], Awaitable[bool]] = _not_cancelled
    report_progress: Callable[[dict[str, Any]], Awaitable[None]] = _no_progress
    """Pushes live counters to the run row after every node (UI shows them)."""
    extras: dict[str, Any] = field(default_factory=dict)
