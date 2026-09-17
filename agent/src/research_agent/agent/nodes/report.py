"""synthesize, verify_citations, output_gate, render_report."""

from __future__ import annotations

import asyncio
import re
from collections import Counter
from typing import Any

from research_agent.agent.coverage import localised_reason
from research_agent.agent.deps import AgentDeps
from research_agent.agent.freshness import source_context
from research_agent.agent.runtime import EventSink
from research_agent.agent.state import (
    ClusterStatus,
    ContradictionKind,
    Report,
    ReportSection,
    ReportSentence,
    ResearchState,
    SectionKey,
    SentenceKind,
    SubQuestionStatus,
)
from research_agent.errors import AgentError, ErrorCode
from research_agent.gate import run_gate
from research_agent.gate.rules import (
    GAP_LABEL_PREFIX,
    LABEL_SINGLE_SOURCE,
    LABEL_UNCERTAIN,
)
from research_agent.gate.runner import rebuild_sources, section_title
from research_agent.observability.events import EventType
from research_agent.prompting.schemas import SentenceOut, SynthesisOutput, VerificationOutput
from research_agent.providers.llm.gateway import LLMFailure

MAX_LEDGER_FINDINGS = 80
_FRAMING = re.compile(
    r"\b(this report|this section|the findings below|in summary|overall|bu rapor|bu bölüm|"
    r"özetle|genel olarak|aşağıda)\b",
    re.IGNORECASE,
)
_GAP_REASON = {
    "en": {
        "open": "not enough independent or primary sources were found",
        "exhausted": "searches stopped producing new information",
    },
    "tr": {
        "open": "yeterli bağımsız ya da birincil kaynak bulunamadı",
        "exhausted": "aramalar yeni bilgi üretmeyi bıraktı",
    },
}


def _ledger(state: ResearchState) -> list[dict[str, Any]]:
    clusters = sorted(
        (c for c in state.clusters.values() if not c.requires_fresh_confirmation),
        key=lambda c: (c.subq_id, -c.confidence, c.id),
    )[:MAX_LEDGER_FINDINGS]
    rows = []
    for cluster in clusters:
        domains = sorted(
            {state.documents[d].domain for d in cluster.doc_ids if d in state.documents}
        )
        rows.append(
            {
                "id": cluster.id,
                "subquestion": cluster.subq_id,
                "statement": cluster.statement,
                "value": cluster.value,
                "as_of": cluster.as_of,
                "conditions": cluster.conditions,
                "effective_from": cluster.effective_from,
                "effective_until": cluster.effective_until,
                "requires_fresh_confirmation": cluster.requires_fresh_confirmation,
                "status": cluster.status.value,
                "independent_sources": cluster.support,
                "has_primary_source": cluster.has_primary,
                "confidence": cluster.confidence,
                "source_domains": domains,
                "latest_source_date": latest.isoformat()
                if (latest := state.latest_source_date(cluster))
                else None,
            }
        )
    return rows


def _gaps(state: ResearchState) -> list[tuple[str, str, str]]:
    """(subq id, question, reason) for everything not answered when the run stopped."""
    reasons = _GAP_REASON.get(state.language, _GAP_REASON["en"])
    gaps = []
    for subq in state.plan:
        if subq.status is SubQuestionStatus.SUFFICIENT:
            continue
        if subq.status is SubQuestionStatus.EXHAUSTED:
            reason = localised_reason(subq.exhausted_reason, state.language) or reasons["exhausted"]
        else:
            missing = ", ".join(f.name for f in subq.missing_facets())
            reason = reasons["open"] + (f" ({missing})" if missing else "")
        gaps.append((subq.id, subq.text, reason))
    return gaps


def _is_framing(text: str) -> bool:
    return not re.search(r"\d", text) and bool(_FRAMING.search(text)) and len(text.split()) <= 30


def _sentence(item: SentenceOut, known: set[str]) -> ReportSentence:
    ids = [cid for cid in dict.fromkeys(item.cluster_ids) if cid in known or cid.startswith("k")]
    if not ids and _is_framing(item.text):
        return ReportSentence(text=item.text.strip(), kind=SentenceKind.META)
    # Uncited non-framing sentences stay FACT so that the gate (G2) removes and records them.
    return ReportSentence(text=item.text.strip(), kind=SentenceKind.FACT, cluster_ids=ids)


def _label(sentence: ReportSentence, state: ResearchState, section: SectionKey) -> None:
    """Deterministic labels from the ledger; the gate re-checks them (G5, G6)."""
    clusters = [state.clusters[c] for c in sentence.cluster_ids if c in state.clusters]
    if not clusters:
        return
    if (
        any(c.status is ClusterStatus.CONTESTED for c in clusters)
        and section is not SectionKey.CONFLICTING
    ):
        sentence.labels.append(LABEL_UNCERTAIN)
    if all(c.status is ClusterStatus.SINGLE_SOURCE for c in clusters):
        sentence.labels.append(LABEL_SINGLE_SOURCE)


def build_report(output: SynthesisOutput, state: ResearchState) -> Report:
    known = set(state.clusters)
    language = state.language

    def section(key: SectionKey, items: list[SentenceOut]) -> ReportSection:
        sentences = [_sentence(item, known) for item in items if item.text.strip()]
        for sentence in sentences:
            _label(sentence, state, key)
        return ReportSection(key=key, title=section_title(key, language), sentences=sentences)

    sections = [
        section(SectionKey.SUMMARY, output.summary),
        section(SectionKey.KEY_FINDINGS, output.key_findings),
        section(SectionKey.CONFLICTING, output.conflicting),
    ]
    wants_actions = state.analysis.answer_type == "action_plan"
    if output.recommendations and wants_actions:
        sections.append(
            ReportSection(
                key=SectionKey.RECOMMENDATIONS,
                title=section_title(SectionKey.RECOMMENDATIONS, language),
                sentences=[
                    ReportSentence(
                        text=r.text.strip(),
                        kind=SentenceKind.RECOMMENDATION,
                        finding_refs=[f for f in r.finding_refs if f in known],
                    )
                    for r in output.recommendations
                    if r.text.strip()
                ],
            )
        )
    sections.append(section(SectionKey.CONCLUSION, output.conclusion))
    sections.append(_gap_section(state))
    sections.append(
        ReportSection(key=SectionKey.SOURCES, title=section_title(SectionKey.SOURCES, language))
    )
    return Report(
        title=output.title.strip() or state.question,
        language=language,
        sections=[s for s in sections if s is not None],
    )


def _gap_section(state: ResearchState) -> ReportSection:
    sentences = [
        ReportSentence(
            text=f"{question} - {reason}",
            kind=SentenceKind.META,
            labels=[f"{GAP_LABEL_PREFIX}{subq_id}"],
        )
        for subq_id, question, reason in _gaps(state)
    ]
    withheld = sum(
        1
        for c in state.claims.values()
        if c.requires_fresh_confirmation or c.validation_status in {"unsupported", "unavailable"}
    )
    if withheld:
        note = (
            f"{withheld} candidate claim(s) withheld: source support, conditions or current "
            "applicability could not be confirmed; see the evidence ledger."
        )
        if state.language == "tr":
            note = (
                f"{withheld} aday iddia kullanılmadı: kaynak desteği, koşulları veya güncel "
                "geçerliliği doğrulanamadı; ayrıntılar kanıt defterindedir."
            )
        sentences.append(ReportSentence(text=note, kind=SentenceKind.META))
    return ReportSection(
        key=SectionKey.KNOWN_GAPS,
        title=section_title(SectionKey.KNOWN_GAPS, state.language),
        sentences=sentences,
    )


def fallback_report(state: ResearchState) -> Report:
    """No model: one sentence per finding, straight from the ledger. Plain but true."""
    language = state.language
    clusters = sorted(
        (c for c in state.clusters.values() if not c.requires_fresh_confirmation),
        key=lambda c: (-c.confidence, c.id),
    )
    contested = [c for c in clusters if c.status is ClusterStatus.CONTESTED]
    settled = [c for c in clusters if c.status is not ClusterStatus.CONTESTED]

    def fact(cluster_id: str, text: str, key: SectionKey) -> ReportSentence:
        sentence = ReportSentence(text=text, cluster_ids=[cluster_id])
        _label(sentence, state, key)
        return sentence

    framing = (
        {
            "en": "The findings above are listed directly from the evidence ledger.",
            "tr": "Yukarıdaki bulgular doğrudan kanıt defterinden listelenmiştir.",
        }
        if clusters
        else {
            "en": "Not enough evidence was found; no answer is given rather than an invented one.",
            "tr": "Yeterli kanıt bulunamadı; uydurma bir cevap yerine cevap verilmedi.",
        }
    )
    empty = {"en": "No supported finding was found.", "tr": "Desteklenen bir bulgu bulunamadı."}
    summary = [fact(c.id, c.statement, SectionKey.SUMMARY) for c in settled[:2]] or [
        ReportSentence(text=empty.get(language, empty["en"]), kind=SentenceKind.META)
    ]
    return Report(
        title=state.question,
        language=language,
        fallback=True,
        sections=[
            ReportSection(
                key=SectionKey.SUMMARY,
                title=section_title(SectionKey.SUMMARY, language),
                sentences=summary,
            ),
            ReportSection(
                key=SectionKey.KEY_FINDINGS,
                title=section_title(SectionKey.KEY_FINDINGS, language),
                sentences=[fact(c.id, c.statement, SectionKey.KEY_FINDINGS) for c in settled[:30]],
            ),
            ReportSection(
                key=SectionKey.CONFLICTING,
                title=section_title(SectionKey.CONFLICTING, language),
                sentences=[fact(c.id, c.statement, SectionKey.CONFLICTING) for c in contested],
            ),
            ReportSection(
                key=SectionKey.CONCLUSION,
                title=section_title(SectionKey.CONCLUSION, language),
                sentences=[
                    ReportSentence(
                        text=framing.get(language, framing["en"]), kind=SentenceKind.META
                    )
                ],
            ),
            _gap_section(state),
            ReportSection(
                key=SectionKey.SOURCES, title=section_title(SectionKey.SOURCES, language)
            ),
        ],
    )


async def _synthesise(
    state: ResearchState, deps: AgentDeps, events: EventSink, feedback: str
) -> Report:
    contradictions = [
        {
            "clusters": c.cluster_ids,
            "kind": c.kind.value,
            "summary": c.summary,
            "preferred": c.preferred_cluster_id,
            "resolved": c.resolved,
        }
        for c in state.contradictions
        if c.kind is not ContradictionKind.CONSISTENT
    ]
    try:
        result = await deps.predictor(
            "synthesize",
            events,
            question=state.question,
            language=state.language,
            today=deps.today.isoformat(),
            ledger=_ledger(state),
            gaps=[{"id": g[0], "question": g[1]} for g in _gaps(state)],
            contradictions=contradictions or "(none)",
            answer_type=state.analysis.answer_type,
            feedback=feedback or "(none)",
        )
    except LLMFailure:
        await events.warn(
            EventType.SYNTHESIS_DONE,
            "The model was unavailable; the report lists the ledger directly.",
            label="Synthesizer",
        )
        return fallback_report(state)
    await events.info(
        EventType.DECISION, result.value.rationale or "Report drafted.", label="Synthesizer"
    )
    return build_report(result.value, state)


async def synthesize(state: ResearchState, deps: AgentDeps, events: EventSink) -> None:
    if not state.clusters:
        state.report = fallback_report(state)
    else:
        state.report = await _synthesise(state, deps, events, feedback="")
    rebuild_sources(state.report, state)
    counts = {s.key.value: len(s.sentences) for s in state.report.sections}
    await events.info(
        EventType.SYNTHESIS_DONE,
        f"Drafted the report: {sum(counts.values())} sentences.",
        label="Synthesizer",
        data={"sections": counts, "fallback": state.report.fallback},
    )


# --- verify_citations --------------------------------------------------------------------------


async def _verify(
    state: ResearchState, deps: AgentDeps, events: EventSink, report: Report
) -> dict[str, str]:
    """Unsupported sentence ids -> reason. Batched and run in parallel (D34)."""
    items = []
    for section in report.sections:
        for index, sentence in enumerate(section.sentences):
            if sentence.kind in {SentenceKind.FACT, SentenceKind.RECOMMENDATION} and (
                sentence.cluster_ids or sentence.finding_refs
            ):
                items.append(
                    {
                        "sentence_id": f"{section.key.value}:{index}",
                        "sentence": sentence.text,
                        "kind": sentence.kind.value,
                        "question": state.question,
                        "as_of": state.as_of.isoformat(),
                        "evidence": [
                            {
                                "cluster_id": cid,
                                "claim": claim.text,
                                "quote": claim.quote,
                                "conditions": claim.conditions,
                                "effective_from": claim.effective_from,
                                "effective_until": claim.effective_until,
                                "as_of": claim.as_of,
                                "freshness": claim.freshness,
                                "requires_fresh_confirmation": claim.requires_fresh_confirmation,
                                "validation_status": claim.validation_status,
                                "source_date": str(doc.published_at) if doc.published_at else None,
                                "source_url": doc.canonical_url,
                                "source_context": source_context(doc, claim.quote),
                            }
                            for cid in (sentence.cluster_ids or sentence.finding_refs)
                            if cid in state.clusters
                            for claim_id in state.clusters[cid].claim_ids
                            if (claim := state.claims.get(claim_id)) is not None
                            if (doc := state.documents.get(claim.doc_id)) is not None
                        ],
                        "cited_findings": [
                            state.clusters[c].statement
                            for c in (sentence.cluster_ids or sentence.finding_refs)
                            if c in state.clusters
                        ],
                    }
                )
    if not items:
        state.verification = {"requested": 0, "checked": 0, "unsupported": 0, "unavailable": 0}
        return {}
    size = deps.settings.concurrency.citation_verification_batch_size
    batches = [items[i : i + size] for i in range(0, len(items), size)]
    semaphore = asyncio.Semaphore(3)
    unsupported: dict[str, str] = {}
    checked: set[str] = set()
    unavailable: set[str] = set()

    async def check(batch: list[dict[str, Any]]) -> None:
        pending = {item["sentence_id"]: item for item in batch}
        async with semaphore:
            for _ in range(2):
                try:
                    result = await deps.predictor(
                        "verify_citations", events, sentences=list(pending.values())
                    )
                except LLMFailure:
                    continue
                output: VerificationOutput = result.value
                counts = Counter(verdict.sentence_id for verdict in output.items)
                for verdict in output.items:
                    sid = verdict.sentence_id
                    if sid not in pending or counts[sid] != 1:
                        continue
                    checked.add(sid)
                    del pending[sid]
                    if not verdict.supported:
                        unsupported[sid] = verdict.reason
                if not pending:
                    break
        for sid in pending:
            unavailable.add(sid)
            unsupported[sid] = "Citation verification unavailable after two attempts."

    await asyncio.gather(*(check(batch) for batch in batches))
    state.verification = {
        "requested": len(items),
        "checked": len(checked),
        "unsupported": len(unsupported) - len(unavailable),
        "unavailable": len(unavailable),
    }
    if unavailable:
        await events.warn(
            EventType.DECISION,
            f"Citation verification unavailable for {len(unavailable)} sentence(s); "
            "withholding them.",
            label="Verifier",
            data={"unavailable": sorted(unavailable)},
        )
    return unsupported


def _drop(report: Report, unsupported: dict[str, str]) -> int:
    dropped = 0
    for section in report.sections:
        kept = []
        for index, sentence in enumerate(section.sentences):
            if f"{section.key.value}:{index}" in unsupported:
                dropped += 1
            else:
                kept.append(sentence)
        section.sentences = kept
    return dropped


async def verify_citations(state: ResearchState, deps: AgentDeps, events: EventSink) -> None:
    if state.report is None or state.report.fallback:
        return
    unsupported = await _verify(state, deps, events, state.report)
    if unsupported and not state.resynthesized and not state.verification.get("unavailable"):
        state.resynthesized = True
        feedback = "\n".join(
            f'- Unsupported: "{_text(state.report, sid)}" - {reason}'
            for sid, reason in unsupported.items()
        )
        await events.warn(
            EventType.DECISION,
            f"{len(unsupported)} sentence(s) not entailed by their citations; "
            "rewriting once with feedback.",
            label="Verifier",
            data={"unsupported": unsupported},
        )
        state.report = await _synthesise(state, deps, events, feedback=feedback)
        if not state.report.fallback:
            unsupported = await _verify(state, deps, events, state.report)
        else:
            # The old positional IDs refer to a different report, not the fallback's rows.
            unsupported = {}
    dropped = _drop(state.report, unsupported) if unsupported else 0
    if state.verification.get("unavailable"):
        note = (
            f"Atıf doğrulaması tamamlanamadığı için {dropped} cümle çıkarıldı."
            if state.language == "tr"
            else f"{dropped} sentence(s) withheld because citation verification was unavailable."
        )
        gaps = state.report.section(SectionKey.KNOWN_GAPS)
        if gaps is None:
            gaps = ReportSection(
                key=SectionKey.KNOWN_GAPS,
                title=section_title(SectionKey.KNOWN_GAPS, state.language),
            )
            state.report.sections.append(gaps)
        gaps.sentences.append(ReportSentence(text=note, kind=SentenceKind.META))
    rebuild_sources(state.report, state)
    state.bump("sentences_dropped_by_verifier", dropped)
    await events.info(
        EventType.NODE_FINISHED,
        f"Checked {state.verification.get('checked', 0)} cited sentences; "
        f"{dropped} removed as unsupported.",
        label="Verifier",
        data={"dropped": dropped, "rewritten": state.resynthesized},
    )


def _text(report: Report, sentence_id: str) -> str:
    key, _, index = sentence_id.partition(":")
    section = report.section(SectionKey(key))
    if section is None or int(index) >= len(section.sentences):
        return ""
    return section.sentences[int(index)].text


# --- output_gate -------------------------------------------------------------------------------


async def output_gate(state: ResearchState, deps: AgentDeps, events: EventSink) -> None:
    assert state.report is not None
    outcome = run_gate(
        state.report, state, deps.gate, max_rounds=deps.settings.gate.max_remediation_rounds
    )
    state.report = outcome.report
    state.gate_result = outcome.to_dict()
    checks = {rule: result["status"] for rule, result in outcome.checks().items()}
    if outcome.remediations:
        await events.error(
            AgentError(
                code=ErrorCode.GATE_REMEDIATED,
                node=events.node,
                decision=f"{len(outcome.remediations)} deterministic fix(es)",
                outcome=f"{outcome.removed_sentences} sentence(s) removed",
            )
        )
    if outcome.verdict == "fail":
        await events.error(
            AgentError(
                code=ErrorCode.GATE_FAILED,
                node=events.node,
                decision="deliver the report with a warning banner",
                outcome="; ".join(v.message for v in outcome.final if v.severity == "error")[:300],
            )
        )
    await events.info(
        EventType.GATE_RESULT,
        f"Gate verdict: {outcome.verdict} ({outcome.removed_sentences} removed).",
        label="Gate",
        data={
            "verdict": outcome.verdict,
            "checks": checks,
            "removed_sentences": outcome.removed_sentences,
        },
    )


def route_after_synthesis(state: ResearchState) -> str:
    return "output_gate"
