"""generate_queries, search, process_results - one research round's intake of evidence."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

from research_agent.agent.coverage import REPEATED_QUERIES_REASON, progress_snapshot
from research_agent.agent.dedup import OriginIndex, QueryDeduplicator, canonicalize_url, domain_of
from research_agent.agent.dedup.minhash import content_hash
from research_agent.agent.deps import AgentDeps
from research_agent.agent.freshness import dated_header
from research_agent.agent.runtime import EventSink
from research_agent.agent.scoring import is_own_domain, recency_score, self_primary_entities
from research_agent.agent.state import (
    Document,
    PendingHit,
    QueryPurpose,
    QueryRecord,
    QueryStatus,
    ResearchState,
    SubQuestion,
    SubQuestionStatus,
)
from research_agent.agent.text import detect_language, overlap_ratio, truncate
from research_agent.errors import AgentError, ErrorCode
from research_agent.observability.events import EventType
from research_agent.prompting.schemas import QueriesOutput
from research_agent.providers.llm.gateway import LLMFailure
from research_agent.providers.search.base import SearchHit, SearchRequest

MAX_DOCUMENT_CHARS = 60_000


# --- generate_queries --------------------------------------------------------------------------


@dataclass
class Target:
    subq: SubQuestion
    quota: int
    purpose: QueryPurpose
    facet_id: str | None = None
    facet_name: str | None = None
    contradiction_id: str | None = None
    detail: str = ""

    def describe(self) -> dict[str, object]:
        return {
            "subq_id": self.subq.id,
            "sub_question": self.subq.text,
            "facet_id": self.facet_id,
            "facet": self.facet_name,
            "purpose": self.purpose.value,
            "detail": self.detail,
            "max_queries": self.quota,
        }


def plan_targets(state: ResearchState, deps: AgentDeps) -> list[Target]:
    """Round width is the real budget control (v0.6 §7): wide first, then only the gaps."""
    budget = deps.settings.budget
    left = deps.meter.searches_left()
    open_subqs = state.open_subquestions()
    if not open_subqs or left <= 0:
        return []

    if state.iteration == 1:
        per = max(1, min(budget.queries_per_subquestion_first_round, left // len(open_subqs)))
        return [Target(subq, per, QueryPurpose.INITIAL) for subq in open_subqs]

    targets: list[Target] = []
    cap = min(budget.max_queries_per_followup_round, left)
    for contradiction in state.contradictions:
        if contradiction.resolved or contradiction.followup_attempted:
            continue
        subq = state.subquestion(contradiction.subq_id)
        if subq is None or not subq.open:
            continue
        targets.append(
            Target(
                subq,
                1,
                QueryPurpose.CONFLICT,
                contradiction_id=contradiction.id,
                detail=contradiction.summary,
            )
        )
    for subq in open_subqs:
        for facet in subq.missing_facets():
            targets.append(
                Target(
                    subq,
                    budget.queries_per_gap_followup_round,
                    QueryPurpose.GAP,
                    facet_id=facet.id,
                    facet_name=facet.name,
                    detail=facet.description + _freshness_gap(state, subq.id, facet.id),
                )
            )
    trimmed: list[Target] = []
    total = 0
    for target in targets:
        if total >= cap:
            break
        target.quota = min(target.quota, cap - total)
        trimmed.append(target)
        total += target.quota
    return trimmed


def _freshness_gap(state: ResearchState, subq_id: str, facet_id: str) -> str:
    authority_gaps = [
        c
        for c in state.claims.values()
        if c.subq_id == subq_id
        and c.facet_id == facet_id
        and c.validation_reason.startswith("Legal authority:")
    ]
    authority_hint = (
        ". Find the operative regulation, article or detailed regulator guidance, including "
        "scope and exceptions. Do not use law-firm commentary, press releases or overview "
        "timelines as the final evidence of a legal obligation. Candidates needing primary "
        "confirmation: " + "; ".join(c.text for c in authority_gaps[:3])
        if authority_gaps
        else ""
    )
    held = [
        c
        for c in state.claims.values()
        if c.subq_id == subq_id and c.facet_id == facet_id and c.requires_fresh_confirmation
    ]
    if not held:
        return authority_hint
    values = "; ".join(
        f"{c.entity or ''} {c.attribute or ''}: {c.value or c.text} ({c.as_of or 'undated'})"
        for c in held[:3]
    )
    return authority_hint + (
        f". Fresh official confirmation needed as of {state.as_of}: {values}. "
        "Search for amendments, replacement thresholds and current consolidated guidance; "
        "do not repeat the old source as proof of the current rule."
    )


def _fallback_queries(target: Target, state: ResearchState) -> list[str]:
    year = str(state.analysis.time_scope.start.year) if state.analysis.time_scope.start else ""
    base = target.subq.text
    options = {
        QueryPurpose.INITIAL: [
            base,
            f"{base} {year}".strip(),
            f"{base} {'resmi kaynak' if state.language == 'tr' else 'official'}",
        ],
        QueryPurpose.GAP: [
            f"{base} {target.facet_name or ''}".strip(),
            f"{target.facet_name or ''} {' '.join(state.analysis.entities[:2])}".strip(),
        ],
        QueryPurpose.CONFLICT: [
            f"{target.detail} {'resmi' if state.language == 'tr' else 'official source'}"
        ],
    }
    return [re.sub(r"[?!]", "", option) for option in options.get(target.purpose, [base])]


async def generate_queries(state: ResearchState, deps: AgentDeps, events: EventSink) -> None:
    state.iteration += 1
    deps.meter.iteration = state.iteration
    events.iteration = state.iteration  # this node opens the round it belongs to
    state.round_snapshot = {k: list(v) for k, v in progress_snapshot(state).items()}
    state.pending_hits = []
    await events.info(
        EventType.ITERATION_STARTED,
        f"Round {state.iteration} started.",
        label="Research",
        iteration=state.iteration,
        data={"budget": deps.meter.snapshot()},
    )

    targets = plan_targets(state, deps)
    if not targets:
        await events.info(
            EventType.QUERIES_GENERATED, "No open targets for this round.", label="Query"
        )
        return

    dedup = QueryDeduplicator(
        threshold=deps.settings.dedup.query_similarity_threshold,
        seen=[query.text for query in state.queries],
    )
    limit = sum(target.quota for target in targets)
    by_key = {(t.subq.id, t.facet_id, t.contradiction_id): t for t in targets}
    admitted: dict[tuple[str, str | None, str | None], list[QueryRecord]] = {}
    rationale = ""

    def admit(target: Target, text: str, reason: str) -> None:
        key = (target.subq.id, target.facet_id, target.contradiction_id)
        taken = admitted.setdefault(key, [])
        if len(taken) >= target.quota or sum(len(v) for v in admitted.values()) >= limit:
            return
        cleaned = " ".join(text.split())[:200]
        if len(cleaned) < 3 or not dedup.admit(cleaned):
            return
        record = QueryRecord(
            id=f"q{len(state.queries) + 1}",
            text=cleaned,
            subq_id=target.subq.id,
            facet_id=target.facet_id,
            purpose=target.purpose,
            rationale=reason[:200],
            iteration=state.iteration,
        )
        state.queries.append(record)
        taken.append(record)

    try:
        result = await deps.predictor(
            "generate_queries",
            events,
            question=state.question,
            language=state.language,
            today=deps.today.isoformat(),
            round=state.iteration,
            targets=[target.describe() for target in targets],
            tried_queries=[query.text for query in state.queries][-60:] or ["(none)"],
            limit=limit,
        )
        output: QueriesOutput = result.value
        rationale = output.rationale
        for query in output.queries:
            target = by_key.get((query.subq_id, query.facet_id, None))
            if target is None:
                # The model may omit the facet or mislabel a conflict target; match loosely.
                target = next((t for t in targets if t.subq.id == query.subq_id), None)
            if target is not None:
                admit(target, query.text, query.rationale)
    except LLMFailure:
        rationale = "Template queries (the model was unavailable)."

    # Top up any target the model left short, with deterministic templates.
    for target in targets:
        key = (target.subq.id, target.facet_id, target.contradiction_id)
        if len(admitted.get(key, [])) < min(1, target.quota):
            for text in _fallback_queries(target, state):
                admit(target, text, "template")

    for target in targets:
        key = (target.subq.id, target.facet_id, target.contradiction_id)
        queries = admitted.get(key, [])
        if queries:
            target.subq.status = SubQuestionStatus.SEARCHING
            target.subq.queries_tried.extend(q.text for q in queries)
            if target.contradiction_id:
                for contradiction in state.contradictions:
                    if contradiction.id == target.contradiction_id:
                        contradiction.followup_attempted = True
        elif state.iteration > 1 and target.purpose is QueryPurpose.GAP:
            # Query exhaustion (v0.6 §7 rule 4): nothing new left to ask for this sub-question.
            if not any(
                admitted.get((target.subq.id, f, None))
                for f in [facet.id for facet in target.subq.facets]
            ):
                target.subq.status = SubQuestionStatus.EXHAUSTED
                target.subq.exhausted_reason = REPEATED_QUERIES_REASON
                await events.error(
                    AgentError(
                        code=ErrorCode.DUPLICATE_QUERY,
                        node=events.node,
                        decision=f"mark {target.subq.id} exhausted",
                        outcome="no new query for its missing facets",
                    )
                )

    planned = [q for q in state.queries if q.iteration == state.iteration]
    await events.info(
        EventType.QUERIES_GENERATED,
        f"Generated {len(planned)} queries for {len(targets)} targets.",
        label="Query",
        data={
            "rationale": rationale,
            "queries": [
                {
                    "id": q.id,
                    "text": q.text,
                    "subq": q.subq_id,
                    "facet": q.facet_id,
                    "purpose": q.purpose.value,
                    "why": q.rationale,
                }
                for q in planned
            ],
        },
    )


# --- search ------------------------------------------------------------------------------------


_BROADEN = re.compile(r'"|\bsite:\S+|\b(19|20)\d{2}\b')


async def search(state: ResearchState, deps: AgentDeps, events: EventSink) -> None:
    planned = [
        q
        for q in state.queries
        if q.iteration == state.iteration and q.status is QueryStatus.PLANNED
    ]
    if not planned:
        return
    semaphore = asyncio.Semaphore(deps.settings.concurrency.max_parallel_searches)
    both = {"tavily", "brave"} <= set(deps.search.available)
    max_results = deps.settings.search.results_per_query

    async def run(index: int, query: QueryRecord) -> None:
        async with semaphore:
            if deps.meter.searches_left() <= 0:
                query.status = QueryStatus.SKIPPED_BUDGET
                return
            preferred = "brave" if both and index % 3 == 2 else None
            request = SearchRequest(
                query.text, max_results=max_results, language=detect_language(query.text)
            )
            outcome = await deps.search.search(
                request, events=events, subquestion_id=query.subq_id, preferred=preferred
            )
        query.provider = outcome.provider
        query.cache_hit = outcome.cache_hit
        query.result_count = len(outcome.hits)
        query.error_code = outcome.error_code.value if outcome.error_code else None
        if outcome.hits:
            query.status = QueryStatus.DONE
            state.pending_hits.extend(
                PendingHit(
                    hit=hit.model_dump(mode="json"), query_id=query.id, subq_id=query.subq_id
                )
                for hit in outcome.hits
            )
            return
        query.status = QueryStatus.FAILED if outcome.failed else QueryStatus.EMPTY
        # One broadened retry for an empty result (v0.6 §13.2, SEARCH_EMPTY).
        broadened = " ".join(_BROADEN.sub(" ", query.text).split())
        if (
            query.status is QueryStatus.EMPTY
            and query.purpose is not QueryPurpose.BROADENED
            and broadened
            and broadened != query.text
            and deps.meter.searches_left() > 0
        ):
            retry = QueryRecord(
                id=f"q{len(state.queries) + 1}",
                text=broadened,
                subq_id=query.subq_id,
                facet_id=query.facet_id,
                purpose=QueryPurpose.BROADENED,
                rationale=f"broadened from {query.id}",
                iteration=state.iteration,
            )
            state.queries.append(retry)
            await run(index, retry)

    await asyncio.gather(*(run(i, q) for i, q in enumerate(planned)))
    ran = [q for q in state.queries if q.iteration == state.iteration]
    counts = {status: sum(1 for q in ran if q.status is status) for status in QueryStatus}
    await events.info(
        EventType.SEARCH_CALLED,
        f"Ran {len(ran)} queries: {counts[QueryStatus.DONE]} with results, "
        f"{counts[QueryStatus.EMPTY]} empty, {counts[QueryStatus.FAILED]} failed; "
        f"{len(state.pending_hits)} results.",
        label="Search",
        data={
            "searches_left": deps.meter.searches_left(),
            "skipped_for_budget": counts[QueryStatus.SKIPPED_BUDGET],
        },
    )


# --- process_results ---------------------------------------------------------------------------


def _origin_index(state: ResearchState, deps: AgentDeps) -> OriginIndex:
    settings = deps.settings.dedup
    index = OriginIndex(
        threshold=settings.minhash_jaccard_threshold,
        num_perm=settings.minhash_num_perm,
        shingle_size=settings.minhash_shingle_size,
    )
    for doc in state.documents.values():
        if doc.origin_id and doc.content_hash:
            index.restore(doc.id, doc.origin_id, doc.minhash, content_hash=doc.content_hash)
    return index


def _triage(
    doc: Document, state: ResearchState, deps: AgentDeps, hit: SearchHit, query_text: str
) -> float:
    subq_text = " ".join(
        subq.text for subq_id in doc.subq_ids if (subq := state.subquestion(subq_id))
    )
    relevance = max(
        overlap_ratio(subq_text, f"{doc.title} {doc.snippet}"),
        overlap_ratio(query_text, f"{doc.title} {doc.snippet}"),
    )
    authority = deps.tiers.tier_score(deps.tiers.tier_of(doc.domain))
    rank_signal = hit.provider_score if hit.provider_score is not None else 1 / (hit.rank + 1)
    recency = recency_score(doc.published_at, state.as_of, state.analysis.time_scope)
    score = 0.4 * relevance + 0.3 * authority + 0.2 * rank_signal + 0.1 * recency
    # The literal question name may be ApilexAI while sources write Apilex.ai or Apilex.
    # Profile triage must not let generic pages about "founders" displace the named company.
    if is_own_domain(doc.domain, self_primary_entities(state.analysis)):
        score = max(score, 0.7 + 0.2 * relevance)
    elif state.analysis.answer_type == "profile":
        compact = re.sub(
            r"[^a-z0-9]", "", f"{doc.title} {doc.snippet} {doc.content[:1500]}".lower()
        )
        names = [re.sub(r"[^a-z0-9]", "", name.lower()) for name in state.analysis.entities]
        aliases = [name[:-2] if name.endswith("ai") and len(name) > 5 else name for name in names]
        matches = any(len(name) >= 3 and name in compact for name in [*names, *aliases])
        score = min(1.0, score + 0.3) if matches else score * 0.25
    return round(score, 4)


async def process_results(state: ResearchState, deps: AgentDeps, events: EventSink) -> None:
    queries = {q.id: q for q in state.queries}
    total = len(state.pending_hits)
    duplicates = 0
    new_docs: list[str] = []
    best_hit: dict[str, tuple[SearchHit, str]] = {}

    for pending in state.pending_hits:
        hit = SearchHit.model_validate(pending.hit)
        canonical = canonicalize_url(hit.url)
        if canonical is None:
            continue
        existing = next((d for d in state.documents.values() if d.canonical_url == canonical), None)
        query_text = queries[pending.query_id].text if pending.query_id in queries else ""
        if existing is not None:
            duplicates += 1
            if pending.subq_id not in existing.subq_ids:
                existing.subq_ids.append(pending.subq_id)
            if pending.query_id not in existing.query_ids:
                existing.query_ids.append(pending.query_id)
            if hit.content and not existing.content:
                existing.content = hit.content[:MAX_DOCUMENT_CHARS]
                existing.fetched = True
            continue
        doc = Document(
            id=f"d{len(state.documents) + 1}",
            url=hit.url,
            canonical_url=canonical,
            domain=domain_of(canonical),
            title=hit.title,
            snippet=hit.snippet,
            content=(hit.content or "")[:MAX_DOCUMENT_CHARS],
            fetched=bool(hit.content),
            published_at=hit.published_at,
            date_provenance="search_provider" if hit.published_at else None,
            provider=hit.provider,
            subq_ids=[pending.subq_id],
            query_ids=[pending.query_id],
            first_seen_iteration=state.iteration,
        )
        state.documents[doc.id] = doc
        new_docs.append(doc.id)
        best_hit[doc.id] = (hit, query_text)
    state.pending_hits = []

    for doc_id, (hit, query_text) in best_hit.items():
        doc = state.documents[doc_id]
        if doc.published_at is None:
            doc.published_at, doc.date_provenance = dated_header(
                doc.content or doc.snippet, doc.url
            )
        doc.triage_score = _triage(doc, state, deps, hit, query_text)

    # Snippet-first triage: only the top-K unextracted documents per sub-question go further.
    k = (
        deps.settings.fetch.top_k_first_round
        if state.iteration == 1
        else deps.settings.fetch.top_k_followup_round
    )
    selected: list[Document] = []
    for subq in state.plan:
        if not subq.open:
            continue
        candidates = sorted(
            (
                d
                for d in state.documents.values()
                if subq.id in d.subq_ids
                and not d.extracted
                and d.selected_round is None
                and d.triage_score >= 0.15
            ),
            key=lambda d: (-d.triage_score, d.id),
        )
        for doc in candidates[:k]:
            if doc not in selected:
                doc.selected_round = state.iteration
                selected.append(doc)

    # Fetch missing text and publication metadata, including undated provider-supplied raw text.
    semaphore = asyncio.Semaphore(deps.settings.concurrency.max_parallel_fetches)
    fetch_failures = 0

    async def fetch(doc: Document) -> None:
        nonlocal fetch_failures
        metadata_only = doc.fetched
        async with semaphore:
            page = await deps.fetcher.fetch(doc.url)
        if page.ok and page.text:
            if not metadata_only:
                doc.content = page.text[:MAX_DOCUMENT_CHARS]
                doc.fetched = True
            doc.title = doc.title or (page.title or "")
            if doc.published_at is None and page.published_at:
                doc.published_at = page.published_at
                doc.date_provenance = "page_metadata"
        elif not metadata_only:
            fetch_failures += 1
            doc.fetch_error = page.error
            doc.content = doc.snippet
            await events.error(
                AgentError(
                    code=ErrorCode.FETCH_FAILED,
                    node=events.node,
                    decision="continue with the snippet",
                    outcome=f"{doc.domain}: {page.error}",
                )
            )

    await asyncio.gather(
        *(fetch(doc) for doc in selected if not doc.fetched or doc.published_at is None)
    )

    # L2: near-duplicate documents share an origin.
    index = _origin_index(state, deps)
    for doc in selected:
        text = doc.content or doc.snippet or doc.title
        doc.origin_id = index.assign(doc.id, text)
        doc.content_hash = content_hash(text)
        doc.minhash = index.signatures.get(doc.id, [])
    # An origin is named after its first document, so any other name means "a copy of".
    syndicated = sum(1 for doc in selected if doc.origin_id != f"o-{doc.id}")
    state.bump("fetch_failures", fetch_failures)
    state.bump("duplicate_urls", duplicates)

    await events.info(
        EventType.NODE_FINISHED,
        f"{total} results -> {len(new_docs)} new pages ({duplicates} duplicate URLs); "
        f"selected {len(selected)} for reading, {fetch_failures} fetch failures, "
        f"{syndicated} share an origin with another page.",
        label="Sources",
        data={
            "selected": [
                {"id": d.id, "domain": d.domain, "triage": d.triage_score, "origin": d.origin_id}
                for d in selected
            ],
            "top_k": k,
        },
    )


def excerpt(doc: Document, limit: int) -> str:
    return truncate(doc.content or doc.snippet, limit)
