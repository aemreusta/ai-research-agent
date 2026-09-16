"""detect_contradictions: how the judge's verdict and the ledger combine."""

from __future__ import annotations

import uuid
from datetime import date
from types import SimpleNamespace
from typing import Any, cast

from research_agent.agent.deps import AgentDeps
from research_agent.agent.nodes.evidence import detect_contradictions
from research_agent.agent.runtime import MemoryEventSink
from research_agent.agent.state import (
    ClaimCluster,
    ClusterStatus,
    ContradictionKind,
    Document,
    ResearchState,
    SourceScore,
)
from research_agent.config.schema import Settings
from research_agent.prompting.schemas import ContradictionJudgement, ContradictionJudgements


def _doc(doc_id: str, published: date, score: float, primary: bool) -> Document:
    return Document(
        id=doc_id,
        url=f"https://{doc_id}.eu",
        canonical_url=f"https://{doc_id}.eu",
        domain=f"{doc_id}.eu",
        origin_id=f"o-{doc_id}",
        published_at=published,
        score=SourceScore(total=score, is_primary=primary),
    )


def _cluster(cid: str, value: str, doc: Document, score: float, primary: bool) -> ClaimCluster:
    return ClaimCluster(
        id=cid,
        subq_id="s1",
        claim_ids=[f"{cid}-c"],
        doc_ids=[doc.id],
        origin_ids=[doc.origin_id],
        statement=f"Annex III rules apply from {value}",
        entity="EU AI Act",
        attribute="high-risk systems application date",
        value=value,
        best_source_score=score,
        has_primary=primary,
    )


def _state() -> ResearchState:
    old = _doc("blog", date(2025, 3, 1), 0.3, primary=False)
    new = _doc("commission", date(2026, 6, 1), 0.9, primary=True)
    return ResearchState(
        run_id=uuid.uuid4(),
        question="What changed in the EU AI Act timeline?",
        as_of=date(2026, 9, 16),
        documents={"blog": old, "commission": new},
        clusters={
            "k1": _cluster("k1", "2 August 2026", old, 0.3, primary=False),
            "k2": _cluster("k2", "2 December 2027", new, 0.9, primary=True),
        },
    )


class _Judge:
    def __init__(self, kind: str, preferred: str) -> None:
        self.kind, self.preferred = kind, preferred
        self.pairs: list[dict[str, Any]] = []

    async def __call__(self, name: str, events: object, **inputs: Any) -> SimpleNamespace:
        self.pairs = inputs["pairs"]
        verdict = ContradictionJudgement(
            pair_id="p1",
            kind=self.kind,
            summary="s",
            preferred=self.preferred,
            rationale="r",
        )
        return SimpleNamespace(value=ContradictionJudgements(rationale="r", items=[verdict]))


async def _run(judge: _Judge) -> ResearchState:
    state = _state()
    deps = cast(AgentDeps, SimpleNamespace(settings=Settings(), predictor=judge))
    await detect_contradictions(state, deps, MemoryEventSink(state.run_id, node="judge"))
    return state


async def test_the_judge_sees_how_recent_each_side_is() -> None:
    judge = _Judge("different_time", "right")
    await _run(judge)
    assert judge.pairs[0]["left"]["latest_source_date"] == "2025-03-01"
    assert judge.pairs[0]["right"]["latest_source_date"] == "2026-06-01"


async def test_a_postponed_date_keeps_the_newer_official_side() -> None:
    state = await _run(_Judge("different_time", "right"))
    (contradiction,) = state.contradictions
    assert contradiction.kind is ContradictionKind.DIFFERENT_TIME
    assert contradiction.resolved and contradiction.preferred_cluster_id == "k2"
    assert state.clusters["k1"].status is not ClusterStatus.CONTESTED


async def test_a_preference_for_the_older_secondary_side_is_ignored() -> None:
    state = await _run(_Judge("different_time", "left"))
    assert state.contradictions[0].preferred_cluster_id is None


async def test_consistent_statements_are_not_contested() -> None:
    state = await _run(_Judge("consistent", "none"))
    (contradiction,) = state.contradictions
    assert contradiction.kind is ContradictionKind.CONSISTENT and contradiction.resolved
    assert {c.status for c in state.clusters.values()} == {ClusterStatus.SINGLE_SOURCE}


async def test_a_true_conflict_marks_both_sides_and_records_the_backed_preference() -> None:
    state = await _run(_Judge("true_conflict", "right"))
    assert {c.status for c in state.clusters.values()} == {ClusterStatus.CONTESTED}
    assert state.contradictions[0].preferred_cluster_id == "k2"
