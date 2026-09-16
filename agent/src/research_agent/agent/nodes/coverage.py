"""assess_coverage: facet statuses in code, gap notes from the model, then the router decision."""

from __future__ import annotations

from typing import Any

from research_agent.agent.coverage import assess_facets, gain_since, update_progress
from research_agent.agent.deps import AgentDeps
from research_agent.agent.runtime import EventSink
from research_agent.agent.state import (
    Facet,
    FacetStatus,
    ResearchState,
    RouteRecord,
    StopReason,
)
from research_agent.agent.termination import decide
from research_agent.agent.text import token_jaccard
from research_agent.errors import AgentError, ErrorCode
from research_agent.observability.events import EventType
from research_agent.prompting.schemas import CoverageOutput
from research_agent.providers.llm.gateway import LLMFailure


def _status_payload(state: ResearchState) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for subq in state.open_subquestions():
        clusters = state.clusters_for(subq.id)
        payload.append(
            {
                "subq_id": subq.id,
                "question": subq.text,
                "facets": [
                    {
                        "id": f.id,
                        "name": f.name,
                        "covered": f.status is FacetStatus.SUFFICIENT,
                        "status": f.status.value,
                    }
                    for f in subq.facets
                ],
                "findings": [
                    {
                        "id": c.id,
                        "statement": c.statement,
                        "sources": c.support,
                        "facets": c.facet_ids,
                    }
                    for c in clusters[:25]
                ],
            }
        )
    return payload


async def assess_coverage(state: ResearchState, deps: AgentDeps, events: EventSink) -> None:
    assess_facets(state, deps.settings.scoring)

    open_subqs = state.open_subquestions()
    if open_subqs:
        try:
            result = await deps.predictor(
                "assess_coverage", events, question=state.question, status=_status_payload(state)
            )
            output: CoverageOutput = result.value
            limit = deps.settings.plan.max_facets_per_subquestion
            for item in output.items:
                subq = state.subquestion(item.subq_id)
                if subq is None or not subq.open:
                    continue
                for missing in item.missing:
                    if len(subq.facets) >= limit:
                        break
                    if any(token_jaccard(missing.name, f.name) >= 0.6 for f in subq.facets):
                        continue
                    subq.facets.append(
                        Facet(
                            id=f"{subq.id}f{len(subq.facets) + 1}",
                            name=missing.name,
                            description=missing.description,
                        )
                    )
                if item.note:
                    await events.info(
                        EventType.COVERAGE_ASSESSED,
                        item.note,
                        label="Research State",
                        data={"subq_id": subq.id, "rationale": output.rationale},
                    )
        except LLMFailure:
            pass  # the rules below decide on their own
        assess_facets(state, deps.settings.scoring)

    snapshot = {key: (value[0], value[1]) for key, value in state.round_snapshot.items()}
    for subq in state.plan:
        update_progress(
            subq, gain=gain_since(state, snapshot, subq.id), settings=deps.settings.budget
        )
        missing_names = [f.name for f in subq.missing_facets()]
        if subq.open and missing_names:
            await events.info(
                EventType.COVERAGE_ASSESSED,
                f"Missing information: {', '.join(missing_names)}.",
                label="Research State",
                data={"subq_id": subq.id},
            )

    decision = decide(state, deps.meter, deps.settings.budget)
    reason = decision.reason
    if decision.stop and not state.clusters:
        reason = StopReason.NO_EVIDENCE
    state.route = RouteRecord(
        stop=decision.stop, reason=reason, rule=decision.rule, detail=decision.detail
    )
    if decision.stop:
        state.stop_reason = reason
        state.stop_detail = decision.detail
        if reason is StopReason.BUDGET:
            await events.error(
                AgentError(
                    code=ErrorCode.BUDGET_EXCEEDED,
                    node=events.node,
                    decision="stop and synthesise",
                    outcome=decision.detail,
                )
            )
        elif reason is StopReason.MAX_ITERATIONS:
            await events.error(
                AgentError(
                    code=ErrorCode.MAX_ITERATIONS,
                    node=events.node,
                    decision="stop and synthesise",
                    outcome=decision.detail,
                )
            )
        elif reason is StopReason.NO_EVIDENCE:
            await events.error(
                AgentError(
                    code=ErrorCode.NO_EVIDENCE,
                    node=events.node,
                    decision="report that no evidence was found",
                    outcome="no answer will be invented",
                )
            )

    statuses = {s.id: s.status.value for s in state.plan}
    verb = f"Stop ({reason.value if reason else ''})" if decision.stop else "Continue"
    await events.info(
        EventType.DECISION,
        f"{verb}: {decision.detail}.",
        label="Router",
        data={
            "stop": decision.stop,
            "rule": decision.rule,
            "reason": reason,
            "subquestions": statuses,
            "budget": deps.meter.snapshot(),
        },
    )


def route_after_coverage(state: ResearchState) -> str:
    return "synthesize" if state.route is None or state.route.stop else "generate_queries"
