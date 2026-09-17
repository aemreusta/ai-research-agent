"""Output Gate G1-G11 (architecture v0.6 §11.2-11.3): one test group per rule, then the runner.

G12 (evidence eligibility) is tested with the evidence rules in `agent/test_evidence_regressions.py`,
and the Turkish-letter check of G10 in `agent/test_turkish_letters.py`.
"""

from __future__ import annotations

import uuid
from datetime import date

from research_agent.agent.state import (
    Claim,
    ClaimCluster,
    ClusterStatus,
    Document,
    Report,
    ReportSection,
    ReportSentence,
    ResearchState,
    SectionKey,
    SentenceKind,
    SourceEntry,
    SourceScore,
    SubQuestion,
    SubQuestionStatus,
)
from research_agent.gate import GateConfig, run_gate
from research_agent.gate.rules import evaluate

CONFIG = GateConfig.load()


def _doc(doc_id: str, url: str) -> Document:
    return Document(
        id=doc_id,
        url=url,
        canonical_url=url,
        domain=url.split("/")[2],
        title=f"Title {doc_id}",
        origin_id=f"o-{doc_id}",
        score=SourceScore(total=0.8),
    )


def _state(
    language: str = "en", question: str = "What is the EU AI Act timeline?"
) -> ResearchState:
    docs = {
        "d1": _doc("d1", "https://eur-lex.europa.eu/a"),
        "d2": _doc("d2", "https://reuters.com/b"),
        "d3": _doc("d3", "https://blog.example.com/c"),
    }
    claims = {
        "c1": Claim(
            id="c1",
            subq_id="s1",
            doc_id="d1",
            origin_id="o-d1",
            text="GPAI obligations apply from 2 August 2025.",
            quote="obligations for general-purpose AI models apply from 2 August 2025",
            entity="EU AI Act",
            attribute="gpai obligations start",
            value="2 August 2025",
        ),
        "c2": Claim(
            id="c2",
            subq_id="s1",
            doc_id="d2",
            origin_id="o-d2",
            text="GPAI rules started on 2 August 2025.",
            quote="rules ... 2 August 2025",
            entity="EU AI Act",
            attribute="gpai obligations start",
            value="2 August 2025",
        ),
        "c3": Claim(
            id="c3",
            subq_id="s1",
            doc_id="d3",
            origin_id="o-d3",
            text="The AI market reached $1.23 billion in revenue, growing 23%.",
            quote="reached $1.23 billion in revenue, growing 23%",
            entity="AI market",
            attribute="revenue",
            value="$1.23 billion",
        ),
    }
    clusters = {
        "k1": ClaimCluster(
            id="k1",
            subq_id="s1",
            claim_ids=["c1", "c2"],
            doc_ids=["d1", "d2"],
            origin_ids=["o-d1", "o-d2"],
            statement=claims["c1"].text,
            status=ClusterStatus.SUPPORTED,
        ),
        "k2": ClaimCluster(
            id="k2",
            subq_id="s1",
            claim_ids=["c3"],
            doc_ids=["d3"],
            origin_ids=["o-d3"],
            statement=claims["c3"].text,
            status=ClusterStatus.SINGLE_SOURCE,
        ),
    }
    plan = [
        SubQuestion(
            id="s1", text="When do GPAI obligations start?", status=SubQuestionStatus.SUFFICIENT
        ),
        SubQuestion(
            id="s2",
            text="What are the penalties?",
            status=SubQuestionStatus.EXHAUSTED,
            exhausted_reason="no sources",
        ),
    ]
    return ResearchState(
        run_id=uuid.uuid4(),
        question=question,
        language=language,
        as_of=date(2026, 9, 16),
        documents=docs,
        claims=claims,
        clusters=clusters,
        plan=plan,
    )


def _report(*sections: ReportSection, sources: list[SourceEntry] | None = None) -> Report:
    return Report(
        title="t",
        language="en",
        sections=list(sections),
        sources=sources
        if sources is not None
        else [
            SourceEntry(
                number=1,
                doc_id="d1",
                url="https://eur-lex.europa.eu/a",
                title="a",
                domain="eur-lex.europa.eu",
                score=0.8,
            ),
            SourceEntry(
                number=2,
                doc_id="d2",
                url="https://reuters.com/b",
                title="b",
                domain="reuters.com",
                score=0.8,
            ),
        ],
    )


def _fact(text: str, *clusters: str, labels: list[str] | None = None) -> ReportSentence:
    return ReportSentence(text=text, cluster_ids=list(clusters), labels=labels or [])


def _complete(*key_findings: ReportSentence, gaps: bool = True) -> Report:
    sections = [
        ReportSection(
            key=SectionKey.SUMMARY,
            title="Summary",
            sentences=[_fact("GPAI obligations apply from 2 August 2025.", "k1")],
        ),
        ReportSection(
            key=SectionKey.KEY_FINDINGS, title="Key Findings", sentences=list(key_findings)
        ),
        ReportSection(
            key=SectionKey.CONFLICTING,
            title="Conflicting",
            sentences=[ReportSentence(text="None found.", kind=SentenceKind.META)],
        ),
        ReportSection(
            key=SectionKey.CONCLUSION,
            title="Conclusion",
            sentences=[_fact("The GPAI regime has applied since 2 August 2025.", "k1")],
        ),
    ]
    if gaps:
        sections.append(
            ReportSection(
                key=SectionKey.KNOWN_GAPS,
                title="Known Gaps",
                sentences=[
                    ReportSentence(
                        text="What are the penalties? - no sources",
                        kind=SentenceKind.META,
                        labels=["gap:s2"],
                    )
                ],
            )
        )
    sections.append(ReportSection(key=SectionKey.SOURCES, title="Sources"))
    return _report(*sections)


def _rules(report: Report, state: ResearchState | None = None) -> dict[str, list[object]]:
    violations = evaluate(report, state or _state(), CONFIG)
    grouped: dict[str, list[object]] = {}
    for violation in violations:
        grouped.setdefault(violation.rule, []).append(violation)
    return grouped


def test_a_well_formed_report_passes_cleanly() -> None:
    report = _complete(_fact("GPAI obligations apply from 2 August 2025.", "k1"))
    assert _rules(report) == {}


# --- G1 ----------------------------------------------------------------------------------------------


def test_g1_missing_sections() -> None:
    report = _report(ReportSection(key=SectionKey.SUMMARY, title="Summary"))
    missing = {v.details["section"] for v in _rules(report)["G1"]}  # type: ignore[attr-defined]
    assert missing >= {
        "key_findings",
        "conflicting_or_uncertain",
        "conclusion",
        "sources",
        "known_gaps",
    }


def test_g1_known_gaps_is_only_required_when_something_is_exhausted() -> None:
    state = _state()
    state.plan[1].status = SubQuestionStatus.SUFFICIENT
    report = _complete(_fact("GPAI obligations apply from 2 August 2025.", "k1"), gaps=False)
    assert "G1" not in _rules(report, state)


# --- G2 / G3 -----------------------------------------------------------------------------------------


def test_g2_uncited_fact() -> None:
    report = _complete(_fact("The Act bans social scoring."))
    assert _rules(report)["G2"][0].severity == "error"  # type: ignore[attr-defined]


def test_g2_meta_sentences_need_no_citation() -> None:
    report = _complete(
        ReportSentence(text="This section lists the findings.", kind=SentenceKind.META)
    )
    assert "G2" not in _rules(report)


def test_g3_unknown_cluster_id() -> None:
    report = _complete(_fact("GPAI obligations apply from 2 August 2025.", "k1", "k99"))
    assert "G3" in _rules(report)


def test_g3_source_list_must_match_citations() -> None:
    report = _complete(_fact("GPAI obligations apply from 2 August 2025.", "k1"))
    report.sources.append(
        SourceEntry(
            number=3,
            doc_id="d3",
            url="https://blog.example.com/c",
            title="c",
            domain="blog.example.com",
            score=0.3,
        )
    )
    assert "G3" in _rules(report), "an uncited source in the list"


# --- G4 ----------------------------------------------------------------------------------------------


def test_g4_b1_unsupported_number_is_an_error() -> None:
    """The dangerous hallucination: a precise figure no source gave."""
    report = _complete(
        _fact("GPAI obligations apply from 2 August 2025, with fines up to €35 million.", "k1")
    )
    violation = _rules(report)["G4"][0]
    assert violation.severity == "error"  # type: ignore[attr-defined]
    assert violation.details["bucket"] == "bare_fact"  # type: ignore[attr-defined]


def test_g4_b1_supported_numbers_pass() -> None:
    report = _complete(
        _fact("The AI market reached $1.23 billion, growing 23%.", "k2", labels=["single_source"])
    )
    assert "G4" not in _rules(report)


def test_g4_b3_rounding_is_a_warning_not_a_deletion() -> None:
    report = _complete(
        _fact("The AI market reached about $1.2 billion.", "k2", labels=["single_source"])
    )
    violation = _rules(report)["G4"][0]
    assert violation.severity == "warn"  # type: ignore[attr-defined]
    assert violation.details["bucket"] == "within_tolerance"  # type: ignore[attr-defined]


def test_g4_b3_date_granularity_is_tolerated() -> None:
    report = _complete(_fact("GPAI obligations have applied since August 2025.", "k1"))
    violation = _rules(report)["G4"][0]
    assert violation.severity == "warn"  # type: ignore[attr-defined]


def test_g4_b2_ledger_derived_counts_are_recomputed() -> None:
    right = _complete(
        _fact(
            "Two independent sources confirm that GPAI obligations apply from 2 August 2025 (2 sources).",
            "k1",
        )
    )
    assert "G4" not in _rules(right)
    wrong = _complete(
        _fact("GPAI obligations apply from 2 August 2025, confirmed by 5 sources.", "k1")
    )
    violation = _rules(wrong)["G4"][0]
    assert violation.details["bucket"] == "ledger_derived"  # type: ignore[attr-defined]
    assert violation.details["expected"] == 2  # type: ignore[attr-defined]


def test_g4_numbers_from_the_question_are_context_not_claims() -> None:
    state = _state(question="KVKK 2026 aksiyon planı nedir?", language="tr")
    report = _complete(_fact("GPAI obligations apply from 2 August 2025 and matter in 2026.", "k1"))
    assert "G4" not in _rules(report, state)


def test_g4_a_different_currency_is_not_support() -> None:
    report = _complete(
        _fact("The AI market reached $1.23 billion.", "k2", labels=["single_source"])
    )
    assert "G4" not in _rules(report)
    euro = _complete(_fact("The AI market reached €1.23 billion.", "k2", labels=["single_source"]))
    assert _rules(euro)["G4"][0].severity == "error"  # type: ignore[attr-defined]


# --- G5 / G6 / G7 --------------------------------------------------------------------------------------


def test_g5_contested_claims_must_be_hedged() -> None:
    state = _state()
    state.clusters["k1"].status = ClusterStatus.CONTESTED
    report = _complete(_fact("GPAI obligations apply from 2 August 2025.", "k1"))
    assert _rules(report, state)["G5"][0].severity == "warn"  # type: ignore[attr-defined]
    hedged = _complete(
        _fact("GPAI obligations apply from 2 August 2025.", "k1", labels=["uncertain"])
    )
    hedged.sections[0].sentences[0].labels.append("uncertain")
    hedged.sections[3].sentences[0].labels.append("uncertain")
    assert "G5" not in _rules(hedged, state)


def test_g6_single_source_findings_are_labelled() -> None:
    report = _complete(_fact("The AI market reached $1.23 billion.", "k2"))
    assert _rules(report)["G6"][0].severity == "warn"  # type: ignore[attr-defined]


def test_g7_exhausted_subquestions_appear_in_known_gaps() -> None:
    report = _complete(_fact("GPAI obligations apply from 2 August 2025.", "k1"))
    report.section(SectionKey.KNOWN_GAPS).sentences.clear()  # type: ignore[union-attr]
    assert _rules(report)["G7"][0].details["subq_id"] == "s2"  # type: ignore[attr-defined]


# --- G8 / G9 / G10 / G11 ----------------------------------------------------------------------------------


def test_g8_invalid_and_duplicate_urls() -> None:
    report = _complete(_fact("GPAI obligations apply from 2 August 2025.", "k1"))
    report.sources[1].url = "javascript:alert(1)"
    report.sources.append(
        SourceEntry(
            number=3,
            doc_id="d1",
            url="https://eur-lex.europa.eu/a/",
            title="dup",
            domain="eur-lex.europa.eu",
            score=0.8,
        )
    )
    kinds = {v.details["problem"] for v in _rules(report)["G8"]}  # type: ignore[attr-defined]
    assert kinds >= {"invalid", "duplicate"}


def test_g9_sensitive_identifiers_in_the_output() -> None:
    report = _complete(
        _fact("Contact ali@example.com; GPAI obligations apply from 2 August 2025.", "k1")
    )
    assert "G9" in _rules(report)


def test_g10_report_language_must_match_the_question() -> None:
    state = _state(language="tr", question="EU AI Act takvimi nedir?")
    report = _complete(_fact("GPAI obligations apply from 2 August 2025.", "k1"))
    assert _rules(report, state)["G10"][0].severity == "warn"  # type: ignore[attr-defined]


def test_g11_recommendations_need_findings() -> None:
    report = _complete(_fact("GPAI obligations apply from 2 August 2025.", "k1"))
    report.sections.insert(
        4,
        ReportSection(
            key=SectionKey.RECOMMENDATIONS,
            title="Recommendations",
            sentences=[
                ReportSentence(
                    text="Audit your GPAI models now.",
                    kind=SentenceKind.RECOMMENDATION,
                    finding_refs=["k1"],
                ),
                ReportSentence(text="Hire three lawyers.", kind=SentenceKind.RECOMMENDATION),
            ],
        ),
    )
    violations = _rules(report)["G11"]
    assert len(violations) == 1


# --- runner ---------------------------------------------------------------------------------------------


def test_the_runner_remediates_and_passes_with_warnings() -> None:
    report = _complete(
        _fact("GPAI obligations apply from 2 August 2025.", "k1"),
        _fact("The AI market reached $1.23 billion.", "k2"),  # G6 -> label
        _fact("The Commission fined Acme €35 million.", "k1"),  # G4 B1 -> drop
        _fact("Nobody cites this."),  # G2 -> drop
    )
    outcome = run_gate(report, _state(), CONFIG)
    assert outcome.verdict == "pass_with_warnings"
    texts = [s.text for _, s in outcome.report.sentences()]
    assert "Nobody cites this." not in texts
    assert not any("€35 million" in text for text in texts)
    single = next(s for _, s in outcome.report.sentences() if "$1.23" in s.text)
    assert "single_source" in single.labels
    assert outcome.removed_sentences == 2
    note = outcome.report.section(SectionKey.SUMMARY).sentences[-1]  # type: ignore[union-attr]
    assert "2 sentence(s) removed" in note.text


def test_the_runner_rebuilds_the_source_list() -> None:
    report = _complete(
        _fact("The AI market reached $1.23 billion.", "k2", labels=["single_source"]),
        _fact("GPAI obligations apply from 2 August 2025.", "k1"),
    )
    report.sources = []
    outcome = run_gate(report, _state(), CONFIG)
    assert {source.doc_id for source in outcome.report.sources} == {"d1", "d2", "d3"}
    assert [source.number for source in outcome.report.sources] == [1, 2, 3]


def test_an_unfixable_report_fails_with_a_banner() -> None:
    """With every fact dropped, the required sections are left empty: nothing to stand on."""
    report = _complete(_fact("Invented €99 billion figure.", "k1"))
    for section in report.sections:
        section.sentences = [s for s in section.sentences if s.kind is SentenceKind.META]
    report.sections[0].sentences = [_fact("Totally made up 77% claim.", "k1")]
    outcome = run_gate(report, _state(), CONFIG)
    assert outcome.verdict == "fail"
    assert outcome.banner


def test_the_gate_is_deterministic() -> None:
    report = _complete(
        _fact("GPAI obligations apply from 2 August 2025.", "k1"),
        _fact("The AI market reached about $1.2 billion.", "k2"),
    )
    first = run_gate(report, _state(), CONFIG).to_dict()
    second = run_gate(report, _state(), CONFIG).to_dict()
    assert first == second


def test_zero_remediation_rounds_preserves_the_report() -> None:
    report = _complete(_fact("Nobody cites this."))
    before = report.model_dump()
    outcome = run_gate(report, _state(), CONFIG, max_rounds=0)
    assert outcome.rounds == 0
    assert outcome.removed_sentences == 0
    assert outcome.report.model_dump() == before
    assert outcome.verdict == "fail"


def test_one_remediation_round_is_a_strict_limit() -> None:
    report = _complete(_fact("GPAI obligations apply from 2 August 2025.", "k1"))
    # G4 produces a persistent warning even after the approximate label has been added.
    report.sections[0].sentences.append(_fact("GPAI obligations apply from August 2025.", "k1"))
    outcome = run_gate(report, _state(), CONFIG, max_rounds=1)
    assert outcome.rounds == 1


def test_the_gate_result_records_every_rule() -> None:
    outcome = run_gate(
        _complete(_fact("GPAI obligations apply from 2 August 2025.", "k1")), _state(), CONFIG
    )
    result = outcome.to_dict()
    assert set(result["checks"]) == {f"G{i}" for i in range(1, 13)}
    assert result["verdict"] == "pass"
