"""Query admission and follow-up policy under incomplete or overproduced model output."""

from __future__ import annotations

import uuid
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from research_agent.agent.coverage import REPEATED_QUERIES_REASON
from research_agent.agent.deps import AgentDeps
from research_agent.agent.nodes.search import generate_queries, plan_targets
from research_agent.agent.research import Toolkit, build_deps
from research_agent.agent.runtime import MemoryCache, MemoryCallRecorder, MemoryEventSink
from research_agent.agent.state import (
    Contradiction,
    ContradictionKind,
    Facet,
    FacetStatus,
    QueryPurpose,
    QueryRecord,
    ResearchState,
    SubQuestion,
    SubQuestionStatus,
)
from research_agent.config.loader import load_settings
from research_agent.prompting.schemas import QueriesOutput, QueryOut


async def _setup() -> tuple[AgentDeps, ResearchState]:
    run_id = uuid.uuid4()
    deps = await build_deps(
        run_id=run_id,
        settings=load_settings().settings,
        toolkit=Toolkit(llm={}, search={}),
        events=MemoryEventSink(run_id),
        recorder=MemoryCallRecorder(),
        cache=MemoryCache(),
        today=date(2026, 9, 17),
    )
    state = ResearchState(
        run_id=run_id,
        question="What is Acme's revenue?",
        as_of=deps.today,
        plan=[
            SubQuestion(id="s1", text="Acme revenue", facets=[Facet(id="f1", name="actual sales")])
        ],
    )
    state.analysis.entities = ["Acme"]
    return deps, state


def _query(text: str, *, subq_id: str = "s1") -> QueryOut:
    return QueryOut(subq_id=subq_id, text=text, purpose="initial", rationale="Find evidence")


async def test_model_overproduction_duplicates_and_unknown_targets_are_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deps, state = await _setup()
    texts = ["Acme annual revenue", "Acme audited earnings", "Acme investor sales", "Acme forecast"]
    output = QueriesOutput(
        rationale="Several angles",
        queries=[_query("unrelated topic", subq_id="unknown"), _query(texts[0])]
        + [_query(text) for text in texts],
    )
    monkeypatch.setattr(deps, "predictor", AsyncMock(return_value=SimpleNamespace(value=output)))

    await generate_queries(state, deps, deps.events)

    assert [q.text for q in state.queries] == texts[:3]
    assert all(q.subq_id == "s1" and q.iteration == 1 for q in state.queries)
    assert state.plan[0].status is SubQuestionStatus.SEARCHING
    assert deps.meter.searches == 0, "query generation must not spend the search budget"


async def test_unavailable_model_still_generates_usable_template_queries() -> None:
    deps, state = await _setup()

    await generate_queries(state, deps, deps.events)

    assert state.queries
    assert len(state.queries) <= deps.settings.budget.queries_per_subquestion_first_round
    assert all("Acme" in q.text and q.rationale == "template" for q in state.queries)
    assert len({q.text for q in state.queries}) == len(state.queries)


async def test_followup_only_targets_gaps_and_attempts_each_conflict_once() -> None:
    deps, state = await _setup()
    state.iteration = 1
    state.plan[0].facets.append(Facet(id="f2", name="currency", status=FacetStatus.SUFFICIENT))
    state.plan.append(
        SubQuestion(id="s2", text="Closed question", status=SubQuestionStatus.SUFFICIENT)
    )
    state.contradictions = [
        Contradiction(
            id="x1",
            cluster_ids=["k1", "k2"],
            subq_id="s1",
            kind=ContradictionKind.TRUE_CONFLICT,
            summary="Acme conflicting annual revenue figures",
        )
    ]

    await generate_queries(state, deps, deps.events)

    assert {q.purpose for q in state.queries} == {QueryPurpose.CONFLICT, QueryPurpose.GAP}
    assert all(q.subq_id == "s1" and q.facet_id != "f2" for q in state.queries)
    assert state.contradictions[0].followup_attempted
    assert not state.contradictions[0].resolved, "a search attempt is not a resolution"
    assert all(t.purpose is not QueryPurpose.CONFLICT for t in plan_targets(state, deps))


async def test_repeating_every_gap_query_exhausts_the_subquestion() -> None:
    deps, state = await _setup()
    state.iteration = 1
    state.queries = [
        QueryRecord(id=f"q{i}", text=text, subq_id="s1", iteration=1)
        for i, text in enumerate(["Acme revenue actual sales", "actual sales Acme"], start=1)
    ]

    await generate_queries(state, deps, deps.events)

    assert len(state.queries) == 2
    assert state.plan[0].status is SubQuestionStatus.EXHAUSTED
    assert state.plan[0].exhausted_reason == REPEATED_QUERIES_REASON


async def test_exhausted_search_budget_skips_the_query_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deps, state = await _setup()
    deps.meter.searches = deps.settings.budget.max_searches
    predictor = AsyncMock()
    monkeypatch.setattr(deps, "predictor", predictor)

    await generate_queries(state, deps, deps.events)

    predictor.assert_not_awaited()
    assert not state.queries
