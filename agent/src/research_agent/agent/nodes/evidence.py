"""evaluate_sources, extract_claims, cluster_and_corroborate, detect_contradictions."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

from research_agent.agent.clustering import cluster_claims
from research_agent.agent.contradictions import contradiction_candidates
from research_agent.agent.deps import AgentDeps
from research_agent.agent.injection import looks_like_injection
from research_agent.agent.quotes import verify_quote
from research_agent.agent.runtime import EventSink
from research_agent.agent.scoring import score_source
from research_agent.agent.state import (
    Claim,
    ClaimKind,
    ClusterStatus,
    Contradiction,
    ContradictionKind,
    Document,
    ResearchState,
)
from research_agent.agent.text import overlap_ratio, truncate
from research_agent.errors import AgentError, ErrorCode
from research_agent.observability.events import EventType
from research_agent.prompting.predict import untrusted
from research_agent.prompting.schemas import (
    ContradictionJudgements,
    ExtractionOutput,
    SourceJudgements,
)
from research_agent.providers.llm.gateway import LLMFailure

EVALUATION_BATCH = 8
EVALUATION_EXCERPT = 700
EXTRACTION_CHARS = 14_000


def _selected(state: ResearchState) -> list[Document]:
    return sorted(
        (d for d in state.documents.values() if d.selected_round == state.iteration),
        key=lambda d: d.id,
    )


def _subquestion_brief(state: ResearchState, only_open: bool = True) -> list[dict[str, Any]]:
    return [
        {
            "id": subq.id,
            "question": subq.text,
            "facets": [{"id": f.id, "name": f.name, "status": f.status.value} for f in subq.facets],
        }
        for subq in state.plan
        if subq.open or not only_open
    ]


# --- evaluate_sources --------------------------------------------------------------------------


async def evaluate_sources(state: ResearchState, deps: AgentDeps, events: EventSink) -> None:
    documents = [d for d in _selected(state) if not d.score.llm_scored]
    if not documents:
        return
    semaphore = asyncio.Semaphore(deps.settings.concurrency.max_parallel_llm_calls)
    judgements: dict[str, Any] = {}

    async def judge(batch: Sequence[Document]) -> None:
        payload = "\n\n".join(
            untrusted(
                f"TITLE: {d.title}\nDATE: {d.published_at or 'unknown'}\n"
                f"{truncate(d.content or d.snippet, EVALUATION_EXCERPT)}",
                id=d.id,
                domain=d.domain,
            )
            for d in batch
        )
        async with semaphore:
            try:
                result = await deps.predictor(
                    "evaluate_sources",
                    events,
                    question=state.question,
                    subquestions=_subquestion_brief(state),
                    documents=payload,
                )
            except LLMFailure:
                return
        output: SourceJudgements = result.value
        wanted = {d.id for d in batch}
        for item in output.items:
            if item.doc_id in wanted:
                judgements[item.doc_id] = item

    batches = [
        documents[i : i + EVALUATION_BATCH] for i in range(0, len(documents), EVALUATION_BATCH)
    ]
    await asyncio.gather(*(judge(batch) for batch in batches))

    for doc in documents:
        subq_text = " ".join(s.text for sid in doc.subq_ids if (s := state.subquestion(sid)))
        rule_relevance = overlap_ratio(subq_text, f"{doc.title} {doc.snippet} {doc.content[:2000]}")
        item = judgements.get(doc.id)
        doc.score = score_source(
            domain=doc.domain,
            published_at=doc.published_at,
            as_of=state.as_of,
            scope=state.analysis.time_scope,
            relevance=item.relevance if item else rule_relevance,
            entities=state.analysis.entities,
            tiers=deps.tiers,
            settings=deps.settings.scoring,
            authority_adjustment=item.authority_adjustment if item else 0.0,
            llm_primary=item.is_primary if item else None,
            llm_scored=item is not None,
            reason=item.reason if item else "rule-based (model unavailable)",
        )
        await events.info(
            EventType.SOURCE_SCORED,
            doc.score.rationale,
            label="Evaluator",
            data={"doc_id": doc.id, "url": doc.url, "score": doc.score.model_dump(mode="json")},
        )

    primary = sum(1 for d in documents if d.score.is_primary)
    rule_only = sum(1 for d in documents if not d.score.llm_scored)
    await events.info(
        EventType.NODE_FINISHED,
        f"Scored {len(documents)} sources: {primary} primary"
        + (f", {rule_only} by rules only" if rule_only else "")
        + ".",
        label="Evaluator",
    )


# --- extract_claims ----------------------------------------------------------------------------


def _kind(value: str) -> ClaimKind:
    try:
        return ClaimKind(value)
    except ValueError:
        return ClaimKind.FACT


async def extract_claims(state: ResearchState, deps: AgentDeps, events: EventSink) -> None:
    documents = [
        d
        for d in _selected(state)
        if not d.extracted and d.score.relevance >= 0.15 and (d.content or d.snippet)
    ]
    if not documents:
        return
    semaphore = asyncio.Semaphore(deps.settings.concurrency.max_parallel_llm_calls)
    brief = _subquestion_brief(state)
    known_subqs = {s.id: s for s in state.plan}
    stats = {"claims": 0, "rejected": 0, "failed": 0, "injection": 0}

    async def extract(doc: Document) -> list[Claim]:
        text = doc.content or doc.snippet
        block = untrusted(
            f"TITLE: {doc.title}\nPUBLISHED: {doc.published_at or 'unknown'}\n\n"
            f"{truncate(text, EXTRACTION_CHARS)}",
            id=doc.id,
            url=doc.canonical_url,
        )
        async with semaphore:
            try:
                result = await deps.predictor(
                    "extract_claims",
                    events,
                    question=state.question,
                    subquestions={"report_language": state.language, "subquestions": brief},
                    document=block,
                    today=deps.today.isoformat(),
                )
            except LLMFailure:
                stats["failed"] += 1
                return []
        doc.extracted = True
        output: ExtractionOutput = result.value
        accepted: list[Claim] = []
        rejected = 0
        injected = 0
        for item in output.claims:
            if looks_like_injection(item.quote) or looks_like_injection(item.text):
                injected += 1
                continue
            check = verify_quote(item.quote, text)
            if not check.ok:
                rejected += 1
                continue
            subq_id = item.subq_id if item.subq_id in known_subqs else doc.subq_ids[0]
            subq = known_subqs[subq_id]
            facet_id = item.facet_id if any(f.id == item.facet_id for f in subq.facets) else None
            accepted.append(
                Claim(
                    id="",
                    subq_id=subq_id,
                    facet_id=facet_id,
                    doc_id=doc.id,
                    origin_id=doc.origin_id,
                    text=item.text.strip(),
                    quote=item.quote.strip(),
                    kind=_kind(item.kind),
                    entity=item.entity,
                    attribute=item.attribute,
                    value=item.value,
                    unit=item.unit,
                    as_of=item.as_of,
                    attributed_to=item.attributed_to,
                    iteration=state.iteration,
                )
            )
        if injected:
            stats["injection"] += injected
            await events.warn(
                EventType.CLAIM_EXTRACTED,
                f"{doc.domain}: {injected} sentence(s) addressed to the model were not admitted "
                "as evidence.",
                label="Extractor",
                data={"doc_id": doc.id, "injection_suspected": injected},
            )
        if rejected:
            stats["rejected"] += rejected
            await events.error(
                AgentError(
                    code=ErrorCode.EXTRACT_QUOTE_NOT_FOUND,
                    node=events.node,
                    decision=f"discard {rejected} claim(s)",
                    outcome=f"{doc.domain}: quote not found in the page text",
                )
            )
        return accepted

    results = await asyncio.gather(*(extract(doc) for doc in documents))
    for claims in results:
        for claim in claims:
            claim.id = f"c{len(state.claims) + 1}"
            state.claims[claim.id] = claim
            stats["claims"] += 1
    state.bump("claims_rejected_quote", stats["rejected"])
    state.bump("extraction_failures", stats["failed"])
    state.bump("claims_rejected_injection", stats["injection"])
    await events.info(
        EventType.CLAIM_EXTRACTED,
        f"Extracted {stats['claims']} claims from {len(documents)} sources"
        + (f" ({stats['rejected']} rejected: quote not found)" if stats["rejected"] else "")
        + ".",
        label="Extractor",
        data=stats,
    )


# --- cluster_and_corroborate -------------------------------------------------------------------


async def cluster_and_corroborate(state: ResearchState, deps: AgentDeps, events: EventSink) -> None:
    clustered = {cid for cluster in state.clusters.values() for cid in cluster.claim_ids}
    new_claims = [c for c in state.claims.values() if c.id not in clustered]
    if not new_claims:
        return
    representatives = [
        state.claims[c.claim_ids[0]]
        for c in state.clusters.values()
        if c.claim_ids and c.claim_ids[0] in state.claims
    ]
    texts = [claim.text for claim in [*new_claims, *representatives]]
    by_text = await deps.embedder.embed(texts, events)
    vectors = None
    if by_text is not None:
        vectors = {
            claim.id: by_text[claim.text]
            for claim in [*new_claims, *representatives]
            if claim.text in by_text
        }

    before = len(state.clusters)
    state.clusters = cluster_claims(
        state.clusters,
        new_claims,
        vectors=vectors,
        documents=state.documents,
        settings=deps.settings.dedup,
        all_claims=state.claims,
    )
    corroborated = sum(1 for c in state.clusters.values() if c.support >= 2)
    await events.info(
        EventType.NODE_FINISHED,
        f"{len(new_claims)} new claims -> {len(state.clusters) - before} new findings; "
        f"{corroborated} of {len(state.clusters)} findings have 2+ independent sources.",
        label="Ledger",
        data={
            "similarity": "embedding" if vectors is not None else "lexical",
            "model": deps.embedder.model,
        },
    )


# --- detect_contradictions ---------------------------------------------------------------------


async def detect_contradictions(state: ResearchState, deps: AgentDeps, events: EventSink) -> None:
    judged = {frozenset(c.cluster_ids) for c in state.contradictions}
    pairs = contradiction_candidates(
        state.clusters, deps.settings.contradiction, language=state.language, already_judged=judged
    )
    if not pairs:
        return
    payload = []
    for index, (left_id, right_id) in enumerate(pairs, start=1):
        left, right = state.clusters[left_id], state.clusters[right_id]
        payload.append(
            {
                "pair_id": f"p{index}",
                "left": {
                    "id": left.id,
                    "statement": left.statement,
                    "value": left.value,
                    "as_of": left.as_of,
                    "support": left.support,
                    "primary": left.has_primary,
                    "best_source_score": left.best_source_score,
                },
                "right": {
                    "id": right.id,
                    "statement": right.statement,
                    "value": right.value,
                    "as_of": right.as_of,
                    "support": right.support,
                    "primary": right.has_primary,
                    "best_source_score": right.best_source_score,
                },
            }
        )

    verdicts: dict[str, Any] = {}
    try:
        result = await deps.predictor(
            "judge_contradictions",
            events,
            question=state.question,
            language=state.language,
            pairs=payload,
        )
        output: ContradictionJudgements = result.value
        verdicts = {item.pair_id: item for item in output.items}
    except LLMFailure:
        pass  # conservative: every candidate is treated as a real conflict

    for index, (left_id, right_id) in enumerate(pairs, start=1):
        left, right = state.clusters[left_id], state.clusters[right_id]
        verdict = verdicts.get(f"p{index}")
        kind = ContradictionKind(verdict.kind) if verdict else ContradictionKind.TRUE_CONFLICT
        summary = verdict.summary if verdict else f"{left.statement} <> {right.statement}"
        contradiction = Contradiction(
            id=f"x{len(state.contradictions) + 1}",
            cluster_ids=[left_id, right_id],
            subq_id=left.subq_id,
            kind=kind,
            summary=summary,
            rationale=verdict.rationale if verdict else "no judge available",
            iteration=state.iteration,
        )
        if kind is not ContradictionKind.TRUE_CONFLICT:
            contradiction.resolved = True  # both can be true; nothing to chase
        else:
            left.status = right.status = ClusterStatus.CONTESTED
            preferred = verdict.preferred if verdict else "none"
            chosen = {"left": left, "right": right}.get(preferred)
            other = right if chosen is left else left
            # The judge's preference counts only if the ledger backs it: a primary source that
            # outranks the other side (v0.6 §10, step 3). The conflict is still reported.
            if (
                chosen is not None
                and chosen.has_primary
                and (chosen.best_source_score >= other.best_source_score)
            ):
                contradiction.preferred_cluster_id = chosen.id
                contradiction.resolved = True
        state.contradictions.append(contradiction)
        await events.warn(
            EventType.CONTRADICTION_FOUND,
            f"{kind.value.replace('_', ' ')}: {summary}",
            label="Contradiction",
            data=contradiction.model_dump(mode="json"),
        )
