"""Typed schema for `config/settings.yaml`.

One schema serves three jobs (architecture v0.6 §3):
  * it validates the YAML at startup,
  * it generates the UI form through `/api/config/schema`,
  * it enforces the same bounds again when a per-run override arrives.

Fields are declared with `tunable()` or `locked()`. Locked fields are security relevant - the PII
boundaries and the output gate cannot be switched off from the UI - and the loader rejects any
override that targets one.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.fields import FieldInfo

UI_KEY = "ui"
GROUP_KEY = "group"
LOCKED_KEY = "locked"


def tunable(default: Any, *, group: str, description: str, **kwargs: Any) -> Any:
    """A field the UI may override per run, within the declared bounds."""
    return Field(
        default,
        description=description,
        json_schema_extra={UI_KEY: True, GROUP_KEY: group, LOCKED_KEY: False},
        **kwargs,
    )


def locked(default: Any, *, group: str, description: str, **kwargs: Any) -> Any:
    """A field the UI may not touch, because turning it off would weaken a safety guarantee."""
    return Field(
        default,
        description=description,
        json_schema_extra={UI_KEY: False, GROUP_KEY: group, LOCKED_KEY: True},
        **kwargs,
    )


class _Section(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


PositiveInt = Annotated[int, Field(gt=0)]
Probability = Annotated[float, Field(ge=0.0, le=1.0)]


class BudgetSettings(_Section):
    """Hard limits and round width (v0.6 §7, D11).

    Round width, not the search cap, is what actually keeps the loop bounded: the first round is
    wide, follow-up rounds only chase missing facets and unresolved conflicts.
    """

    max_iterations: int = tunable(
        4, group="Budget", description="Research rounds: 1 initial + N follow-ups.", ge=1, le=10
    )
    max_searches: int = tunable(
        45, group="Budget", description="Total search calls per run.", ge=1, le=200
    )
    max_wall_clock_seconds: int | None = tunable(
        None,
        group="Budget",
        description="Wall-clock budget. Disabled by default; the counter always runs.",
        ge=30,
        le=7200,
    )
    max_cost_usd: float | None = tunable(
        None,
        group="Budget",
        description="Cost budget in USD. Disabled by default; the counter always runs.",
        gt=0,
        le=100,
    )
    queries_per_subquestion_first_round: int = tunable(
        3, group="Budget", description="Queries per open sub-question in round 1.", ge=1, le=5
    )
    queries_per_gap_followup_round: int = tunable(
        1,
        group="Budget",
        description="Queries per missing facet or unresolved conflict in later rounds.",
        ge=1,
        le=3,
    )
    max_queries_per_followup_round: int = tunable(
        8,
        group="Budget",
        description="Ceiling on queries in a single follow-up round.",
        ge=1,
        le=20,
    )
    stagnation_threshold: int = tunable(
        2,
        group="Budget",
        description="Rounds without a new cluster or origin before a sub-question is exhausted.",
        ge=1,
        le=5,
    )


class PlanSettings(_Section):
    min_subquestions: int = tunable(3, group="Planning", description="Lower bound.", ge=1, le=10)
    max_subquestions: int = tunable(6, group="Planning", description="Upper bound.", ge=1, le=12)
    max_facets_per_subquestion: int = tunable(
        5, group="Planning", description="Answered-criteria checklist size.", ge=1, le=10
    )

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.min_subquestions > self.max_subquestions:
            raise ValueError("min_subquestions cannot exceed max_subquestions")
        return self


class FetchSettings(_Section):
    """Snippet-first triage: only the top-K documents are fetched in full (D7)."""

    top_k_first_round: int = tunable(
        5, group="Retrieval", description="Documents fetched in full in round 1.", ge=1, le=20
    )
    top_k_followup_round: int = tunable(
        3, group="Retrieval", description="Documents fetched in full in later rounds.", ge=1, le=20
    )
    timeout_seconds: float = tunable(
        10.0, group="Retrieval", description="Per-document fetch timeout.", gt=0, le=120
    )
    retries: int = tunable(1, group="Retrieval", description="Fetch retries.", ge=0, le=5)
    max_content_bytes: PositiveInt = tunable(
        2_000_000, group="Retrieval", description="Truncation limit for fetched content."
    )


class DedupSettings(_Section):
    """The four dedup layers (v0.6 §8). Corroboration counts origins, never URLs."""

    minhash_num_perm: PositiveInt = tunable(
        128, group="Deduplication", description="MinHash permutations."
    )
    minhash_shingle_size: int = tunable(
        5, group="Deduplication", description="Word shingle size.", ge=2, le=12
    )
    minhash_jaccard_threshold: Probability = tunable(
        0.8, group="Deduplication", description="L2 near-duplicate document threshold."
    )
    claim_similarity_threshold: Probability = tunable(
        0.86, group="Deduplication", description="L3 claim cosine threshold."
    )
    claim_lexical_fallback_threshold: Probability = tunable(
        0.7, group="Deduplication", description="L3 threshold without an embedding provider."
    )
    query_similarity_threshold: Probability = tunable(
        0.9, group="Deduplication", description="L4 query repetition threshold."
    )
    syndication_min_shared_quotes: int = tunable(
        2,
        group="Deduplication",
        description="Verbatim quotes two pages must share to count as one origin.",
        ge=1,
        le=10,
    )
    syndication_min_quote_words: int = tunable(
        12,
        group="Deduplication",
        description="Shortest quote (in words) that counts towards syndication.",
        ge=5,
        le=60,
    )


class ScoringWeights(_Section):
    authority: Probability = tunable(0.35, group="Scoring", description="Domain tier weight.")
    primary: Probability = tunable(0.25, group="Scoring", description="Primary source weight.")
    recency: Probability = tunable(0.15, group="Scoring", description="Recency weight.")
    relevance: Probability = tunable(0.25, group="Scoring", description="Relevance weight.")

    @model_validator(mode="after")
    def _sum_to_one(self) -> Self:
        total = self.authority + self.primary + self.recency + self.relevance
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"scoring weights must sum to 1.0, got {total:.4f}")
        return self


class ScoringSettings(_Section):
    weights: ScoringWeights = Field(default_factory=ScoringWeights)
    primary_source_min_score: Probability = tunable(
        0.7,
        group="Scoring",
        description="A facet is sufficient with one primary source at or above this score.",
    )
    min_independent_origins: int = tunable(
        2,
        group="Scoring",
        description="...or with this many independent origins (v0.6 §7).",
        ge=1,
        le=5,
    )
    unknown_domain_tier: Literal[1, 2, 3] = tunable(
        3, group="Scoring", description="Tier assigned to unknown domains."
    )


class ContradictionSettings(_Section):
    numeric_relative_tolerance: Probability = tunable(
        0.05, group="Contradiction", description="Relative gap above which values conflict."
    )
    date_granularity_days: int = tunable(
        31,
        group="Contradiction",
        description="Date distance treated as the same point in time.",
        ge=0,
        le=366,
    )


class ConcurrencySettings(_Section):
    max_parallel_searches: int = tunable(
        5, group="Concurrency", description="Search fan-out.", ge=1, le=20
    )
    max_parallel_fetches: int = tunable(
        5, group="Concurrency", description="Fetch fan-out.", ge=1, le=20
    )
    max_parallel_llm_calls: int = tunable(
        4, group="Concurrency", description="LLM fan-out.", ge=1, le=20
    )
    citation_verification_batch_size: int = tunable(
        10,
        group="Concurrency",
        description="Sentences per entailment call - batching keeps verification cheap (D34).",
        ge=1,
        le=50,
    )


class LlmSettings(_Section):
    reasoning_model: str = tunable(
        "",
        group="Models",
        description="Planning, synthesis and evidence verification. Empty = automatic.",
    )
    fast_model: str = tunable(
        "",
        group="Models",
        description="Search queries, source scoring and claim extraction. Empty = automatic.",
    )
    allow_fallback: bool = tunable(
        True,
        group="Models",
        description="Allow another provider on failure. Switches appear in the timeline.",
    )
    timeout_seconds: float = tunable(
        60.0, group="LLM", description="Per-call timeout.", gt=0, le=600
    )
    retries: int = tunable(
        2, group="LLM", description="Retries before the next provider.", ge=0, le=5
    )
    repair_retries: int = tunable(
        1, group="LLM", description="Structured-output repair attempts.", ge=0, le=3
    )


class SearchSettings(_Section):
    timeout_seconds: float = tunable(
        15.0, group="Search", description="Per-query timeout.", gt=0, le=120
    )
    retries: int = tunable(
        2, group="Search", description="Retries before the fallback provider.", ge=0, le=5
    )
    results_per_query: int = tunable(
        10, group="Search", description="Results requested per query.", ge=1, le=50
    )
    cache_ttl_seconds: PositiveInt = tunable(
        86_400, group="Search", description="Search cache TTL."
    )


class PiiSettings(_Section):
    """Sensitive identifiers are always masked; that switch is not the user's to flip (v0.6 §12)."""

    enabled: bool = locked(True, group="PII", description="Masking of sensitive identifiers.")
    mask_question_person_names: bool = tunable(
        False,
        group="PII",
        description="Mask person names in the question too. Web content is never name-masked.",
    )
    presidio_timeout_seconds: float = tunable(
        5.0, group="PII", description="Presidio HTTP timeout.", gt=0, le=60
    )
    score_threshold: Probability = tunable(
        0.5, group="PII", description="Minimum recogniser confidence."
    )


class GateSettings(_Section):
    """The output gate is the last word before a report reaches the user (v0.6 §11)."""

    enabled: bool = locked(True, group="Output gate", description="Deterministic output gate.")
    max_remediation_rounds: int = tunable(
        1, group="Output gate", description="Deterministic remediation passes.", ge=0, le=3
    )


class LoggingSettings(_Section):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = tunable(
        "INFO", group="Logging", description="Log level."
    )
    format: Literal["json", "console"] = tunable(
        "json", group="Logging", description="json in containers, console for local debugging."
    )


class Settings(BaseModel):
    """The effective agent configuration for one run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    budget: BudgetSettings = Field(default_factory=BudgetSettings)
    plan: PlanSettings = Field(default_factory=PlanSettings)
    fetch: FetchSettings = Field(default_factory=FetchSettings)
    dedup: DedupSettings = Field(default_factory=DedupSettings)
    scoring: ScoringSettings = Field(default_factory=ScoringSettings)
    contradiction: ContradictionSettings = Field(default_factory=ContradictionSettings)
    concurrency: ConcurrencySettings = Field(default_factory=ConcurrencySettings)
    llm: LlmSettings = Field(default_factory=LlmSettings)
    search: SearchSettings = Field(default_factory=SearchSettings)
    pii: PiiSettings = Field(default_factory=PiiSettings)
    gate: GateSettings = Field(default_factory=GateSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)


class TunableField(BaseModel):
    """One row of the UI form, derived from the schema rather than hand-maintained."""

    model_config = ConfigDict(frozen=True)

    path: str
    group: str
    description: str
    annotation: str
    default: Any = None
    locked: bool = False
    minimum: float | None = None
    maximum: float | None = None


def _bounds(info: FieldInfo) -> tuple[float | None, float | None]:
    """Numeric bounds from `Field(ge=..., le=...)`, so the form renders the same limits."""
    minimum: float | None = None
    maximum: float | None = None
    for constraint in info.metadata:
        for attribute in ("ge", "gt"):
            if (value := getattr(constraint, attribute, None)) is not None:
                minimum = float(value)
        for attribute in ("le", "lt"):
            if (value := getattr(constraint, attribute, None)) is not None:
                maximum = float(value)
    return minimum, maximum


def _extra(info: FieldInfo) -> dict[str, Any]:
    extra = info.json_schema_extra
    return extra if isinstance(extra, dict) else {}


def _walk(model: type[BaseModel], prefix: str = "") -> list[TunableField]:
    rows: list[TunableField] = []
    for name, info in model.model_fields.items():
        path = f"{prefix}{name}"
        annotation = info.annotation
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            rows.extend(_walk(annotation, f"{path}."))
            continue
        extra = _extra(info)
        minimum, maximum = _bounds(info)
        rows.append(
            TunableField(
                path=path,
                group=str(extra.get(GROUP_KEY, "Other")),
                description=info.description or "",
                annotation=str(annotation),
                default=info.get_default(call_default_factory=False),
                locked=bool(extra.get(LOCKED_KEY, False)),
                minimum=minimum,
                maximum=maximum,
            )
        )
    return rows


def tunable_fields(*, include_locked: bool = False) -> list[TunableField]:
    """Flat field list for `/api/config/schema`; locked fields are hidden unless asked for."""
    rows = _walk(Settings)
    return rows if include_locked else [row for row in rows if not row.locked]


def is_locked(path: str) -> bool:
    """Whether a dotted settings path is off limits to per-run overrides."""
    return any(row.path == path and row.locked for row in _walk(Settings))


def known_paths() -> frozenset[str]:
    return frozenset(row.path for row in _walk(Settings))
