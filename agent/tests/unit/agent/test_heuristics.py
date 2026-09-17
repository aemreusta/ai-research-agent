"""Checks must detect omissions and expose withheld evidence without claiming semantic truth."""

import uuid
from datetime import date
from types import SimpleNamespace
from typing import cast

from research_agent.agent.deps import AgentDeps
from research_agent.agent.nodes.heuristics import check_plan, review_evidence
from research_agent.agent.runtime import MemoryEventSink
from research_agent.agent.state import Claim, ResearchState, SubQuestion
from research_agent.gate.config import GateConfig


async def test_comparison_omission_is_visible_and_not_a_false_pass() -> None:
    state = ResearchState(
        run_id=uuid.uuid4(), question="Compare Shopify and Wix", as_of=date.today()
    )
    state.analysis.answer_type = "comparison"
    state.analysis.entities = ["Shopify", "Wix.com"]
    state.plan = [SubQuestion(id="s1", text="Shopify revenue")]
    events = MemoryEventSink(state.run_id)
    deps = cast(AgentDeps, SimpleNamespace())
    await check_plan(state, deps, events)
    assert state.heuristic_checks[0].status == "warn"
    assert state.heuristic_checks[0].related_ids == ["Wix.com"]
    state.plan.append(SubQuestion(id="s2", text="Wix revenue"))
    await check_plan(state, deps, events)
    assert len(state.heuristic_checks) == 1
    assert state.heuristic_checks[0].model_dump()["status"] == "pass"


async def test_evidence_checks_expose_missing_verdicts_and_dates() -> None:
    state = ResearchState(run_id=uuid.uuid4(), question="A current threshold", as_of=date.today())
    state.claims["c1"] = Claim(
        id="c1",
        subq_id="s1",
        doc_id="d1",
        origin_id="o1",
        text="An old value",
        quote="An old value",
        requires_fresh_confirmation=True,
        validation_status="unavailable",
    )
    events = MemoryEventSink(state.run_id)
    deps = cast(AgentDeps, SimpleNamespace(gate=GateConfig.load()))
    await review_evidence(state, deps, events)
    checks = {c.id: c for c in state.heuristic_checks}
    assert checks["H2"].related_ids == ["c1"] and checks["H2"].status == "warn"
    assert checks["H3"].status == "warn" and checks["H6"].status == "warn"
    assert len(events.events) == 5
