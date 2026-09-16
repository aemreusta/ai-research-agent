"""Clustering, contradiction candidates, coverage and termination - the deterministic core."""

from __future__ import annotations

import uuid
from datetime import date

import pytest

from research_agent.agent.budget import BudgetMeter
from research_agent.agent.clustering import cluster_claims, normalise_entity
from research_agent.agent.contradictions import contradiction_candidates
from research_agent.agent.coverage import assess_facets, update_progress
from research_agent.agent.state import (
    Claim,
    ClaimCluster,
    ClusterStatus,
    Document,
    Facet,
    FacetStatus,
    ResearchState,
    SourceScore,
    StopReason,
    SubQuestion,
    SubQuestionStatus,
)
from research_agent.agent.termination import decide
from research_agent.config.schema import (
    BudgetSettings,
    ContradictionSettings,
    DedupSettings,
    ScoringSettings,
)
from research_agent.providers.llm.fake import bag_of_words_vector


def _doc(doc_id: str, origin: str, score: float = 0.6, primary: bool = False) -> Document:
    return Document(
        id=doc_id,
        url=f"https://{doc_id}.com",
        canonical_url=f"https://{doc_id}.com",
        domain=f"{doc_id}.com",
        origin_id=origin,
        score=SourceScore(total=score, is_primary=primary, tier=1 if primary else 2),
    )


def _claim(claim_id: str, doc: Document, text: str, **fields: object) -> Claim:
    return Claim(
        id=claim_id,
        subq_id="s1",
        doc_id=doc.id,
        origin_id=doc.origin_id,
        text=text,
        quote=text,
        **fields,
    )


def _state(**kwargs: object) -> ResearchState:
    return ResearchState(run_id=uuid.uuid4(), question="q", as_of=date(2026, 9, 16), **kwargs)


# --- L3 clustering -----------------------------------------------------------------------------------


def test_entity_normalisation() -> None:
    assert normalise_entity("Apilex A.Ş.") == normalise_entity("apilex") == "apilex"
    assert normalise_entity("OpenAI, Inc.") == "openai"
    assert normalise_entity(None) is None


def test_the_same_fact_from_two_origins_is_one_supported_cluster() -> None:
    a, b = _doc("a", "o1"), _doc("b", "o2")
    claims = [
        _claim("c1", a, "ApilexAI partnered with Microsoft in 2025", entity="ApilexAI"),
        _claim("c2", b, "In 2025 ApilexAI partnered with Microsoft", entity="Apilex AI"),
    ]
    vectors = {c.id: bag_of_words_vector(c.text) for c in claims}
    clusters = cluster_claims(
        {},
        claims,
        vectors=vectors,
        documents={"a": a, "b": b},
        settings=DedupSettings(),
        all_claims={},
    )
    assert len(clusters) == 1
    cluster = next(iter(clusters.values()))
    assert cluster.support == 2
    assert cluster.status is ClusterStatus.SUPPORTED


def test_five_copies_of_one_press_release_are_one_confirmation() -> None:
    docs = {f"d{i}": _doc(f"d{i}", "o-press") for i in range(5)}
    claims = [
        _claim(f"c{i}", docs[f"d{i}"], "Company X raised $10 million", entity="X") for i in range(5)
    ]
    clusters = cluster_claims(
        {}, claims, vectors=None, documents=docs, settings=DedupSettings(), all_claims={}
    )
    cluster = next(iter(clusters.values()))
    assert len(cluster.doc_ids) == 5
    assert cluster.support == 1
    assert cluster.status is ClusterStatus.SINGLE_SOURCE


def test_different_values_are_never_merged_into_one_fact() -> None:
    """Merging them would hide exactly the contradiction the system must report."""
    a, b = _doc("a", "o1"), _doc("b", "o2")
    claims = [
        _claim(
            "c1",
            a,
            "The market is worth $4.5 billion",
            entity="market",
            attribute="size",
            value="$4.5 billion",
        ),
        _claim(
            "c2",
            b,
            "The market is worth $7.2 billion",
            entity="market",
            attribute="size",
            value="$7.2 billion",
        ),
    ]
    vectors = {c.id: bag_of_words_vector(c.text) for c in claims}
    clusters = cluster_claims(
        {},
        claims,
        vectors=vectors,
        documents={"a": a, "b": b},
        settings=DedupSettings(claim_similarity_threshold=0.5),
        all_claims={},
    )
    assert len(clusters) == 2


def test_different_entities_are_not_merged() -> None:
    a, b = _doc("a", "o1"), _doc("b", "o2")
    claims = [
        _claim("c1", a, "Revenue grew 20% in 2025", entity="Acme"),
        _claim("c2", b, "Revenue grew 20% in 2025", entity="Globex"),
    ]
    clusters = cluster_claims(
        {},
        claims,
        vectors=None,
        documents={"a": a, "b": b},
        settings=DedupSettings(),
        all_claims={},
    )
    assert len(clusters) == 2


def test_lexical_fallback_clusters_without_embeddings() -> None:
    a, b = _doc("a", "o1"), _doc("b", "o2")
    claims = [
        _claim("c1", a, "KVKK requires notification within five business days", entity="KVKK"),
        _claim("c2", b, "KVKK requires notification within five business days.", entity="KVKK"),
    ]
    clusters = cluster_claims(
        {},
        claims,
        vectors=None,
        documents={"a": a, "b": b},
        settings=DedupSettings(),
        all_claims={},
    )
    assert len(clusters) == 1


def test_cluster_ids_are_stable_across_rounds() -> None:
    a, b = _doc("a", "o1"), _doc("b", "o2")
    c1 = _claim("c1", a, "X acquired Y in 2024", entity="X")
    first = cluster_claims(
        {}, [c1], vectors=None, documents={"a": a}, settings=DedupSettings(), all_claims={}
    )
    second = cluster_claims(
        first,
        [_claim("c2", b, "X acquired Y in 2024", entity="X")],
        vectors=None,
        documents={"a": a, "b": b},
        settings=DedupSettings(),
        all_claims={"c1": c1},
    )
    assert set(first) <= set(second)
    assert second[next(iter(first))].support == 2


def test_confidence_grows_with_independent_strong_sources() -> None:
    strong, weak = _doc("a", "o1", 0.9, primary=True), _doc("b", "o2", 0.3)
    one = cluster_claims(
        {},
        [_claim("c1", weak, "Fact one here", entity="E")],
        vectors=None,
        documents={"b": weak},
        settings=DedupSettings(),
        all_claims={},
    )
    two = cluster_claims(
        {},
        [
            _claim("c1", weak, "Fact one here", entity="E"),
            _claim("c2", strong, "Fact one here", entity="E"),
        ],
        vectors=None,
        documents={"a": strong, "b": weak},
        settings=DedupSettings(),
        all_claims={},
    )
    assert next(iter(two.values())).confidence > next(iter(one.values())).confidence
    assert next(iter(two.values())).has_primary


# --- contradictions -----------------------------------------------------------------------------------


def _cluster(
    cid: str,
    value: str | None,
    *,
    entity: str = "EU AI Act",
    attribute: str = "gpai obligations start",
    origins: tuple[str, ...] = ("o1",),
    as_of: str | None = None,
) -> ClaimCluster:
    return ClaimCluster(
        id=cid,
        subq_id="s1",
        claim_ids=[f"{cid}-c"],
        doc_ids=[f"{cid}-d"],
        origin_ids=list(origins),
        statement=f"{attribute} {value}",
        entity=entity,
        attribute=attribute,
        value=value,
        as_of=as_of,
    )


def test_differing_dates_for_the_same_attribute_are_candidates() -> None:
    clusters = {"k1": _cluster("k1", "2 August 2025"), "k2": _cluster("k2", "2 August 2026")}
    pairs = contradiction_candidates(clusters, ContradictionSettings(), language="en")
    assert pairs == [("k1", "k2")]


def test_rounding_is_not_a_candidate() -> None:
    clusters = {
        "k1": _cluster("k1", "$4.52 billion", attribute="market size"),
        "k2": _cluster("k2", "$4.5 billion", attribute="market size"),
    }
    assert contradiction_candidates(clusters, ContradictionSettings(), language="en") == []


def test_numeric_disagreement_beyond_tolerance_is_a_candidate() -> None:
    clusters = {
        "k1": _cluster("k1", "$4.5 billion", attribute="market size"),
        "k2": _cluster("k2", "$7.2 billion", attribute="market size"),
        "k3": _cluster("k3", "$7.2 billion", attribute="headcount"),
    }
    assert contradiction_candidates(clusters, ContradictionSettings(), language="en") == [
        ("k1", "k2")
    ]


def test_textual_values_that_differ_are_candidates_too() -> None:
    clusters = {
        "k1": _cluster("k1", "banned", attribute="status"),
        "k2": _cluster("k2", "allowed with conditions", attribute="status"),
    }
    assert contradiction_candidates(clusters, ContradictionSettings(), language="en") == [
        ("k1", "k2")
    ]


# --- coverage -----------------------------------------------------------------------------------------


def _subq(facets: list[str]) -> SubQuestion:
    return SubQuestion(
        id="s1",
        text="q",
        facets=[Facet(id=f, name=f) for f in facets],
        status=SubQuestionStatus.SEARCHING,
    )


def test_a_facet_is_sufficient_with_one_strong_primary_source() -> None:
    state = _state(
        plan=[_subq(["f1"])],
        clusters={
            "k1": ClaimCluster(
                id="k1",
                subq_id="s1",
                claim_ids=["c"],
                doc_ids=["d"],
                origin_ids=["o1"],
                facet_ids=["f1"],
                statement="s",
                has_primary=True,
                best_source_score=0.8,
            ),
        },
    )
    assess_facets(state, ScoringSettings())
    assert state.plan[0].facets[0].status is FacetStatus.SUFFICIENT


def test_a_facet_is_sufficient_with_two_independent_origins() -> None:
    state = _state(
        plan=[_subq(["f1"])],
        clusters={
            "k1": ClaimCluster(
                id="k1",
                subq_id="s1",
                claim_ids=["c"],
                doc_ids=["d1", "d2"],
                origin_ids=["o1", "o2"],
                facet_ids=["f1"],
                statement="s",
                best_source_score=0.4,
            ),
        },
    )
    assess_facets(state, ScoringSettings())
    assert state.plan[0].facets[0].status is FacetStatus.SUFFICIENT


def test_one_weak_source_is_not_enough() -> None:
    state = _state(
        plan=[_subq(["f1"])],
        clusters={
            "k1": ClaimCluster(
                id="k1",
                subq_id="s1",
                claim_ids=["c"],
                doc_ids=["d"],
                origin_ids=["o1"],
                facet_ids=["f1"],
                statement="s",
                best_source_score=0.5,
            ),
        },
    )
    assess_facets(state, ScoringSettings())
    assert state.plan[0].facets[0].status is FacetStatus.OPEN


def test_a_contested_facet_is_not_sufficient() -> None:
    state = _state(
        plan=[_subq(["f1"])],
        clusters={
            "k1": ClaimCluster(
                id="k1",
                subq_id="s1",
                claim_ids=["c"],
                doc_ids=["d1", "d2"],
                origin_ids=["o1", "o2"],
                facet_ids=["f1"],
                statement="s",
                status=ClusterStatus.CONTESTED,
            ),
        },
    )
    assess_facets(state, ScoringSettings())
    assert state.plan[0].facets[0].status is FacetStatus.CONTESTED


def test_all_facets_sufficient_makes_the_subquestion_sufficient() -> None:
    subq = _subq(["f1"])
    subq.facets[0].status = FacetStatus.SUFFICIENT
    update_progress(subq, gain=3, settings=BudgetSettings())
    assert subq.status is SubQuestionStatus.SUFFICIENT


def test_stagnation_exhausts_a_subquestion_after_the_threshold() -> None:
    subq = _subq(["f1"])
    statuses = []
    for _ in range(2):
        update_progress(subq, gain=0, settings=BudgetSettings(stagnation_threshold=2))
        statuses.append(subq.status)
    assert statuses == [SubQuestionStatus.SEARCHING, SubQuestionStatus.EXHAUSTED]
    assert subq.exhausted_reason and "progress" in subq.exhausted_reason


def test_progress_resets_the_stagnation_counter() -> None:
    subq = _subq(["f1"])
    update_progress(subq, gain=0, settings=BudgetSettings())
    update_progress(subq, gain=2, settings=BudgetSettings())
    assert subq.rounds_without_progress == 0


# --- termination --------------------------------------------------------------------------------------


def _router_state(
    statuses: list[SubQuestionStatus], priorities: list[str] | None = None, iteration: int = 1
) -> ResearchState:
    priorities = priorities or ["must"] * len(statuses)
    plan = [
        SubQuestion(id=f"s{i}", text="q", status=status, priority=priority)
        for i, (status, priority) in enumerate(zip(statuses, priorities, strict=True))
    ]
    return _state(plan=plan, iteration=iteration)


def test_all_must_subquestions_sufficient_stops_with_success() -> None:
    state = _router_state(
        [SubQuestionStatus.SUFFICIENT, SubQuestionStatus.SEARCHING], ["must", "nice"]
    )
    decision = decide(state, BudgetMeter(BudgetSettings()), BudgetSettings())
    assert (decision.stop, decision.reason) == (True, StopReason.SUFFICIENT)


def test_open_gaps_continue_the_loop() -> None:
    state = _router_state([SubQuestionStatus.SUFFICIENT, SubQuestionStatus.SEARCHING])
    decision = decide(state, BudgetMeter(BudgetSettings()), BudgetSettings())
    assert decision.stop is False
    assert "gap" in decision.rule


def test_max_iterations_is_a_hard_stop() -> None:
    state = _router_state([SubQuestionStatus.SEARCHING], iteration=4)
    decision = decide(state, BudgetMeter(BudgetSettings()), BudgetSettings(max_iterations=4))
    assert (decision.stop, decision.reason) == (True, StopReason.MAX_ITERATIONS)


def test_the_search_budget_is_a_hard_stop() -> None:
    meter = BudgetMeter(BudgetSettings(max_searches=5))
    meter.add_search(5)
    decision = decide(
        _router_state([SubQuestionStatus.SEARCHING]), meter, BudgetSettings(max_searches=5)
    )
    assert (decision.stop, decision.reason) == (True, StopReason.BUDGET)
    assert "max_searches" in decision.detail


def test_nothing_left_to_search_is_no_progress() -> None:
    state = _router_state([SubQuestionStatus.EXHAUSTED, SubQuestionStatus.SUFFICIENT])
    decision = decide(state, BudgetMeter(BudgetSettings()), BudgetSettings())
    assert (decision.stop, decision.reason) == (True, StopReason.NO_PROGRESS)


# The disabled-by-default gates: only a test proves they work (D11, note kept from the review).
def test_wall_clock_budget_stops_the_run_when_enabled() -> None:
    settings = BudgetSettings(max_wall_clock_seconds=60)
    clock = iter([0.0, 61.0, 61.0])
    meter = BudgetMeter(settings, clock=lambda: next(clock))
    decision = decide(_router_state([SubQuestionStatus.SEARCHING]), meter, settings)
    assert (decision.stop, decision.reason) == (True, StopReason.BUDGET)
    assert "max_wall_clock_seconds" in decision.detail


def test_cost_budget_stops_the_run_when_enabled() -> None:
    settings = BudgetSettings(max_cost_usd=0.10)
    meter = BudgetMeter(settings)
    meter.add_llm(tokens_in=1, tokens_out=1, cost_usd=0.11)
    decision = decide(_router_state([SubQuestionStatus.SEARCHING]), meter, settings)
    assert (decision.stop, decision.reason) == (True, StopReason.BUDGET)
    assert "max_cost_usd" in decision.detail


def test_disabled_gates_never_stop_a_run() -> None:
    settings = BudgetSettings()
    assert settings.max_wall_clock_seconds is None and settings.max_cost_usd is None
    clock = iter([0.0, 1e9, 1e9])
    meter = BudgetMeter(settings, clock=lambda: next(clock))
    meter.add_llm(tokens_in=1, tokens_out=1, cost_usd=1e6)
    assert decide(_router_state([SubQuestionStatus.SEARCHING]), meter, settings).stop is False


def test_forgetting_existing_claims_is_an_error_not_a_silent_zero() -> None:
    a = _doc("a", "o1")
    c1 = _claim("c1", a, "X acquired Y in 2024", entity="X")
    first = cluster_claims(
        {}, [c1], vectors=None, documents={"a": a}, settings=DedupSettings(), all_claims={}
    )
    with pytest.raises(ValueError, match="every existing claim"):
        cluster_claims(
            first, [], vectors=None, documents={"a": a}, settings=DedupSettings(), all_claims={}
        )


def test_a_contradiction_that_was_followed_up_stops_blocking_its_facet() -> None:
    from research_agent.agent.state import Contradiction, ContradictionKind

    state = _state(
        plan=[_subq(["f1"])],
        clusters={
            "k1": ClaimCluster(
                id="k1",
                subq_id="s1",
                claim_ids=["c"],
                doc_ids=["d1", "d2"],
                origin_ids=["o1", "o2"],
                facet_ids=["f1"],
                statement="s",
                status=ClusterStatus.CONTESTED,
            ),
        },
        contradictions=[
            Contradiction(
                id="x1",
                cluster_ids=["k1", "k2"],
                subq_id="s1",
                kind=ContradictionKind.TRUE_CONFLICT,
                summary="",
                followup_attempted=True,
            )
        ],
    )
    assess_facets(state, ScoringSettings())
    assert state.plan[0].facets[0].status is FacetStatus.SUFFICIENT
