"""The LLM steps of the agent, declared once.

A signature says what goes in (template variables), what comes out (a Pydantic model) and which
tier runs it. The words live in `config/prompts/<id>.yaml` (or Langfuse) and are checked against
this declaration before use.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

from research_agent.prompting import schemas
from research_agent.providers.llm.catalog import Tier
from research_agent.providers.llm.schema import schema_hash


@dataclass(frozen=True)
class Signature:
    id: str
    output: type[BaseModel]
    tier: Tier
    inputs: frozenset[str]
    uses_skills: bool = False

    @property
    def schema_hash(self) -> str:
        return schema_hash(self.output)


SIGNATURES: dict[str, Signature] = {
    sig.id: sig
    for sig in (
        Signature(
            "analyze_query",
            schemas.AnalyzeOutput,
            Tier.REASONING,
            frozenset({"question", "today", "skills_menu"}),
        ),
        Signature(
            "plan",
            schemas.PlanOutput,
            Tier.REASONING,
            frozenset(
                {"question", "analysis", "min_subquestions", "max_subquestions", "max_facets"}
            ),
            uses_skills=True,
        ),
        Signature(
            "generate_queries",
            schemas.QueriesOutput,
            Tier.FAST,
            frozenset(
                {"question", "language", "today", "round", "targets", "tried_queries", "limit"}
            ),
            uses_skills=True,
        ),
        Signature(
            "evaluate_sources",
            schemas.SourceJudgements,
            Tier.FAST,
            frozenset({"question", "subquestions", "documents"}),
            uses_skills=True,
        ),
        Signature(
            "extract_claims",
            schemas.ExtractionOutput,
            Tier.FAST,
            frozenset({"question", "subquestions", "document", "today"}),
        ),
        Signature(
            "judge_contradictions",
            schemas.ContradictionJudgements,
            Tier.FAST,
            frozenset({"question", "language", "pairs"}),
        ),
        Signature(
            "assess_coverage",
            schemas.CoverageOutput,
            Tier.REASONING,
            frozenset({"question", "status"}),
        ),
        Signature(
            "synthesize",
            schemas.SynthesisOutput,
            Tier.REASONING,
            frozenset(
                {
                    "question",
                    "language",
                    "today",
                    "ledger",
                    "gaps",
                    "contradictions",
                    "answer_type",
                    "feedback",
                }
            ),
            uses_skills=True,
        ),
        Signature(
            "verify_citations", schemas.VerificationOutput, Tier.FAST, frozenset({"sentences"})
        ),
    )
}
