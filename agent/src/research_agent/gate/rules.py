"""Rules G1-G11. Each is a plain function `(report, state, config) -> list[Violation]`.

Sentences are addressed by (section key, index) so remediation can act on exactly the sentence
a rule flagged, and every violation carries the details that end up in `gate_result.json`.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from research_agent.agent.dedup import canonicalize_url
from research_agent.agent.state import (
    ClusterStatus,
    Report,
    ReportSentence,
    ResearchState,
    SectionKey,
    SentenceKind,
    SubQuestionStatus,
)
from research_agent.agent.text import detect_language
from research_agent.gate.config import GateConfig
from research_agent.gate.numeric import (
    Kind,
    Quantity,
    extract_quantities,
    normalised_label,
    quantities_match,
)
from research_agent.observability.redaction import redact_text

LABEL_UNCERTAIN = "uncertain"
LABEL_SINGLE_SOURCE = "single_source"
LABEL_APPROXIMATE = "approximate"
GAP_LABEL_PREFIX = "gap:"

# Numbers that count something in the ledger rather than state a fact from a source (G4 / B2).
_LEDGER_NOUN = re.compile(
    r"^\s*(?:independent\s+|bağımsız\s+|farklı\s+|distinct\s+)?"
    r"(?P<noun>sources?|kaynak\w*|findings?|bulgu\w*|sub-?questions?|alt\s+soru\w*|"
    r"searches|arama\w*|contradictions?|çelişki\w*|queries|sorgu\w*)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Violation:
    rule: str
    severity: str  # "error" | "warn"
    message: str
    section: SectionKey | None = None
    index: int | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "severity": self.severity,
            "message": self.message,
            "section": self.section.value if self.section else None,
            "sentence": self.index,
            **self.details,
        }


Rule = Callable[[Report, ResearchState, GateConfig], list[Violation]]


def _addressed(report: Report) -> Iterable[tuple[SectionKey, int, ReportSentence]]:
    for section in report.sections:
        for index, sentence in enumerate(section.sentences):
            yield section.key, index, sentence


# --- G1: required sections ---------------------------------------------------------------------


def g1_sections(report: Report, state: ResearchState, config: GateConfig) -> list[Violation]:
    present = {section.key.value for section in report.sections}
    required = list(config.required_sections)
    if any(subq.status is SubQuestionStatus.EXHAUSTED for subq in state.plan):
        required.append(SectionKey.KNOWN_GAPS.value)
    violations = [
        Violation(
            "G1",
            "error",
            f"required section '{name}' is missing",
            details={"section": name, "remediable": True},
        )
        for name in required
        if name not in present
    ]
    # A report whose findings were all removed has nothing to stand on. Filling the section with
    # "None" would make it look like a clean answer - the silent failure the gate exists to stop.
    findings = report.section(SectionKey.KEY_FINDINGS)
    if findings is not None and not any(
        sentence.kind is SentenceKind.FACT and sentence.cluster_ids
        for sentence in findings.sentences
    ):
        violations.append(
            Violation(
                "G1",
                "error",
                "no supported finding remains: insufficient evidence",
                section=SectionKey.KEY_FINDINGS,
                details={"section": "key_findings", "remediable": False},
            )
        )
    return violations


# --- G2 / G3: citations ------------------------------------------------------------------------


def g2_citations(report: Report, state: ResearchState, config: GateConfig) -> list[Violation]:
    return [
        Violation(
            "G2",
            "error",
            "factual sentence without a citation",
            key,
            index,
            {"text": sentence.text[:160]},
        )
        for key, index, sentence in _addressed(report)
        if sentence.kind is SentenceKind.FACT and not sentence.cluster_ids
    ]


def cited_doc_ids(report: Report, state: ResearchState) -> list[str]:
    """Documents behind every cited finding, in order of first citation."""
    ordered: list[str] = []
    for _, _, sentence in _addressed(report):
        for cluster_id in [*sentence.cluster_ids, *sentence.finding_refs]:
            cluster = state.clusters.get(cluster_id)
            if cluster is None:
                continue
            for doc_id in cluster.doc_ids:
                if doc_id not in ordered and doc_id in state.documents:
                    ordered.append(doc_id)
    return ordered


def g3_ledger(report: Report, state: ResearchState, config: GateConfig) -> list[Violation]:
    violations = [
        Violation(
            "G3",
            "error",
            f"citation '{cluster_id}' is not in the ledger",
            key,
            index,
            {"cluster_id": cluster_id},
        )
        for key, index, sentence in _addressed(report)
        for cluster_id in sentence.cluster_ids
        if cluster_id not in state.clusters
    ]
    listed = [source.doc_id for source in report.sources]
    cited = set(cited_doc_ids(report, state))
    for doc_id in dict.fromkeys(listed):
        if doc_id not in cited:
            violations.append(
                Violation(
                    "G3",
                    "error",
                    f"source {doc_id} is listed but never cited",
                    details={"doc_id": doc_id},
                )
            )
    for doc_id in sorted(cited - set(listed)):
        violations.append(
            Violation(
                "G3",
                "error",
                f"cited source {doc_id} is missing from the list",
                details={"doc_id": doc_id},
            )
        )
    return violations


# --- G4: numbers -------------------------------------------------------------------------------


def _quantities(text: str) -> list[Quantity]:
    return extract_quantities(text, language=detect_language(text))


def _support(state: ResearchState, cluster_ids: Iterable[str]) -> list[Quantity]:
    found: list[Quantity] = []
    for cluster_id in cluster_ids:
        cluster = state.clusters.get(cluster_id)
        if cluster is None:
            continue
        texts = [cluster.statement, cluster.value or ""]
        for claim_id in cluster.claim_ids:
            claim = state.claims.get(claim_id)
            if claim is not None:
                texts.extend([claim.text, claim.quote, claim.value or "", claim.as_of or ""])
        for text in texts:
            if text:
                found.extend(_quantities(text))
    return found


def _ledger_expected(
    noun: str, state: ResearchState, cluster_ids: list[str], report: Report
) -> set[int]:
    noun = noun.lower()
    clusters = [state.clusters[c] for c in cluster_ids if c in state.clusters]
    if noun.startswith(("source", "kaynak")):
        if clusters:
            docs = {doc for cluster in clusters for doc in cluster.doc_ids}
            origins = {origin for cluster in clusters for origin in cluster.origin_ids}
            return {len(docs), len(origins)}
        return {len(report.sources)}
    if noun.startswith(("finding", "bulgu")):
        return {len(state.clusters), len(clusters)}
    if noun.startswith(("sub", "alt")):
        return {len(state.plan)}
    if noun.startswith(("search", "arama", "quer", "sorgu")):
        return {len(state.queries), sum(1 for q in state.queries if q.status.value == "done")}
    return {len(state.contradictions)}


def g4_numbers(report: Report, state: ResearchState, config: GateConfig) -> list[Violation]:
    tolerance = config.tolerance
    context = _quantities(state.question)
    violations: list[Violation] = []

    for key, index, sentence in _addressed(report):
        if sentence.kind is SentenceKind.META:
            continue
        refs = sentence.cluster_ids if sentence.kind is SentenceKind.FACT else sentence.finding_refs
        support: list[Quantity] | None = None
        for quantity in _quantities(sentence.text):
            if any(
                quantities_match(quantity, c, relative=0, percent_points=0, date_days=0)
                for c in context
            ):
                continue

            if quantity.kind is Kind.NUMBER:
                tail = sentence.text[quantity.end :]
                if match := _LEDGER_NOUN.match(tail):
                    expected = _ledger_expected(match["noun"], state, refs, report)
                    stated = int(quantity.value or -1)
                    if stated not in expected:
                        violations.append(
                            Violation(
                                "G4",
                                "error",
                                f"'{quantity.raw} {match['noun']}' does not match the ledger",
                                key,
                                index,
                                {
                                    "bucket": "ledger_derived",
                                    "value": quantity.raw,
                                    "expected": min(expected),
                                    "text": sentence.text[:160],
                                },
                            )
                        )
                    continue

            if support is None:
                support = _support(state, refs)
            exact = any(
                quantities_match(quantity, s, relative=0, percent_points=0, date_days=0)
                for s in support
            )
            if exact:
                continue
            tolerant = any(
                quantities_match(
                    quantity,
                    s,
                    relative=tolerance.numeric_relative,
                    percent_points=tolerance.percentage_absolute,
                    date_days=tolerance.date_granularity_days,
                )
                for s in support
            )
            if tolerant:
                violations.append(
                    Violation(
                        "G4",
                        "warn",
                        f"'{quantity.raw}' is an approximation of a cited value",
                        key,
                        index,
                        {
                            "bucket": "within_tolerance",
                            "value": normalised_label(quantity),
                            "text": sentence.text[:160],
                        },
                    )
                )
            else:
                violations.append(
                    Violation(
                        "G4",
                        "error",
                        f"'{quantity.raw}' is not supported by any cited claim",
                        key,
                        index,
                        {
                            "bucket": "bare_fact",
                            "value": normalised_label(quantity),
                            "text": sentence.text[:160],
                        },
                    )
                )
    return violations


# --- G5 / G6 / G7: labelling -------------------------------------------------------------------


def g5_contested(report: Report, state: ResearchState, config: GateConfig) -> list[Violation]:
    violations = []
    for key, index, sentence in _addressed(report):
        contested = [
            c
            for c in sentence.cluster_ids
            if (cluster := state.clusters.get(c)) and cluster.status is ClusterStatus.CONTESTED
        ]
        if not contested:
            continue
        if key is SectionKey.CONFLICTING or LABEL_UNCERTAIN in sentence.labels:
            continue
        violations.append(
            Violation(
                "G5",
                "warn",
                "cites a contested finding without hedging",
                key,
                index,
                {"cluster_ids": contested},
            )
        )
    return violations


def g6_single_source(report: Report, state: ResearchState, config: GateConfig) -> list[Violation]:
    violations = []
    for key, index, sentence in _addressed(report):
        if sentence.kind is not SentenceKind.FACT or not sentence.cluster_ids:
            continue
        clusters = [state.clusters[c] for c in sentence.cluster_ids if c in state.clusters]
        only_single = bool(clusters) and all(
            c.status is ClusterStatus.SINGLE_SOURCE for c in clusters
        )
        if only_single and LABEL_SINGLE_SOURCE not in sentence.labels:
            violations.append(
                Violation("G6", "warn", "single-source finding is not labelled", key, index)
            )
    return violations


def g7_known_gaps(report: Report, state: ResearchState, config: GateConfig) -> list[Violation]:
    gaps = report.section(SectionKey.KNOWN_GAPS)
    listed = {
        label.removeprefix(GAP_LABEL_PREFIX)
        for sentence in (gaps.sentences if gaps else [])
        for label in sentence.labels
        if label.startswith(GAP_LABEL_PREFIX)
    }
    return [
        Violation(
            "G7",
            "error",
            f"exhausted sub-question {subq.id} is not in Known Gaps",
            details={"subq_id": subq.id},
        )
        for subq in state.plan
        if subq.status is SubQuestionStatus.EXHAUSTED and subq.id not in listed
    ]


# --- G8 / G9 / G10 / G11 -----------------------------------------------------------------------


def g8_urls(report: Report, state: ResearchState, config: GateConfig) -> list[Violation]:
    violations = []
    seen_urls: set[str] = set()
    seen_docs: set[str] = set()
    for source in report.sources:
        canonical = canonicalize_url(source.url)
        if canonical is None:
            violations.append(
                Violation(
                    "G8",
                    "error",
                    f"invalid URL for source {source.number}",
                    details={"problem": "invalid", "number": source.number},
                )
            )
            continue
        if canonical != source.url:
            violations.append(
                Violation(
                    "G8",
                    "error",
                    f"source {source.number} is not canonical",
                    details={"problem": "not_canonical", "number": source.number},
                )
            )
        if canonical in seen_urls or source.doc_id in seen_docs:
            violations.append(
                Violation(
                    "G8",
                    "error",
                    f"source {source.number} is a duplicate",
                    details={"problem": "duplicate", "number": source.number},
                )
            )
        seen_urls.add(canonical)
        seen_docs.add(source.doc_id)
    return violations


def g9_sensitive(report: Report, state: ResearchState, config: GateConfig) -> list[Violation]:
    violations = [
        Violation("G9", "error", "sensitive identifier or secret in the output", key, index)
        for key, index, sentence in _addressed(report)
        if redact_text(sentence.text) != sentence.text
    ]
    violations.extend(
        Violation(
            "G9",
            "error",
            f"sensitive data in source {source.number}",
            details={"number": source.number},
        )
        for source in report.sources
        if redact_text(source.title) != source.title
    )
    return violations


def g10_language(report: Report, state: ResearchState, config: GateConfig) -> list[Violation]:
    body = " ".join(s.text for _, _, s in _addressed(report) if s.kind is not SentenceKind.META)
    if not body.strip():
        return []
    detected = detect_language(body)
    if detected == state.language:
        return []
    return [
        Violation(
            "G10",
            "warn",
            f"report reads as '{detected}' but the question is '{state.language}'",
            details={"detected": detected, "expected": state.language},
        )
    ]


def g11_recommendations(
    report: Report, state: ResearchState, config: GateConfig
) -> list[Violation]:
    return [
        Violation(
            "G11",
            "error",
            "recommendation is not linked to a finding",
            key,
            index,
            {"text": sentence.text[:160]},
        )
        for key, index, sentence in _addressed(report)
        if sentence.kind is SentenceKind.RECOMMENDATION
        and not any(ref in state.clusters for ref in sentence.finding_refs)
    ]


def g12_evidence_eligibility(
    report: Report, state: ResearchState, config: GateConfig
) -> list[Violation]:
    """Never publish a finding whose own ledger says current applicability is unconfirmed."""
    violations = []
    for key, index, sentence in _addressed(report):
        for cid in dict.fromkeys([*sentence.cluster_ids, *sentence.finding_refs]):
            cluster = state.clusters.get(cid)
            if cluster is None:
                continue
            rejected = [
                claim_id
                for claim_id in cluster.claim_ids
                if claim_id in state.claims
                and state.claims[claim_id].validation_status in {"unsupported", "unavailable"}
            ]
            if cluster.requires_fresh_confirmation or rejected:
                violations.append(
                    Violation(
                        "G12",
                        "error",
                        "finding lacks validated, applicable source evidence",
                        key,
                        index,
                        {
                            "cluster_id": cid,
                            "requires_fresh_confirmation": cluster.requires_fresh_confirmation,
                            "rejected_claims": rejected,
                        },
                    )
                )
    return violations


RULES: dict[str, Rule] = {
    "G1": g1_sections,
    "G2": g2_citations,
    "G3": g3_ledger,
    "G4": g4_numbers,
    "G5": g5_contested,
    "G6": g6_single_source,
    "G7": g7_known_gaps,
    "G8": g8_urls,
    "G9": g9_sensitive,
    "G10": g10_language,
    "G11": g11_recommendations,
    "G12": g12_evidence_eligibility,
}


def evaluate(report: Report, state: ResearchState, config: GateConfig) -> list[Violation]:
    violations: list[Violation] = []
    for rule in RULES.values():
        violations.extend(rule(report, state, config))
    return violations
