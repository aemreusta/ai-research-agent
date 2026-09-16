"""Explainable source scoring (architecture v0.6 §9).

    source_score = 0.35·authority + 0.25·primary + 0.15·recency + 0.25·relevance

Authority comes from `config/domain_tiers.yaml`, never from page content: an LLM may nudge it
within the tier band, but a page cannot talk its way from tier 3 to tier 1 (§11.4). Every score
carries a one-line rationale that goes onto the timeline:

    kvkk.gov.tr → 0.91 (T1, primary, 2026-03, relevance 0.85)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from functools import cache
from pathlib import Path
from typing import Any

import yaml

from research_agent.agent.state import SourceScore, TimeScope
from research_agent.agent.text import fold
from research_agent.config.schema import ScoringSettings
from research_agent.paths import config_dir

# How far an LLM may move authority inside a tier, and how far below the next tier it must stay.
_MAX_NUDGE = 0.1
_TIER_GAP = 0.05
_LEGAL_SUFFIXES = (
    "inc",
    "ltd",
    "llc",
    "gmbh",
    "as",
    "a.s",
    "a.ş",
    "corp",
    "co",
    "ai",
    "io",
    "com",
    "tr",
    "net",
    "org",
)


@dataclass(frozen=True)
class DomainTiers:
    tier_scores: dict[int, float]
    tier1_domains: frozenset[str]
    tier1_suffixes: tuple[str, ...]
    tier2_domains: frozenset[str]
    tier3_markers: frozenset[str]
    self_primary: bool = True
    extra: dict[int, frozenset[str]] = field(default_factory=dict)

    @classmethod
    def load(cls, directory: Path | None = None) -> DomainTiers:
        return _load(directory or config_dir())

    def extended(self, additions: dict[int, list[str]]) -> DomainTiers:
        """A copy with skill-provided domains added (skills never remove or demote)."""
        merged = {tier: set(domains) for tier, domains in self.extra.items()}
        for tier, domains in additions.items():
            merged.setdefault(int(tier), set()).update(d.lower() for d in domains)
        return DomainTiers(
            tier_scores=self.tier_scores,
            tier1_domains=self.tier1_domains,
            tier1_suffixes=self.tier1_suffixes,
            tier2_domains=self.tier2_domains,
            tier3_markers=self.tier3_markers,
            self_primary=self.self_primary,
            extra={tier: frozenset(domains) for tier, domains in merged.items()},
        )

    def tier_score(self, tier: int) -> float:
        return self.tier_scores.get(tier, self.tier_scores.get(3, 0.3))

    def tier_of(self, domain: str) -> int:
        host = domain.lower()

        def listed(domains: frozenset[str]) -> bool:
            return any(host == item or host.endswith("." + item) for item in domains)

        if listed(self.tier3_markers):
            return 3
        if listed(self.tier1_domains) or listed(self.extra.get(1, frozenset())):
            return 1
        if any(host.endswith(suffix) for suffix in self.tier1_suffixes):
            return 1
        if listed(self.tier2_domains) or listed(self.extra.get(2, frozenset())):
            return 2
        return 3


@cache
def _load(directory: Path) -> DomainTiers:
    with (directory / "domain_tiers.yaml").open(encoding="utf-8") as handle:
        raw: dict[str, Any] = yaml.safe_load(handle)
    tier1 = raw.get("tier_1", {})
    return DomainTiers(
        tier_scores={int(k): float(v) for k, v in raw["tier_scores"].items()},
        tier1_domains=frozenset(d.lower() for d in tier1.get("domains", [])),
        tier1_suffixes=tuple(s.lower() for s in tier1.get("suffixes", [])),
        tier2_domains=frozenset(d.lower() for d in raw.get("tier_2", {}).get("domains", [])),
        tier3_markers=frozenset(d.lower() for d in raw.get("tier_3_markers", [])),
        self_primary=bool(raw.get("self_primary_for_own_claims", True)),
    )


def _entity_key(name: str) -> str:
    words = "".join(c if c.isalnum() else " " for c in fold(name)).split()
    while words and words[-1] in _LEGAL_SUFFIXES:
        words.pop()
    key = "".join(words)
    for suffix in ("ai", "io"):
        if key.endswith(suffix) and len(key) > len(suffix) + 2:
            key = key[: -len(suffix)]
    return key


def is_own_domain(domain: str, entities: list[str]) -> bool:
    """`apilex.ai` is the company's own site for claims about "ApilexAI"."""
    labels = [label for label in fold(domain).split(".") if label not in _LEGAL_SUFFIXES]
    host_key = "".join(labels[-1:]) if labels else ""
    if len(host_key) < 3:
        return False
    for entity in entities:
        key = _entity_key(entity)
        if len(key) >= 3 and (
            key == host_key or key.startswith(host_key) or host_key.startswith(key)
        ):
            return True
    return False


def recency_score(published_at: date | None, as_of: date, scope: TimeScope) -> float:
    """1.0 for fresh, in-scope content; decays with age; out-of-scope content is penalised."""
    if published_at is None:
        return 0.4  # unknown: slightly below neutral
    if scope.end is not None and published_at > scope.end:
        return 0.5
    if scope.start is not None and published_at < scope.start:
        return 0.2
    age = (as_of - published_at).days
    if age < 0:
        return 0.6  # dated in the future: probably a scheduled or mis-dated page
    if age <= 90:
        return 1.0
    if age <= 365:
        return 0.8
    if age <= 730:
        return 0.6
    return 0.3


def score_source(
    *,
    domain: str,
    published_at: date | None,
    as_of: date,
    scope: TimeScope,
    relevance: float,
    entities: list[str],
    tiers: DomainTiers,
    settings: ScoringSettings,
    authority_adjustment: float = 0.0,
    llm_primary: bool | None = None,
    llm_scored: bool = False,
    reason: str = "",
) -> SourceScore:
    tier = tiers.tier_of(domain)
    base = tiers.tier_score(tier)
    nudge = max(-_MAX_NUDGE, min(_MAX_NUDGE, authority_adjustment))
    ceiling = 1.0 if tier == 1 else tiers.tier_score(tier - 1) - _TIER_GAP
    authority = max(0.0, min(ceiling, base + nudge))

    rule_primary = tier == 1 or (tiers.self_primary and is_own_domain(domain, entities))
    # The LLM may recognise a primary source the rules miss (a ministry PDF on a CDN), but only
    # outside tier 3: blogs and forums stay secondary whatever the page claims about itself.
    is_primary = rule_primary or (bool(llm_primary) and tier <= 2)

    recency = recency_score(published_at, as_of, scope)
    relevance = max(0.0, min(1.0, relevance))
    weights = settings.weights
    total = (
        weights.authority * authority
        + weights.primary * (1.0 if is_primary else 0.0)
        + weights.recency * recency
        + weights.relevance * relevance
    )
    dated = published_at.strftime("%Y-%m") if published_at else "undated"
    parts = [
        f"T{tier}",
        "primary" if is_primary else "secondary",
        dated,
        f"relevance {relevance:.2f}",
    ]
    rationale = f"{domain} → {total:.2f} ({', '.join(parts)})"
    if reason:
        rationale = f"{rationale} - {reason}"
    return SourceScore(
        authority=round(authority, 4),
        primary=1.0 if is_primary else 0.0,
        recency=round(recency, 4),
        relevance=round(relevance, 4),
        total=round(total, 4),
        tier=tier,
        is_primary=is_primary,
        rationale=rationale,
        llm_scored=llm_scored,
    )
