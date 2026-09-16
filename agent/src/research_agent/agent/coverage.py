"""Facet sufficiency and stagnation - the "do we know enough?" decision, made in code (§7).

The LLM's coverage assessment can add missing facets and explain gaps; whether a facet is
sufficient is decided here:

    sufficient  <=>  (a primary source scoring >= primary_source_min_score)
                     or (>= min_independent_origins independent origins)
                     and no unresolved contradiction touches it
"""

from __future__ import annotations

from research_agent.agent.state import (
    ClusterStatus,
    FacetStatus,
    ResearchState,
    SubQuestion,
    SubQuestionStatus,
)
from research_agent.config.schema import BudgetSettings, ScoringSettings


def assess_facets(state: ResearchState, settings: ScoringSettings) -> None:
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
                if facet.id in cluster.facet_ids
                or (not cluster.facet_ids and len(subq.facets) == 1)
            ]
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
            if contested:
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
        subq.exhausted_reason = (
            f"no progress for {subq.rounds_without_progress} rounds "
            "(no new findings or independent sources)"
        )


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
