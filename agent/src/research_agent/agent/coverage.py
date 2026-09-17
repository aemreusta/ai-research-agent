"""Facet sufficiency and stagnation - the "do we know enough?" decision, made in code (§7).

The LLM's coverage assessment can add missing facets and explain gaps; whether a facet is
sufficient is decided here:

    sufficient  <=>  (a primary source scoring >= primary_source_min_score)
                     or (>= min_independent_origins independent origins)
                     and no contradiction still awaiting follow-up blocks it

A conflict with a follow-up attempt stays contested in the report but stops blocking coverage.
"""

from __future__ import annotations

import re

from research_agent.agent.state import (
    ClusterStatus,
    FacetStatus,
    ResearchState,
    SubQuestion,
    SubQuestionStatus,
)
from research_agent.config.schema import BudgetSettings, ScoringSettings


def assess_facets(state: ResearchState, settings: ScoringSettings) -> None:
    from research_agent.agent.clustering import independent_origin_keys

    # A contradiction stops blocking its facet once it is resolved, or once a targeted follow-up
    # has been tried: from then on it is reported under "Conflicting / Uncertain" instead of
    # being chased round after round.
    settled = {
        cluster_id
        for contradiction in state.contradictions
        if contradiction.resolved or contradiction.followup_attempted
        for cluster_id in contradiction.cluster_ids
    }
    for subq in state.plan:
        clusters = state.clusters_for(subq.id)
        for facet in subq.facets:
            relevant = [
                cluster
                for cluster in clusters
                if not cluster.requires_fresh_confirmation
                and (
                    facet.id in cluster.facet_ids
                    or (not cluster.facet_ids and len(subq.facets) == 1)
                )
            ]
            members = {
                cid: state.claims[cid]
                for cluster in relevant
                for cid in cluster.claim_ids
                if cid in state.claims
            }
            origins = set(independent_origin_keys(list(members.values()), state.documents).values())
            # Legacy/test snapshots may contain clusters without the underlying claims.
            if not members:
                origins = {origin for cluster in relevant for origin in cluster.origin_ids}
            strong_primary = any(
                cluster.has_primary
                and cluster.best_source_score >= settings.primary_source_min_score
                for cluster in relevant
            )
            contested = any(
                cluster.status is ClusterStatus.CONTESTED and cluster.id not in settled
                for cluster in relevant
            )
            facet_claims = [
                claim
                for claim in state.claims.values()
                if claim.subq_id == subq.id
                and claim.facet_id == facet.id
                and claim.validation_status in {"supported", "legacy"}
            ]
            awaiting_freshness = any(
                c.requires_fresh_confirmation for c in facet_claims
            ) and not any(
                c.time_sensitive and not c.requires_fresh_confirmation for c in facet_claims
            )
            if awaiting_freshness:
                facet.status = FacetStatus.OPEN
            elif contested:
                facet.status = FacetStatus.CONTESTED
            elif strong_primary or len(origins) >= settings.min_independent_origins:
                facet.status = FacetStatus.SUFFICIENT
            else:
                facet.status = FacetStatus.OPEN


def update_progress(subq: SubQuestion, *, gain: int, settings: BudgetSettings) -> None:
    """Move a sub-question along `pending -> searching -> sufficient | exhausted`."""
    if not subq.open:
        return
    if subq.facets and all(facet.status is FacetStatus.SUFFICIENT for facet in subq.facets):
        subq.status = SubQuestionStatus.SUFFICIENT
        subq.rounds_without_progress = 0
        return
    subq.status = SubQuestionStatus.SEARCHING
    if gain > 0:
        subq.rounds_without_progress = 0
        return
    subq.rounds_without_progress += 1
    if subq.rounds_without_progress >= settings.stagnation_threshold:
        subq.status = SubQuestionStatus.EXHAUSTED
        subq.exhausted_reason = NO_PROGRESS_REASON.format(rounds=subq.rounds_without_progress)


NO_PROGRESS_REASON = "no progress for {rounds} rounds (no new findings or independent sources)"
REPEATED_QUERIES_REASON = "only repeated queries could be generated"
_NO_PROGRESS = re.compile(r"no progress for (\d+) rounds")
_TR_REASONS = {
    REPEATED_QUERIES_REASON: "yalnızca daha önce sorulmuş sorgular üretilebildi",
}


def localised_reason(reason: str | None, language: str) -> str:
    """Exhausted reasons are stored in English (logs, events); reports show them localised."""
    if not reason or language != "tr":
        return reason or ""
    if (match := _NO_PROGRESS.match(reason)) is not None:
        return f"{match.group(1)} turdur ilerleme yok (yeni bulgu ya da bağımsız kaynak gelmedi)"
    return _TR_REASONS.get(reason, reason)


def progress_snapshot(state: ResearchState) -> dict[str, tuple[int, int]]:
    """Per sub-question (clusters, origins), taken before a round to measure its gain."""
    return {
        subq.id: (len(state.clusters_for(subq.id)), len(state.origins_for(subq.id)))
        for subq in state.plan
    }


def gain_since(state: ResearchState, snapshot: dict[str, tuple[int, int]], subq_id: str) -> int:
    clusters_before, origins_before = snapshot.get(subq_id, (0, 0))
    return (len(state.clusters_for(subq_id)) - clusters_before) + (
        len(state.origins_for(subq_id)) - origins_before
    )
