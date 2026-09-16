"""The research state, built around the claim ledger (architecture v0.6 §4, §6).

    SearchHit -> Document -> Claim (+ verbatim quote) -> ClaimCluster -> finding in the report

One data model answers four requirements at once: duplicates collapse into one origin and one
cluster; corroboration is the number of independent origins in a cluster (never URLs); a
contradiction is two clusters disagreeing on the same (entity, attribute); and a citation is a
cluster id whose claims, quotes and sources are already known.

The whole state is plain JSON-serialisable Pydantic, which is also what the LangGraph checkpoint
stores - no pickles, so a resumed run survives a code change that keeps the schema.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=False)


# --- analysis and plan -------------------------------------------------------------------------


class TimeScope(_Model):
    """A question's time frame, turned into absolute dates at intake ("2026", "son gelişmeler")."""

    start: date | None = None
    end: date | None = None
    description: str = ""


class QueryAnalysis(_Model):
    language: str = "en"
    intent: str = ""
    entities: list[str] = Field(default_factory=list)
    time_scope: TimeScope = Field(default_factory=TimeScope)
    answer_type: str = "overview"
    domain: str = "general"
    source_hints: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    rationale: str = ""
    fallback: bool = False


class SubQuestionStatus(StrEnum):
    PENDING = "pending"
    SEARCHING = "searching"
    SUFFICIENT = "sufficient"
    EXHAUSTED = "exhausted"


class FacetStatus(StrEnum):
    OPEN = "open"
    SUFFICIENT = "sufficient"
    CONTESTED = "contested"


class Facet(_Model):
    """One "answered when..." criterion of a sub-question."""

    id: str
    name: str
    description: str = ""
    status: FacetStatus = FacetStatus.OPEN


class SubQuestion(_Model):
    id: str
    text: str
    priority: Literal["must", "nice"] = "must"
    facets: list[Facet] = Field(default_factory=list)
    expected_sources: list[str] = Field(default_factory=list)
    status: SubQuestionStatus = SubQuestionStatus.PENDING
    queries_tried: list[str] = Field(default_factory=list)
    rounds_without_progress: int = 0
    exhausted_reason: str | None = None

    @property
    def open(self) -> bool:
        return self.status in (SubQuestionStatus.PENDING, SubQuestionStatus.SEARCHING)

    def missing_facets(self) -> list[Facet]:
        return [facet for facet in self.facets if facet.status is not FacetStatus.SUFFICIENT]


class QueryPurpose(StrEnum):
    INITIAL = "initial"
    GAP = "gap"
    CONFLICT = "conflict"
    BROADENED = "broadened"


class QueryStatus(StrEnum):
    PLANNED = "planned"
    DONE = "done"
    EMPTY = "empty"
    FAILED = "failed"
    SKIPPED_BUDGET = "skipped_budget"


class QueryRecord(_Model):
    id: str
    text: str
    subq_id: str
    facet_id: str | None = None
    purpose: QueryPurpose = QueryPurpose.INITIAL
    rationale: str = ""
    iteration: int
    status: QueryStatus = QueryStatus.PLANNED
    provider: str | None = None
    result_count: int = 0
    cache_hit: bool = False
    error_code: str | None = None


# --- sources -----------------------------------------------------------------------------------


class SourceScore(_Model):
    """Transparent weighted score (v0.6 §9); the components are shown in the UI."""

    authority: float = 0.0
    primary: float = 0.0
    recency: float = 0.0
    relevance: float = 0.0
    total: float = 0.0
    tier: int = 3
    is_primary: bool = False
    rationale: str = ""
    llm_scored: bool = False


class Document(_Model):
    id: str
    url: str
    canonical_url: str
    domain: str
    title: str = ""
    snippet: str = ""
    content: str = ""
    content_hash: str = ""
    published_at: date | None = None
    provider: str | None = None
    subq_ids: list[str] = Field(default_factory=list)
    query_ids: list[str] = Field(default_factory=list)
    origin_id: str = ""
    """Documents with (near-)identical text share an origin; corroboration counts origins."""
    minhash: list[int] = Field(default_factory=list, repr=False)
    fetched: bool = False
    fetch_error: str | None = None
    extracted: bool = False
    selected_round: int | None = None
    """The round in which triage picked this document for scoring and extraction."""
    triage_score: float = 0.0
    score: SourceScore = Field(default_factory=SourceScore)
    first_seen_iteration: int = 1


# --- the ledger --------------------------------------------------------------------------------


class ClaimKind(StrEnum):
    FACT = "fact"
    NUMBER = "number"
    DATE = "date"
    EVENT = "event"
    DEFINITION = "definition"
    OPINION = "opinion"
    FORECAST = "forecast"


class Claim(_Model):
    id: str
    subq_id: str
    facet_id: str | None = None
    doc_id: str
    origin_id: str
    text: str
    quote: str
    kind: ClaimKind = ClaimKind.FACT
    entity: str | None = None
    attribute: str | None = None
    value: str | None = None
    unit: str | None = None
    as_of: str | None = None
    attributed_to: str | None = None
    """"X'in açıklamasına göre..." - the original speaker, which becomes the origin (§8)."""
    iteration: int = 1


class ClusterStatus(StrEnum):
    SUPPORTED = "supported"
    SINGLE_SOURCE = "single_source"
    CONTESTED = "contested"


class ClaimCluster(_Model):
    id: str
    subq_id: str
    claim_ids: list[str]
    doc_ids: list[str]
    origin_ids: list[str]
    facet_ids: list[str] = Field(default_factory=list)
    statement: str
    entity: str | None = None
    attribute: str | None = None
    value: str | None = None
    unit: str | None = None
    as_of: str | None = None
    kind: ClaimKind = ClaimKind.FACT
    confidence: float = 0.0
    best_source_score: float = 0.0
    has_primary: bool = False
    status: ClusterStatus = ClusterStatus.SINGLE_SOURCE

    @property
    def support(self) -> int:
        return len(self.origin_ids)


class ContradictionKind(StrEnum):
    TRUE_CONFLICT = "true_conflict"
    DIFFERENT_TIME = "different_time"
    DIFFERENT_SCOPE = "different_scope"
    ROUNDING = "rounding"
    CONSISTENT = "consistent"
    """Worded differently but saying the same thing - not a disagreement at all."""


class Contradiction(_Model):
    id: str
    cluster_ids: list[str]
    subq_id: str
    kind: ContradictionKind
    summary: str
    rationale: str = ""
    preferred_cluster_id: str | None = None
    resolved: bool = False
    followup_attempted: bool = False
    iteration: int = 1


# --- report ------------------------------------------------------------------------------------


class SentenceKind(StrEnum):
    FACT = "fact"
    RECOMMENDATION = "recommendation"
    META = "meta"
    """Framing that asserts nothing checkable ("This report covers..."); exempt from G2."""


class ReportSentence(_Model):
    text: str
    kind: SentenceKind = SentenceKind.FACT
    cluster_ids: list[str] = Field(default_factory=list)
    finding_refs: list[str] = Field(
        default_factory=list, description="For recommendations: the cluster ids they build on."
    )
    labels: list[str] = Field(default_factory=list)


class SectionKey(StrEnum):
    SUMMARY = "summary"
    KEY_FINDINGS = "key_findings"
    CONFLICTING = "conflicting_or_uncertain"
    CONCLUSION = "conclusion"
    RECOMMENDATIONS = "recommendations"
    KNOWN_GAPS = "known_gaps"
    SOURCES = "sources"


class ReportSection(_Model):
    key: SectionKey
    title: str
    sentences: list[ReportSentence] = Field(default_factory=list)


class SourceEntry(_Model):
    number: int
    doc_id: str
    url: str
    title: str
    domain: str
    score: float


class Report(_Model):
    title: str
    language: str
    sections: list[ReportSection]
    sources: list[SourceEntry] = Field(default_factory=list)
    fallback: bool = False

    def section(self, key: SectionKey) -> ReportSection | None:
        return next((section for section in self.sections if section.key is key), None)

    def sentences(self) -> list[tuple[ReportSection, ReportSentence]]:
        return [(section, sentence) for section in self.sections for sentence in section.sentences]


class PendingHit(_Model):
    """A search result waiting for `process_results` - transient, cleared every round."""

    hit: dict[str, object]
    query_id: str
    subq_id: str


# --- the run -----------------------------------------------------------------------------------


class StopReason(StrEnum):
    SUFFICIENT = "sufficient"
    BUDGET = "budget"
    MAX_ITERATIONS = "max_iterations"
    NO_PROGRESS = "no_progress"
    NO_EVIDENCE = "no_evidence"
    CANCELLED = "cancelled"


class RouteRecord(_Model):
    stop: bool
    reason: StopReason | None = None
    rule: str = ""
    detail: str = ""


class ResearchState(_Model):
    run_id: uuid.UUID
    question: str
    language: str = "en"
    as_of: date
    analysis: QueryAnalysis = Field(default_factory=QueryAnalysis)
    plan: list[SubQuestion] = Field(default_factory=list)
    queries: list[QueryRecord] = Field(default_factory=list)
    documents: dict[str, Document] = Field(default_factory=dict)
    claims: dict[str, Claim] = Field(default_factory=dict)
    clusters: dict[str, ClaimCluster] = Field(default_factory=dict)
    contradictions: list[Contradiction] = Field(default_factory=list)
    iteration: int = 0
    stop_reason: StopReason | None = None
    stop_detail: str = ""
    skills: list[str] = Field(default_factory=list)
    report: Report | None = None
    report_markdown: str = ""
    gate_result: dict[str, Any] | None = None
    verification: dict[str, object] = Field(default_factory=dict)
    counters: dict[str, int] = Field(default_factory=dict)
    """Dropped claims, rejected quotes, failed fetches... - surfaced in the report metadata."""
    budget: dict[str, float | int] = Field(default_factory=dict)
    started_at: datetime | None = None
    pending_hits: list[PendingHit] = Field(default_factory=list)
    round_snapshot: dict[str, list[int]] = Field(default_factory=dict)
    route: RouteRecord | None = None
    resynthesized: bool = False
    skill_domains: dict[int, list[str]] = Field(default_factory=dict)

    # -- helpers used by nodes and the gate --

    def subquestion(self, subq_id: str) -> SubQuestion | None:
        return next((subq for subq in self.plan if subq.id == subq_id), None)

    def open_subquestions(self) -> list[SubQuestion]:
        return [subq for subq in self.plan if subq.open]

    def must_subquestions(self) -> list[SubQuestion]:
        return [subq for subq in self.plan if subq.priority == "must"]

    def queries_for(self, subq_id: str) -> list[QueryRecord]:
        return [query for query in self.queries if query.subq_id == subq_id]

    def clusters_for(self, subq_id: str) -> list[ClaimCluster]:
        return [cluster for cluster in self.clusters.values() if cluster.subq_id == subq_id]

    def origins_for(self, subq_id: str) -> set[str]:
        return {origin for cluster in self.clusters_for(subq_id) for origin in cluster.origin_ids}

    def bump(self, counter: str, amount: int = 1) -> None:
        self.counters[counter] = self.counters.get(counter, 0) + amount

    def next_id(self, prefix: str, existing: int) -> str:
        return f"{prefix}{existing + 1}"
