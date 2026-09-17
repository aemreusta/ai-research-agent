"""An explicit, complete source-entailment verdict is required for each extracted claim."""

from __future__ import annotations

from collections import Counter

from research_agent.agent.deps import AgentDeps
from research_agent.agent.freshness import (
    applicability_passages,
    assess_claim_freshness,
    document_evidence,
)
from research_agent.agent.legal_authority import legal_evidence_gap
from research_agent.agent.runtime import EventSink
from research_agent.agent.state import Claim, Document, ResearchState
from research_agent.prompting.predict import untrusted
from research_agent.prompting.schemas import ClaimValidationOutput
from research_agent.providers.llm.gateway import LLMFailure


async def validate_claims(
    claims: list[Claim],
    document: Document,
    state: ResearchState,
    deps: AgentDeps,
    events: EventSink,
) -> None:
    pending = {f"candidate-{index}": claim for index, claim in enumerate(claims)}
    for _ in range(2):
        if not pending:
            break
        try:
            result = await deps.predictor(
                "validate_claims",
                events,
                question=state.question,
                time_scope=state.analysis.time_scope.model_dump(mode="json"),
                today=state.as_of.isoformat(),
                document=untrusted(
                    f"TITLE: {document.title}\nPUBLISHED: {document.published_at or 'unknown'}\n"
                    + document_evidence(document, " ".join(c.text for c in claims)),
                    id=document.id,
                    url=document.canonical_url,
                ),
                claims=[
                    {
                        "claim_id": key,
                        **claim.model_dump(exclude={"id"}),
                        "source_applicability_passages": applicability_passages(document, claim),
                    }
                    for key, claim in pending.items()
                ],
            )
        except LLMFailure:
            continue
        output: ClaimValidationOutput = result.value
        counts = Counter(item.claim_id for item in output.items)
        for item in output.items:
            if item.claim_id not in pending or counts[item.claim_id] != 1:
                continue
            claim = pending.pop(item.claim_id)
            unrepresented_scope = (
                bool(applicability_passages(document, claim)) and not claim.conditions
            )
            claim.validation_status = (
                "supported"
                if item.supported and item.conditions_complete and not unrepresented_scope
                else "unsupported"
            )
            claim.validation_reason = (
                "The source restricts the same provision, but the claim has no applicability "
                "conditions. Re-extract the scoped claim before using it."
                if unrepresented_scope
                else item.reason
            )
            claim.time_sensitive = claim.time_sensitive or item.time_sensitive
            assess_claim_freshness(claim, document, state)
            if reason := legal_evidence_gap(claim, document, state):
                claim.validation_status = "unsupported"
                claim.validation_reason = reason
    for claim in pending.values():
        claim.validation_status = "unavailable"
        claim.validation_reason = "No complete source-entailment verdict after one retry."
