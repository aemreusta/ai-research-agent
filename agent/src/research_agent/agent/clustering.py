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
from research_agent.agent.text import fold, token_jaccard, tokens
from research_agent.config.schema import DedupSettings
from research_agent.gate.numeric import extract_quantities, quantities_match

_SUFFIXES = re.compile(
    r"\b(?:a\.?\s?ş\.?|inc\.?|ltd\.?|llc|gmbh|corp\.?|co\.?|plc|s\.?a\.?|as)$", re.IGNORECASE
)


_ARTICLES = re.compile(r"^(?:the|a|an)\s+", re.IGNORECASE)


def normalise_entity(name: str | None) -> str | None:
    if not name:
        return None
    cleaned = _ARTICLES.sub("", fold(name).strip()).rstrip(",. ")
    previous = None
    while previous != cleaned:
        previous = cleaned
        cleaned = _SUFFIXES.sub("", cleaned).strip().rstrip(",. ")
    compact = re.sub(r"[^\w]", "", cleaned)
    return compact or None


# Words that name the kind of thing rather than which one ("Europe legal tech market").
_GENERIC_ENTITY_WORDS = frozenset(
    {"market", "markets", "industry", "sector", "pazar", "pazari", "sektor", "sektör", "sektörü"}
)


def entity_words(name: str | None) -> frozenset[str]:
    if not name:
        return frozenset()
    cleaned = _ARTICLES.sub("", fold(name).strip())
    return frozenset(
        word
        for word in tokens(_SUFFIXES.sub("", cleaned.rstrip(",. ")))
        if word not in _GENERIC_ENTITY_WORDS
    )


def _same_word(left: str, right: str) -> bool:
    """Equal, or one is a >=4-letter stem of the other (europe/european, tech/technology)."""
    if left == right:
        return True
    shorter, longer = sorted((left, right), key=len)
    return len(shorter) >= 4 and longer.startswith(shorter)


def _covered(small: frozenset[str], large: frozenset[str]) -> bool:
    return bool(small) and all(any(_same_word(a, b) for b in large) for a in small)


def entities_equivalent(left: str | None, right: str | None) -> bool:
    """The same entity written differently ("Europe Legal Tech Market" ~ "European legal
    technology market"). Both must name one; qualifiers must match on both sides."""
    a, b = normalise_entity(left), normalise_entity(right)
    if a is None or b is None:
        return False
    if a == b:
        return True
    wa, wb = entity_words(left), entity_words(right)
    return _covered(wa, wb) and _covered(wb, wa)


def entity_contains(left: str | None, right: str | None) -> bool:
    """A bare one-word name inside the other ("Europe" in "Europe legal tech market").

    Only one word: "legal tech" inside "Germany legal tech" would pair a global figure with a
    national one.
    """
    small, large = sorted((entity_words(left), entity_words(right)), key=len)
    return len(small) == 1 and _covered(small, large)


def _entities_compatible(left: str | None, right: str | None) -> bool:
    a, b = normalise_entity(left), normalise_entity(right)
    if a is None or b is None:
        return True
    return a.startswith(b) or b.startswith(a) or entities_equivalent(left, right)


_SCALE_WORDS = frozenset(
    {"thousand", "million", "billion", "trillion", "bn", "mn", "bin", "milyon", "milyar", "trilyon"}
)


_SYMBOLS = {"usd": "$", "dollars": "$", "eur": "€", "euro": "€", "gbp": "£", "try": "₺", "tl": "₺"}


def value_text(value: str | None, unit: str | None) -> str:
    """The value with its unit, scale words next to the number ("8,624 million USD").

    Extractors write the unit in any order ("USD million"); without it 6.15 (billion) and
    8,624 (million) would be compared as bare numbers.
    """
    if not value:
        return ""
    if not unit:
        return value
    written = set(fold(value).split())
    words = [
        word
        for word in unit.split()
        if fold(word) not in written and _SYMBOLS.get(fold(word), "\0") not in value
    ]
    scale = [w for w in words if fold(w) in _SCALE_WORDS]
    rest = [w for w in words if fold(w) not in _SCALE_WORDS]
    return " ".join([value, *scale, *rest])


def _cosine(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right, strict=False))
    norm = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return dot / norm if norm else 0.0


def _quantity_text(item: Claim | ClaimCluster) -> str:
    if item.value:
        return value_text(item.value, item.unit)
    return item.text if isinstance(item, Claim) else ""


def values_conflict(left: Claim | ClaimCluster, right: Claim | ClaimCluster) -> bool:
    """True when both state a quantity and no reading of one matches the other."""
    a = extract_quantities(_quantity_text(left))
    b = extract_quantities(_quantity_text(right))
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
    # "According to X ..." makes X the origin, whoever republished it (§8). One origin text
    # counts once, even when one of its sentences names the speaker and the next does not.
    speaker: dict[str, str] = {}
    for claim in members:
        if claim.attributed_to and claim.origin_id not in speaker:
            speaker[claim.origin_id] = f"said:{normalise_entity(claim.attributed_to)}"

    def origin_key(claim: Claim) -> str:
        return speaker.get(claim.origin_id, claim.origin_id)

    origins = list(dict.fromkeys(origin_key(claim) for claim in members))
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
        key = origin_key(claim)
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
