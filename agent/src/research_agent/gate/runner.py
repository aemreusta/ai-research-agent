"""Run the rules, remediate deterministically, run them again - then decide.

    evaluate -> remediate -> evaluate   (at most `gate.max_remediation_rounds` rounds)

Verdict: `fail` if an error survives remediation, `pass_with_warnings` if anything was found at
any point (a dropped sentence changes the report even if the final pass is clean), `pass`
otherwise. A failed report is still returned, with a banner and the violation list on top:
there is no silent failure (§11.2). And whenever a sentence was removed the report says so.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from research_agent.agent.coverage import localised_reason
from research_agent.agent.dedup import canonicalize_url
from research_agent.agent.state import (
    Report,
    ReportSection,
    ReportSentence,
    ResearchState,
    SectionKey,
    SentenceKind,
    SourceEntry,
    SubQuestionStatus,
)
from research_agent.gate.config import GateConfig
from research_agent.gate.rules import (
    GAP_LABEL_PREFIX,
    LABEL_APPROXIMATE,
    LABEL_SINGLE_SOURCE,
    LABEL_UNCERTAIN,
    RULES,
    Violation,
    cited_doc_ids,
    evaluate,
)
from research_agent.observability.redaction import redact_text

SECTION_TITLES: dict[str, dict[SectionKey, str]] = {
    "en": {
        SectionKey.SUMMARY: "Summary",
        SectionKey.KEY_FINDINGS: "Key Findings",
        SectionKey.CONFLICTING: "Conflicting / Uncertain Information",
        SectionKey.CONCLUSION: "Conclusion",
        SectionKey.RECOMMENDATIONS: "Recommendations / Action Plan",
        SectionKey.KNOWN_GAPS: "Known Gaps",
        SectionKey.SOURCES: "Sources",
    },
    "tr": {
        SectionKey.SUMMARY: "Özet",
        SectionKey.KEY_FINDINGS: "Temel Bulgular",
        SectionKey.CONFLICTING: "Çelişkili / Belirsiz Bilgiler",
        SectionKey.CONCLUSION: "Sonuç",
        SectionKey.RECOMMENDATIONS: "Öneriler / Aksiyon Planı",
        SectionKey.KNOWN_GAPS: "Bilinen Eksikler",
        SectionKey.SOURCES: "Kaynaklar",
    },
}
SECTION_ORDER = [
    SectionKey.SUMMARY,
    SectionKey.KEY_FINDINGS,
    SectionKey.CONFLICTING,
    SectionKey.RECOMMENDATIONS,
    SectionKey.CONCLUSION,
    SectionKey.KNOWN_GAPS,
    SectionKey.SOURCES,
]
_NONE = {"en": "None found.", "tr": "Bulunmadı."}
_DROP_NOTE = {
    "en": "{count} sentence(s) removed for insufficient evidence - see gate_result.json",
    "tr": "Kanıt yetersizliği nedeniyle {count} cümle çıkarıldı - ayrıntılar gate_result.json'da",
}
_BANNER = {
    "en": "Insufficient evidence: this report did not pass the output checks. "
    "Treat it as a partial draft and review the listed issues.",
    "tr": "Yetersiz kanıt: Bu rapor çıktı kontrollerinden geçemedi. "
    "Kısmi bir taslak olarak değerlendirin ve listelenen sorunları inceleyin.",
}


def section_title(key: SectionKey, language: str) -> str:
    return SECTION_TITLES.get(language, SECTION_TITLES["en"])[key]


@dataclass
class GateOutcome:
    verdict: str
    report: Report
    initial: list[Violation]
    final: list[Violation]
    remediations: list[dict[str, Any]] = field(default_factory=list)
    removed_sentences: int = 0
    rounds: int = 0
    banner: str | None = None
    rule_descriptions: dict[str, str] = field(default_factory=dict)

    def checks(self) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for rule_id in RULES:
            before = [v for v in self.initial if v.rule == rule_id]
            after = [v for v in self.final if v.rule == rule_id]
            if any(v.severity == "error" for v in after):
                status = "fail"
            elif after:
                status = "warn"
            elif before:
                status = "remediated"
            else:
                status = "pass"
            result[rule_id] = {
                "status": status,
                "description": self.rule_descriptions.get(rule_id, ""),
                "found": [v.to_dict() for v in before],
                "remaining": [v.to_dict() for v in after],
            }
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "rounds": self.rounds,
            "removed_sentences": self.removed_sentences,
            "banner": self.banner,
            "checks": self.checks(),
            "remediations": self.remediations,
        }


def _clone(report: Report) -> Report:
    return report.model_copy(deep=True)


def _ensure_section(report: Report, key: SectionKey, language: str) -> ReportSection:
    existing = report.section(key)
    if existing is not None:
        return existing
    section = ReportSection(key=key, title=section_title(key, language))
    report.sections.append(section)
    report.sections.sort(key=lambda s: SECTION_ORDER.index(s.key) if s.key in SECTION_ORDER else 99)
    return section


def rebuild_sources(report: Report, state: ResearchState) -> None:
    """The source list is always derived, never trusted: cited documents, canonical, numbered."""
    entries: list[SourceEntry] = []
    seen: set[str] = set()
    for doc_id in cited_doc_ids(report, state):
        document = state.documents[doc_id]
        url = canonicalize_url(document.canonical_url or document.url)
        if url is None or url in seen:
            continue
        seen.add(url)
        entries.append(
            SourceEntry(
                number=len(entries) + 1,
                doc_id=doc_id,
                url=url,
                title=redact_text(document.title or url),
                domain=document.domain,
                score=document.score.total,
            )
        )
    report.sources = entries


def _remediate(
    report: Report, state: ResearchState, violations: list[Violation], language: str
) -> tuple[list[dict[str, Any]], int]:
    actions: list[dict[str, Any]] = []
    drop: set[tuple[SectionKey, int]] = set()

    def sentence(v: Violation) -> ReportSentence | None:
        if v.section is None or v.index is None:
            return None
        section = report.section(v.section)
        if section is None or v.index >= len(section.sentences):
            return None
        return section.sentences[v.index]

    def label(v: Violation, name: str) -> None:
        target = sentence(v)
        if target is not None and name not in target.labels:
            target.labels.append(name)
            actions.append(
                {
                    "rule": v.rule,
                    "action": f"label:{name}",
                    "section": v.section.value if v.section else None,
                    "sentence": v.index,
                }
            )

    for v in violations:
        if v.rule == "G1" and v.details.get("remediable"):
            key = SectionKey(v.details["section"])
            section = _ensure_section(report, key, language)
            if key is not SectionKey.SOURCES and not section.sentences:
                section.sentences.append(
                    ReportSentence(text=_NONE.get(language, _NONE["en"]), kind=SentenceKind.META)
                )
            actions.append({"rule": "G1", "action": "add_section", "section": key.value})
        elif v.rule in {"G2", "G11", "G12"} or (v.rule == "G4" and v.severity == "error"):
            if v.section is not None and v.index is not None:
                drop.add((v.section, v.index))
        elif v.rule == "G3" and "cluster_id" in v.details:
            target = sentence(v)
            if target is not None:
                target.cluster_ids = [c for c in target.cluster_ids if c in state.clusters]
                if not target.cluster_ids and target.kind is SentenceKind.FACT and v.section:
                    drop.add((v.section, v.index or 0))
                actions.append(
                    {"rule": "G3", "action": "drop_citation", "cluster_id": v.details["cluster_id"]}
                )
        elif v.rule == "G4":
            label(v, LABEL_APPROXIMATE)
        elif v.rule == "G5":
            label(v, LABEL_UNCERTAIN)
        elif v.rule == "G6":
            label(v, LABEL_SINGLE_SOURCE)
        elif v.rule == "G7":
            subq = state.subquestion(v.details["subq_id"])
            if subq is not None:
                gaps = _ensure_section(report, SectionKey.KNOWN_GAPS, language)
                reason = localised_reason(subq.exhausted_reason, language)
                text = f"{subq.text} - {reason}" if reason else subq.text
                gaps.sentences.append(
                    ReportSentence(
                        text=text, kind=SentenceKind.META, labels=[f"{GAP_LABEL_PREFIX}{subq.id}"]
                    )
                )
                actions.append({"rule": "G7", "action": "add_gap", "subq_id": subq.id})
        elif v.rule == "G9":
            target = sentence(v)
            if target is not None:
                target.text = redact_text(target.text)
                actions.append({"rule": "G9", "action": "mask", "sentence": v.index})

    removed = 0
    for section in report.sections:
        kept = []
        for index, item in enumerate(section.sentences):
            if (section.key, index) in drop:
                removed += 1
                actions.append(
                    {
                        "rule": "drop",
                        "action": "drop_sentence",
                        "section": section.key.value,
                        "text": item.text[:160],
                    }
                )
            else:
                kept.append(item)
        section.sentences = kept

    rebuild_sources(report, state)
    return actions, removed


def run_gate(
    report: Report, state: ResearchState, config: GateConfig, *, max_rounds: int = 1
) -> GateOutcome:
    if max_rounds < 0:
        raise ValueError("max_rounds must be nonnegative")
    language = state.language
    working = _clone(report)
    if max_rounds:
        for section in working.sections:
            section.title = section_title(section.key, language)
        # Zero rounds is report-only, including normalization and missing sections.
        if any(s.status is SubQuestionStatus.EXHAUSTED for s in state.plan):
            _ensure_section(working, SectionKey.KNOWN_GAPS, language)

    initial = evaluate(working, state, config)
    violations = initial
    actions: list[dict[str, Any]] = []
    removed = 0
    rounds = 0
    while violations and rounds < max_rounds:
        remediable = [v for v in violations if v.details.get("remediable", True)]
        if not remediable:
            break
        rounds += 1
        round_actions, round_removed = _remediate(working, state, remediable, language)
        actions.extend(round_actions)
        removed += round_removed
        violations = evaluate(working, state, config)

    if removed:
        summary = _ensure_section(working, SectionKey.SUMMARY, language)
        template = _DROP_NOTE.get(language, _DROP_NOTE["en"])
        summary.sentences.append(
            ReportSentence(
                text=template.format(count=removed), kind=SentenceKind.META, labels=["gate_note"]
            )
        )

    errors = [v for v in violations if v.severity == "error"]
    if errors:
        verdict = "fail"
    elif initial or violations or removed:
        verdict = "pass_with_warnings"
    else:
        verdict = "pass"
    return GateOutcome(
        verdict=verdict,
        report=working,
        initial=initial,
        final=violations,
        remediations=actions,
        removed_sentences=removed,
        rounds=rounds,
        banner=_BANNER.get(language, _BANNER["en"]) if verdict == "fail" else None,
        rule_descriptions={rule_id: config.rule(rule_id).description for rule_id in RULES},
    )
