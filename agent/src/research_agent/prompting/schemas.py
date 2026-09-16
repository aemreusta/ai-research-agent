"""Output models for every LLM step. Each one carries a short `rationale`.

The rationale is not the model's chain of thought; it is the auditable reason for a decision,
and it is what the run timeline shows as "reasoning" (architecture v0.6 §5, D25).
Field descriptions double as instructions: both providers pass them to the model with the schema.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Rationale = Field(description="One or two sentences: why this output. Shown to the user.")


# --- analyze_query -----------------------------------------------------------------------------


class TimeScopeOut(BaseModel):
    start: str | None = Field(None, description="ISO date (YYYY-MM-DD) the question starts from.")
    end: str | None = Field(None, description="ISO date the question ends at.")
    description: str = Field("", description="The scope in words, e.g. 'calendar year 2026'.")


class AnalyzeOutput(BaseModel):
    rationale: str = Rationale
    language: Literal["tr", "en"] = Field(description="Language of the question.")
    intent: str = Field(description="What the user wants to know or decide, in one sentence.")
    entities: list[str] = Field(
        description="Organisations, laws, products, people named or implied."
    )
    time_scope: TimeScopeOut
    answer_type: Literal["overview", "comparison", "timeline", "figure", "action_plan", "profile"]
    domain: str = Field(description="Subject area, e.g. 'data protection law', 'company research'.")
    source_hints: list[str] = Field(
        description="Kinds of primary sources that would settle the question."
    )
    skills: list[str] = Field(
        description="Names of 0-2 skills from the menu that fit this question. Empty if none fit."
    )


# --- plan --------------------------------------------------------------------------------------


class FacetOut(BaseModel):
    name: str = Field(description="Short noun phrase, e.g. 'effective date'.")
    description: str = Field(description="What an answer must contain for this facet.")


class SubQuestionOut(BaseModel):
    text: str = Field(description="A self-contained, searchable research question.")
    priority: Literal["must", "nice"]
    facets: list[FacetOut] = Field(description="1-5 checkable criteria that together answer it.")
    expected_sources: list[str] = Field(description="Where the answer is most likely published.")


class PlanOutput(BaseModel):
    rationale: str = Rationale
    subquestions: list[SubQuestionOut]


# --- generate_queries --------------------------------------------------------------------------


class QueryOut(BaseModel):
    subq_id: str
    facet_id: str | None = Field(None, description="The facet this query targets, if any.")
    text: str = Field(description="A web search query, 3-12 words, no quotes unless essential.")
    purpose: Literal["initial", "gap", "conflict"]
    rationale: str = Field(description="Why this query, in a few words.")


class QueriesOutput(BaseModel):
    rationale: str = Rationale
    queries: list[QueryOut]


# --- evaluate_sources --------------------------------------------------------------------------


class SourceJudgement(BaseModel):
    doc_id: str
    relevance: float = Field(ge=0, le=1, description="How directly it addresses the sub-questions.")
    is_primary: bool = Field(
        description="True if the publisher is the origin of the information (regulator, the "
        "company itself, the court) rather than a report about it."
    )
    authority_adjustment: float = Field(
        ge=-0.1, le=0.1, description="Small correction to the domain's authority, -0.1..0.1."
    )
    reason: str = Field(description="A few words explaining the judgement.")


class SourceJudgements(BaseModel):
    rationale: str = Rationale
    items: list[SourceJudgement]


# --- extract_claims ----------------------------------------------------------------------------


class ClaimOut(BaseModel):
    subq_id: str
    facet_id: str | None = None
    text: str = Field(description="One atomic, self-contained factual statement.")
    quote: str = Field(
        description="The exact sentence(s) from the source that state it, copied verbatim."
    )
    kind: Literal["fact", "number", "date", "event", "definition", "opinion", "forecast"]
    entity: str | None = Field(None, description="Who or what the claim is about.")
    attribute: str | None = Field(None, description="Which property, e.g. 'effective date'.")
    value: str | None = Field(None, description="The value as written, e.g. '2 August 2026'.")
    unit: str | None = None
    as_of: str | None = Field(None, description="When the value holds, if stated.")
    attributed_to: str | None = Field(
        None, description="If the source reports someone else's statement, who said it."
    )


class ExtractionOutput(BaseModel):
    rationale: str = Rationale
    claims: list[ClaimOut]


# --- judge_contradictions ----------------------------------------------------------------------


class ContradictionJudgement(BaseModel):
    pair_id: str
    kind: Literal["true_conflict", "different_time", "different_scope", "rounding"]
    summary: str = Field(description="What disagrees, in one sentence, in the report language.")
    preferred: Literal["left", "right", "none"] = Field(
        description="The better-supported side if the evidence clearly favours one, else none."
    )
    rationale: str


class ContradictionJudgements(BaseModel):
    rationale: str = Rationale
    items: list[ContradictionJudgement]


# --- assess_coverage ---------------------------------------------------------------------------


class CoverageItem(BaseModel):
    subq_id: str
    missing: list[FacetOut] = Field(
        description="Facets still unanswered that the current checklist misses. Usually empty."
    )
    note: str = Field(description="What is known and what is still missing, briefly.")


class CoverageOutput(BaseModel):
    rationale: str = Rationale
    items: list[CoverageItem]


# --- synthesize --------------------------------------------------------------------------------


class SentenceOut(BaseModel):
    text: str
    cluster_ids: list[str] = Field(
        description="Ids of the findings that support this sentence. Required for every factual "
        "sentence; empty only for framing sentences that assert nothing."
    )


class RecommendationOut(BaseModel):
    text: str
    finding_refs: list[str] = Field(description="Ids of the findings this action is based on.")


class SynthesisOutput(BaseModel):
    rationale: str = Rationale
    title: str
    summary: list[SentenceOut]
    key_findings: list[SentenceOut]
    conflicting: list[SentenceOut]
    recommendations: list[RecommendationOut]
    conclusion: list[SentenceOut]


# --- verify_citations --------------------------------------------------------------------------


class VerificationItem(BaseModel):
    sentence_id: str
    supported: bool = Field(description="True only if the cited findings entail the sentence.")
    reason: str


class VerificationOutput(BaseModel):
    items: list[VerificationItem]
