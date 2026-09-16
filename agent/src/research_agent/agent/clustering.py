"""L3: claims that state the same fact become one cluster; support = distinct origins.

A claim joins an existing cluster when all of these hold:

* same sub-question and the same normalised entity (or neither names one),
* similar enough - cosine on embeddings, or token Jaccard when no embedding model is available,
* no conflicting value - two claims with different numbers or dates are *not* the same fact,
  however similar the wording; merging them would hide the contradiction the report must show.

Cluster ids are stable across rounds, so a finding cited in round 2 is still the same finding in
round 4 and the UI can follow it.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping

from research_agent.agent.state import Claim, ClaimCluster, ClusterStatus, Document
from research_agent.agent.text import fold, token_jaccard
from research_agent.config.schema import DedupSettings
from research_agent.gate.numeric import extract_quantities, quantities_match

_SUFFIXES = re.compile(
    r"\b(?:a\.?\s?ş\.?|inc\.?|ltd\.?|llc|gmbh|corp\.?|co\.?|plc|s\.?a\.?|as)$", re.IGNORECASE
)


def normalise_entity(name: str | None) -> str | None:
    if not name:
        return None
    cleaned = fold(name).strip().rstrip(",. ")
    previous = None
    while previous != cleaned:
        previous = cleaned
        cleaned = _SUFFIXES.sub("", cleaned).strip().rstrip(",. ")
    compact = re.sub(r"[^\w]", "", cleaned)
    return compact or None


def _entities_compatible(left: str | None, right: str | None) -> bool:
    a, b = normalise_entity(left), normalise_entity(right)
    if a is None or b is None:
        return True
    return a == b or a.startswith(b) or b.startswith(a)


def _cosine(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right, strict=False))
    norm = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return dot / norm if norm else 0.0


def values_conflict(left: Claim | ClaimCluster, right: Claim | ClaimCluster) -> bool:
    """True when both state a quantity and no reading of one matches the other."""
    a = extract_quantities(left.value or left.text if isinstance(left, Claim) else left.value or "")
    b = extract_quantities(
        right.value or right.text if isinstance(right, Claim) else right.value or ""
    )
    if not a or not b:
        return False
    for first in a:
        if any(
            quantities_match(first, second, relative=0.02, percent_points=0.5, date_days=31)
            for second in b
        ):
            return False
    return True


def cluster_claims(
    existing: Mapping[str, ClaimCluster],
    new_claims: list[Claim],
    *,
    vectors: Mapping[str, list[float]] | None,
    documents: Mapping[str, Document],
    settings: DedupSettings,
    all_claims: Mapping[str, Claim],
) -> dict[str, ClaimCluster]:
    """Assign each new claim to a cluster; returns the full, updated cluster map.

    `all_claims` must hold every claim already in `existing` - cluster statistics are recomputed
    from it, so a missing claim would silently shrink a cluster's support.
    """
    clusters = {cid: cluster.model_copy(deep=True) for cid, cluster in existing.items()}
    claims: dict[str, Claim] = dict(all_claims)
    missing = {cid for c in clusters.values() for cid in c.claim_ids} - set(claims)
    if missing:
        raise ValueError(
            f"cluster_claims needs every existing claim; missing {sorted(missing)[:5]}"
        )
    claims.update({claim.id: claim for claim in new_claims})
    representative: dict[str, Claim] = {}
    for cluster in clusters.values():
        if cluster.claim_ids and cluster.claim_ids[0] in claims:
            representative[cluster.id] = claims[cluster.claim_ids[0]]

    def similarity(claim: Claim, other: Claim) -> tuple[float, float]:
        if vectors is not None and claim.id in vectors and other.id in vectors:
            return _cosine(
                vectors[claim.id], vectors[other.id]
            ), settings.claim_similarity_threshold
        return token_jaccard(claim.text, other.text), settings.claim_lexical_fallback_threshold

    for claim in new_claims:
        best_id: str | None = None
        best_score = 0.0
        for cid, cluster in clusters.items():
            if cluster.subq_id != claim.subq_id:
                continue
            anchor = representative.get(cid)
            if anchor is None or not _entities_compatible(claim.entity, anchor.entity):
                continue
            if values_conflict(claim, anchor):
                continue
            score, threshold = similarity(claim, anchor)
            if normalise_entity(claim.entity) is None or normalise_entity(anchor.entity) is None:
                threshold = min(1.0, threshold + 0.04)  # be stricter without an entity anchor
            if score >= threshold and score > best_score:
                best_id, best_score = cid, score

        if best_id is None:
            best_id = f"k{len(clusters) + 1}"
            while best_id in clusters:
                best_id = f"k{int(best_id[1:]) + 1}"
            clusters[best_id] = ClaimCluster(
                id=best_id,
                subq_id=claim.subq_id,
                claim_ids=[],
                doc_ids=[],
                origin_ids=[],
                statement=claim.text,
                entity=claim.entity,
                attribute=claim.attribute,
                value=claim.value,
                unit=claim.unit,
                as_of=claim.as_of,
                kind=claim.kind,
            )
            representative[best_id] = claim

        cluster = clusters[best_id]
        if claim.id not in cluster.claim_ids:
            cluster.claim_ids.append(claim.id)
        if claim.facet_id and claim.facet_id not in cluster.facet_ids:
            cluster.facet_ids.append(claim.facet_id)

    for cluster in clusters.values():
        _refresh(cluster, claims, documents)
    return clusters


def _refresh(
    cluster: ClaimCluster, claims: Mapping[str, Claim], documents: Mapping[str, Document]
) -> None:
    """Recompute support and confidence from the ledger."""
    members = [claims[cid] for cid in cluster.claim_ids if cid in claims]
    doc_ids = list(dict.fromkeys(claim.doc_id for claim in members))
    # "According to X ..." makes X the origin, whoever republished it (§8).
    origins = list(
        dict.fromkeys(
            f"said:{normalise_entity(claim.attributed_to)}"
            if claim.attributed_to
            else claim.origin_id
            for claim in members
        )
    )
    cluster.doc_ids = doc_ids
    cluster.origin_ids = origins

    best_by_origin: dict[str, float] = {}
    has_primary = False
    best = 0.0
    for claim in members:
        document = documents.get(claim.doc_id)
        if document is None:
            continue
        score = document.score.total
        best = max(best, score)
        has_primary = has_primary or document.score.is_primary
        key = (
            f"said:{normalise_entity(claim.attributed_to)}"
            if claim.attributed_to
            else claim.origin_id
        )
        best_by_origin[key] = max(best_by_origin.get(key, 0.0), score)

    # Independent evidence combines like independent probabilities.
    doubt = 1.0
    for score in best_by_origin.values():
        doubt *= 1.0 - 0.8 * max(0.0, min(1.0, score))
    cluster.confidence = round(min(0.99, 1.0 - doubt), 4)
    cluster.best_source_score = round(best, 4)
    cluster.has_primary = has_primary
    if cluster.status is not ClusterStatus.CONTESTED:
        cluster.status = (
            ClusterStatus.SUPPORTED if len(origins) >= 2 else ClusterStatus.SINGLE_SOURCE
        )
