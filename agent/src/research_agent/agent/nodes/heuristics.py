"""Visible deterministic review steps. Warnings explain limitations, never certify truth."""

from __future__ import annotations

import re

from research_agent.agent.deps import AgentDeps
from research_agent.agent.freshness import applicability_passages
from research_agent.agent.runtime import EventSink
from research_agent.agent.state import HeuristicCheck, ResearchState
from research_agent.agent.text import fold
from research_agent.gate.rules import g2_citations, g3_ledger, g4_numbers
from research_agent.observability.events import EventType


async def _record(state: ResearchState, events: EventSink, check: HeuristicCheck) -> None:
    state.heuristic_checks = [old for old in state.heuristic_checks if old.id != check.id]
    state.heuristic_checks.append(check)
    await events.info(
        EventType.DECISION,
        check.summary,
        label="Heuristics",
        data={"heuristic": check.model_dump(mode="json")},
    )


async def check_plan(state: ResearchState, deps: AgentDeps, events: EventSink) -> None:
    """Flag a comparison that has silently omitted one of the requested entities."""
    plan_text = fold(" ".join(q.text for q in state.plan))
    missing = []
    if state.analysis.answer_type == "comparison":
        for entity in state.analysis.entities:
            alias = re.sub(r"\.(?:com|org|net|ai)$", "", fold(entity))
            if alias and not re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", plan_text):
                missing.append(entity)
    await _record(
        state,
        events,
        HeuristicCheck(
            id="H1",
            stage="plan",
            name="Comparison entity coverage",
            status="warn" if missing else "pass",
            related_ids=missing,
            summary=f"Plan omits comparison entities: {', '.join(missing)}."
            if missing
            else "No comparison entity omission detected in the plan.",
        ),
    )


async def review_evidence(state: ResearchState, deps: AgentDeps, events: EventSink) -> None:
    held = [c.id for c in state.claims.values() if c.requires_fresh_confirmation]
    rejected = [
        c.id for c in state.claims.values() if c.validation_status in {"unsupported", "unavailable"}
    ]
    unscoped = [
        c.id
        for c in state.claims.values()
        if not c.conditions
        and c.doc_id in state.documents
        and applicability_passages(state.documents[c.doc_id], c)
    ]
    for id_, name, ids, summary in [
        (
            "H2",
            "Time applicability",
            held,
            "candidate claims lack current applicability confirmation",
        ),
        ("H3", "Source support", rejected, "candidate claims lack an affirmative source verdict"),
        (
            "H4",
            "Material conditions",
            unscoped,
            "candidate claims omit detected applicability conditions",
        ),
    ]:
        await _record(
            state,
            events,
            HeuristicCheck(
                id=id_,
                stage="evidence",
                name=name,
                status="warn" if ids else "pass",
                related_ids=ids,
                summary=f"{len(ids)} {summary}; ineligible claims are withheld.",
            ),
        )
    cited = (
        {
            cid
            for _, sentence in state.report.sentences()
            for cid in [*sentence.cluster_ids, *sentence.finding_refs]
        }
        if state.report
        else set()
    )
    singles = [c.id for c in state.clusters.values() if c.id in cited and len(c.origin_ids) < 2]
    await _record(
        state,
        events,
        HeuristicCheck(
            id="H5",
            stage="evidence",
            name="Publisher independence",
            status="warn" if singles else "pass",
            related_ids=singles,
            summary=f"{len(singles)} cited findings rely on a single independent origin. "
            "Primary sources may still suffice.",
        ),
    )
    violations = []
    if state.report:
        for rule in (g2_citations, g3_ledger, g4_numbers):
            violations.extend(rule(state.report, state, deps.gate))
    await _record(
        state,
        events,
        HeuristicCheck(
            id="H6",
            stage="report",
            name="Citation and numeric integrity",
            status="warn" if violations or not state.report else "pass",
            related_ids=[f"{v.rule}:{v.section}:{v.index}" for v in violations],
            summary=f"{len(violations)} citation or numeric issues before gate remediation."
            if state.report
            else "No report available for integrity review.",
        ),
    )
