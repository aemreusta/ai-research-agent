"""Render the gated report as Markdown and JSON, with numbered citations and run metadata."""

from __future__ import annotations

from typing import Any

from research_agent.agent.state import (
    Report,
    ReportSentence,
    ResearchState,
    SectionKey,
    SubQuestionStatus,
)
from research_agent.gate.rules import LABEL_APPROXIMATE, LABEL_SINGLE_SOURCE, LABEL_UNCERTAIN

LABELS = {
    "en": {
        LABEL_SINGLE_SOURCE: "single independent source",
        LABEL_APPROXIMATE: "approximate",
        LABEL_UNCERTAIN: "uncertain",
    },
    "tr": {
        LABEL_SINGLE_SOURCE: "tek bağımsız kaynak",
        LABEL_APPROXIMATE: "yaklaşık",
        LABEL_UNCERTAIN: "belirsiz",
    },
}
WORDS = {
    "en": {
        "based_on": "based on",
        "none_found": "None found.",
        "metadata": "Run metadata",
        "stop": "Stop reason",
        "rounds": "Research rounds",
        "searches": "Searches",
        "subqs": "Sub-questions",
        "sources": "Sources read",
        "findings": "Findings",
        "cost": "LLM cost",
        "tokens": "Tokens (in/out)",
        "gate": "Output gate",
        "removed": "removed",
        "config": "Config hash",
        "models": "Models",
        "prompts": "Prompt versions",
        "skills": "Skills",
        "none": "none",
        "score": "score",
        "primary": "primary",
        "answered": "answered",
        "open": "open",
        "exhausted": "exhausted",
        "at": "as of",
    },
    "tr": {
        "based_on": "dayanak",
        "none_found": "Bulunmadı.",
        "metadata": "Çalışma bilgileri",
        "stop": "Durma nedeni",
        "rounds": "Araştırma turu",
        "searches": "Arama",
        "subqs": "Alt sorular",
        "sources": "Okunan kaynak",
        "findings": "Bulgu",
        "cost": "LLM maliyeti",
        "tokens": "Token (giriş/çıkış)",
        "gate": "Çıktı kontrolü",
        "removed": "çıkarıldı",
        "config": "Konfig hash",
        "models": "Modeller",
        "prompts": "Prompt sürümleri",
        "skills": "Skill'ler",
        "none": "yok",
        "score": "skor",
        "primary": "birincil",
        "answered": "cevaplandı",
        "open": "açık",
        "exhausted": "tükendi",
        "at": "tarih",
    },
}


def _words(language: str) -> dict[str, str]:
    return WORDS.get(language, WORDS["en"])


def citation_numbers(report: Report, state: ResearchState) -> dict[str, list[int]]:
    """cluster id -> source numbers, from the (gate-built) source list."""
    number_of = {source.doc_id: source.number for source in report.sources}
    mapping: dict[str, list[int]] = {}
    for cluster_id, cluster in state.clusters.items():
        numbers = sorted({number_of[d] for d in cluster.doc_ids if d in number_of})
        if numbers:
            mapping[cluster_id] = numbers
    return mapping


def _cite(sentence: ReportSentence, numbers: dict[str, list[int]]) -> str:
    refs = sorted(
        {n for c in [*sentence.cluster_ids, *sentence.finding_refs] for n in numbers.get(c, [])}
    )
    return "".join(f"[{n}]" for n in refs)


def _decorate(sentence: ReportSentence, language: str, numbers: dict[str, list[int]]) -> str:
    text = sentence.text.strip()
    labels = [
        LABELS.get(language, LABELS["en"])[label]
        for label in sentence.labels
        if label in LABELS["en"]
    ]
    suffix = f" _({', '.join(labels)})_" if labels else ""
    cite = _cite(sentence, numbers)
    return f"{text}{suffix}{' ' + cite if cite else ''}"


def render_markdown(state: ResearchState, *, metadata: dict[str, Any]) -> str:
    report = state.report
    assert report is not None
    language = report.language
    words = _words(language)
    numbers = citation_numbers(report, state)
    gate = state.gate_result or {}
    lines = [f"# {report.title}", ""]
    if gate.get("banner"):
        failed = [
            item["message"]
            for check in gate.get("checks", {}).values()
            for item in check.get("remaining", [])
            if item.get("severity") == "error"
        ]
        lines += [f"> **{gate['banner']}**"] + [f"> - {m}" for m in failed[:10]] + [""]

    for section in report.sections:
        if section.key is SectionKey.SOURCES:
            continue
        if section.key is SectionKey.KNOWN_GAPS and not section.sentences:
            continue
        lines += [f"## {section.title}", ""]
        if not section.sentences:
            lines += [f"_{words['none_found']}_", ""]
            continue
        if section.key in (SectionKey.KEY_FINDINGS, SectionKey.CONFLICTING, SectionKey.KNOWN_GAPS):
            for sentence in section.sentences:
                lines.append(f"- {_decorate(sentence, language, numbers)}")
        elif section.key is SectionKey.RECOMMENDATIONS:
            for index, sentence in enumerate(section.sentences, start=1):
                lines.append(f"{index}. {_decorate(sentence, language, numbers)}")
        else:
            prose = [s for s in section.sentences if "gate_note" not in s.labels]
            notes = [s for s in section.sentences if "gate_note" in s.labels]
            if prose:
                lines.append(" ".join(_decorate(s, language, numbers) for s in prose))
            for note in notes:
                lines += ["", f"> _{note.text}_"]
        lines.append("")

    lines += [f"## {section_title_for(report, SectionKey.SOURCES)}", ""]
    for source in report.sources:
        document = state.documents.get(source.doc_id)
        primary = f", {words['primary']}" if document and document.score.is_primary else ""
        dated = (
            f", {words['at']} {document.published_at.isoformat()}"
            if document and document.published_at
            else ""
        )
        lines.append(
            f"{source.number}. [{source.title or source.url}]({source.url}) - "
            f"{source.domain} ({words['score']} {source.score:.2f}{primary}{dated})"
        )
    if not report.sources:
        lines.append(f"_{words['none']}_")
    lines.append("")

    lines += [f"## {words['metadata']}", ""]
    for key, value in _metadata_rows(state, metadata, words):
        lines.append(f"- **{key}:** {value}")
    return "\n".join(lines).rstrip() + "\n"


def section_title_for(report: Report, key: SectionKey) -> str:
    section = report.section(key)
    if section is not None:
        return section.title
    from research_agent.gate.runner import section_title

    return section_title(key, report.language)


def _metadata_rows(
    state: ResearchState, metadata: dict[str, Any], words: dict[str, str]
) -> list[tuple[str, str]]:
    budget = state.budget
    gate = state.gate_result or {}
    status_word = {
        SubQuestionStatus.SUFFICIENT: words["answered"],
        SubQuestionStatus.EXHAUSTED: words["exhausted"],
    }
    subqs = ", ".join(f"{s.id} {status_word.get(s.status, words['open'])}" for s in state.plan)
    read = sum(1 for d in state.documents.values() if d.extracted)
    rows = [
        (
            words["stop"],
            f"`{state.stop_reason.value if state.stop_reason else '-'}` - {state.stop_detail}",
        ),
        (words["rounds"], str(state.iteration)),
        (words["searches"], str(budget.get("searches", 0))),
        (words["subqs"], subqs or words["none"]),
        (words["sources"], f"{read} / {len(state.documents)}"),
        (words["findings"], f"{len(state.clusters)} ({len(state.claims)} claims)"),
        (words["tokens"], f"{budget.get('tokens_in', 0)} / {budget.get('tokens_out', 0)}"),
        (words["cost"], f"${float(budget.get('cost_usd', 0)):.4f}"),
        (
            words["gate"],
            f"`{gate.get('verdict', '-')}` ({gate.get('removed_sentences', 0)} {words['removed']})",
        ),
        (words["skills"], ", ".join(state.skills) or words["none"]),
        (words["config"], f"`{str(metadata.get('config_hash', ''))[:16]}`"),
        (
            words["models"],
            ", ".join(f"{k}={v}" for k, v in sorted(metadata.get("models_used", {}).items()))
            or "-",
        ),
    ]
    prompts = metadata.get("prompt_versions") or {}
    if prompts:
        rows.append(
            (
                words["prompts"],
                ", ".join(
                    f"{name}@{ref.get('source')}:{ref.get('version')}"
                    for name, ref in sorted(prompts.items())
                ),
            )
        )
    return rows


def render_json(state: ResearchState, *, metadata: dict[str, Any]) -> dict[str, Any]:
    report = state.report
    assert report is not None
    numbers = citation_numbers(report, state)
    return {
        "question": state.question,
        "language": report.language,
        "title": report.title,
        "banner": (state.gate_result or {}).get("banner"),
        "sections": [
            {
                "key": section.key.value,
                "title": section.title,
                "sentences": [
                    {
                        "text": s.text,
                        "kind": s.kind.value,
                        "labels": s.labels,
                        "findings": s.cluster_ids or s.finding_refs,
                        "sources": sorted(
                            {
                                n
                                for c in [*s.cluster_ids, *s.finding_refs]
                                for n in numbers.get(c, [])
                            }
                        ),
                    }
                    for s in section.sentences
                ],
            }
            for section in report.sections
            if section.key is not SectionKey.SOURCES
        ],
        "sources": [source.model_dump(mode="json") for source in report.sources],
        "findings": {
            cid: {
                "statement": c.statement,
                "status": c.status.value,
                "support": c.support,
                "confidence": c.confidence,
                "sources": numbers.get(cid, []),
            }
            for cid, c in state.clusters.items()
        },
        "metadata": {
            **metadata,
            "stop_reason": state.stop_reason.value if state.stop_reason else None,
            "stop_detail": state.stop_detail,
            "iterations": state.iteration,
            "budget": state.budget,
            "gate_verdict": (state.gate_result or {}).get("verdict"),
            "counters": state.counters,
            "skills": state.skills,
        },
    }
