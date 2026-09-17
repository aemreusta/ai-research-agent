"""Semantic and temporal traps from the independent audit, with positive controls."""

from __future__ import annotations

import uuid
from datetime import date
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest

from research_agent.agent.clustering import cluster_claims
from research_agent.agent.coverage import assess_facets
from research_agent.agent.deps import AgentDeps
from research_agent.agent.freshness import assess_claim_freshness, document_evidence
from research_agent.agent.nodes.report import _ledger, fallback_report
from research_agent.agent.research import Toolkit, build_deps
from research_agent.agent.runtime import MemoryCache, MemoryCallRecorder, MemoryEventSink
from research_agent.agent.state import (
    Claim,
    Document,
    Facet,
    ResearchState,
    SourceScore,
    SubQuestion,
    TimeScope,
)
from research_agent.agent.text import detect_language
from research_agent.agent.validation import validate_claims
from research_agent.config.schema import Settings
from research_agent.gate.config import GateConfig
from research_agent.gate.rules import g12_evidence_eligibility
from research_agent.prompting.schemas import ClaimValidation, ClaimValidationOutput


def state() -> ResearchState:
    s = ResearchState(
        run_id=uuid.uuid4(), question="Current obligations in 2026?", as_of=date(2026, 9, 17)
    )
    s.analysis.time_scope = TimeScope(start=date(2026, 1, 1), end=date(2026, 12, 31))
    s.plan = [SubQuestion(id="s1", text=s.question, facets=[Facet(id="f1", name="obligations")])]
    return s


def claim(cid: str = "c1", **changes: object) -> Claim:
    return Claim.model_validate(
        dict(
            id=cid,
            subq_id="s1",
            facet_id="f1",
            doc_id="d1",
            origin_id="o1",
            text="The threshold is 25 million TL.",
            quote="The threshold is 25 million TL.",
            **changes,
        )
    )


def doc(did: str = "d1", domain: str = "kvkk.gov.tr", **changes: object) -> Document:
    return Document.model_validate(
        dict(
            id=did,
            url=f"https://{domain}/{did}",
            canonical_url=f"https://{domain}/{did}",
            domain=domain,
            score=SourceScore(total=0.94, is_primary=True),
            **changes,
        )
    )


def clustered(s: ResearchState) -> None:
    s.clusters = cluster_claims(
        {},
        list(s.claims.values()),
        vectors=None,
        documents=s.documents,
        settings=Settings().dedup,
        all_claims=s.claims,
    )


@pytest.mark.parametrize("publication", [None, date(2026, 8, 1)])
def test_2021_value_is_not_current_even_on_a_fresh_primary_page(publication: date | None) -> None:
    s = state()
    c = claim(as_of="11/03/2021", time_sensitive=True)
    d = doc(published_at=publication)
    assess_claim_freshness(c, d, s)
    assert c.requires_fresh_confirmation and c.freshness == "historical"
    s.claims[c.id], s.documents[d.id] = c, d
    clustered(s)
    assess_facets(s, Settings().scoring)
    assert s.plan[0].facets[0].status == "open"
    assert _ledger(s) == []
    assert "25 million" not in fallback_report(s).model_dump_json()


def test_current_value_and_historical_question_are_valid_controls() -> None:
    s = state()
    current = claim(as_of="2026-08-01", time_sensitive=True)
    assess_claim_freshness(current, doc(), s)
    assert not current.requires_fresh_confirmation
    s.analysis.time_scope = TimeScope(start=date(2021, 1, 1), end=date(2021, 12, 31))
    historical = claim(as_of="11/03/2021", time_sensitive=True)
    assess_claim_freshness(historical, doc(), s)
    assert not historical.requires_fresh_confirmation


def test_fiscal_period_is_not_confused_with_the_release_publication_date() -> None:
    s = state()
    s.analysis.time_scope = TimeScope(start=date(2025, 1, 1), end=date(2025, 12, 31))
    revenue = claim(as_of="FY2025", time_sensitive=True)
    assess_claim_freshness(revenue, doc(published_at=date(2026, 2, 1)), s)
    assert revenue.freshness == "current" and not revenue.requires_fresh_confirmation


def test_similar_comparison_questions_preserve_both_entities_and_remove_real_duplicates() -> None:
    from research_agent.agent.nodes.intake import normalise_plan
    from research_agent.prompting.schemas import PlanOutput

    s = state()
    s.analysis.entities = ["Shopify", "Wix.com"]
    template = "What was {entity}'s actual reported full year 2025 revenue in US dollars?"
    questions = [template.format(entity=e) for e in ["Shopify", "Wix", "Shopify"]]
    out = PlanOutput.model_validate(
        {
            "subquestions": [
                {"text": text, "priority": "must", "facets": [], "expected_sources": []}
                for text in questions
            ],
            "rationale": "Compare the two companies.",
        }
    )
    deps = cast(AgentDeps, SimpleNamespace(settings=Settings()))
    kept = normalise_plan(out, s, deps)
    assert [q.text for q in kept] == questions[:2]


def test_undated_mutable_value_is_held_but_definition_is_not() -> None:
    s = state()
    mutable = claim(time_sensitive=True)
    assess_claim_freshness(mutable, doc(), s)
    assert mutable.freshness == "undated" and mutable.requires_fresh_confirmation
    definition = claim(time_sensitive=False)
    definition.text = "Personal data concerns an identifiable natural person."
    assess_claim_freshness(definition, doc(), s)
    assert not definition.requires_fresh_confirmation


def test_scoped_and_unscoped_claims_never_merge() -> None:
    s = state()
    scoped = claim(conditions=["only systems placed on the market before 2 August 2026"])
    unscoped = claim("c2")
    s.claims = {c.id: c for c in [scoped, unscoped]}
    s.documents = {"d1": doc()}
    clustered(s)
    assert len(s.clusters) == 2
    assert next(iter(s.clusters.values())).conditions == scoped.conditions


def test_facet_sources_deduplicate_publishers_and_copied_origins_across_clusters() -> None:
    s = state()
    s.documents = {
        "d1": doc("d1", "news.example.com"),
        "d2": doc("d2", "news.example.com"),
        "d3": doc("d3", "another.example.org"),
    }
    for d in s.documents.values():
        d.score = SourceScore(total=0.5, is_primary=False)
    a, b, c = claim(), claim("c2"), claim("c3")
    b.doc_id, b.origin_id, b.text = "d2", "o2", "A completely different obligation also applies."
    c.doc_id, c.origin_id, c.text = "d3", "o2", "A third distinct obligation is stated."
    s.claims = {x.id: x for x in [a, b, c]}
    clustered(s)
    assess_facets(s, Settings().scoring)
    assert s.plan[0].facets[0].status == "open", "one publisher plus its copied text is one voice"
    c.origin_id = "o3"
    clustered(s)
    assess_facets(s, Settings().scoring)
    assert s.plan[0].facets[0].status == "sufficient"


def test_late_applicability_clause_is_visible_in_bounded_context() -> None:
    phrase = "The Article 50(2) transparency transition applies only to existing AI systems."
    content = "Unrelated navigation paragraph.\n\n" * 600 + phrase
    d = doc(content=content)
    excerpt = document_evidence(d, "Article 50(2) transparency obligations", limit=4000)
    assert phrase in excerpt and len(excerpt) <= 4000


@pytest.mark.parametrize(
    "complete,supported,expected", [(False, True, "unsupported"), (True, True, "supported")]
)
async def test_material_condition_loss_is_rejected_by_explicit_source_verdict(
    complete: bool, supported: bool, expected: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = state()
    deps = await build_deps(
        run_id=s.run_id,
        settings=Settings(),
        toolkit=Toolkit(llm={}, search={}),
        events=MemoryEventSink(s.run_id),
        recorder=MemoryCallRecorder(),
        cache=MemoryCache(),
    )
    candidate = claim()
    source = doc(
        content="The threshold applies only to domestic companies, excluding small charities."
    )
    predictor = AsyncMock(
        return_value=SimpleNamespace(
            value=ClaimValidationOutput(
                items=[
                    ClaimValidation(
                        claim_id="candidate-0",
                        supported=supported,
                        conditions_complete=complete,
                        time_sensitive=False,
                        reason="scope audit",
                    )
                ]
            )
        )
    )
    monkeypatch.setattr(deps, "predictor", predictor)
    await validate_claims([candidate], source, s, deps, deps.events)
    assert candidate.validation_status == expected
    assert "excluding small charities" in predictor.call_args.kwargs["document"]


def test_gate_rejects_a_stale_finding_even_if_a_synthesizer_cites_it() -> None:
    s = state()
    c = claim()
    s.claims = {c.id: c}
    s.documents = {"d1": doc()}
    clustered(s)
    report = fallback_report(s)
    next(iter(s.clusters.values())).requires_fresh_confirmation = True
    assert g12_evidence_eligibility(report, s, GateConfig.load())


def test_inline_ledger_ids_become_verifiable_citations_not_raw_report_text() -> None:
    from research_agent.agent.nodes.report import _sentence
    from research_agent.prompting.schemas import SentenceOut

    sentence = _sentence(
        SentenceOut(text="Revenue rose 30% [k1, k999].", cluster_ids=["k1"]), {"k1"}
    )
    assert sentence.text == "Revenue rose 30%."
    assert sentence.cluster_ids == ["k1", "k999"], "unknown references must reach the gate"


@pytest.mark.parametrize(
    "text,want",
    [
        ("What are the requirements in Türkiye?", "en"),
        ("Türkiye'deki şirketler için gereklilikler nelerdir?", "tr"),
        ("How is the German Gründer market growing?", "en"),
    ],
)
def test_named_entities_do_not_override_question_language(text: str, want: str) -> None:
    assert detect_language(text) == want


def test_profile_triage_prefers_the_named_company_over_generic_high_rank_pages() -> None:
    from research_agent.agent.nodes.search import _triage
    from research_agent.agent.scoring import DomainTiers
    from research_agent.providers.search.base import SearchHit

    s = state()
    s.question = "What does ApilexAI offer?"
    s.analysis.entities = ["ApilexAI"]
    s.analysis.answer_type = "profile"
    deps = cast(AgentDeps, SimpleNamespace(tiers=DomainTiers.load()))
    official = doc("d1", "apilex.ai", title="Apilex - AI for Legal", snippet="Legal assistant")
    generic = doc("d2", "irs.gov", title="Partnership forms", snippet="Company partnerships")
    hit = SearchHit(url=official.url, provider="tavily", rank=1, provider_score=0.8)
    score = _triage(official, s, deps, hit, "ApilexAI partnerships")
    irrelevant = _triage(generic, s, deps, hit, "ApilexAI partnerships")
    assert score > 0.65 and irrelevant < 0.2


def test_law_names_and_domain_prefixes_cannot_impersonate_primary_publishers() -> None:
    from research_agent.agent.scoring import is_own_domain, self_primary_entities

    assert not is_own_domain("kvkkuyum.com", ["KVKK"])
    assert not is_own_domain("apilexreviews.com", ["ApilexAI"])
    assert is_own_domain("apilex.ai", ["ApilexAI"])
    assert is_own_domain("postgresql.org", ["PostgreSQL 18"])
    s = state()
    s.analysis.entities = ["EU AI Act"]
    s.analysis.domain = "EU AI regulation"
    s.analysis.answer_type = "timeline"
    assert not is_own_domain("euaiact.com", self_primary_entities(s.analysis))


async def test_original_eu_scope_trap_is_rejected_even_if_the_model_accepts_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio
    import json
    from pathlib import Path

    from research_agent.agent.freshness import applicability_passages

    def load_fixture() -> dict[str, object]:
        rows = json.loads((Path(__file__).resolve().parents[4] / "evals/cases.json").read_text())[
            "cases"
        ]
        return dict(next(r for r in rows if r["id"] == "real-c36"))

    row = await asyncio.to_thread(load_fixture)
    source = Document.model_validate(row["document"])
    candidate = Claim.model_validate(row["claim"])
    assert applicability_passages(source, candidate)
    s = state()
    deps = await build_deps(
        run_id=s.run_id,
        settings=Settings(),
        toolkit=Toolkit(llm={}, search={}),
        events=MemoryEventSink(s.run_id),
        recorder=MemoryCallRecorder(),
        cache=MemoryCache(),
    )
    predictor = AsyncMock(
        return_value=SimpleNamespace(
            value=ClaimValidationOutput(
                items=[
                    ClaimValidation(
                        claim_id="candidate-0",
                        supported=True,
                        conditions_complete=True,
                        time_sensitive=False,
                        reason="incorrectly trusted the broad quoted sentence",
                    )
                ]
            )
        )
    )
    monkeypatch.setattr(deps, "predictor", predictor)
    await validate_claims([candidate], source, s, deps, deps.events)
    assert candidate.validation_status == "unsupported"
    assert "no applicability conditions" in candidate.validation_reason
