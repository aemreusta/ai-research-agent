"""Layered duplicate detection (architecture v0.6 §8).

| Layer | Method | Catches |
|---|---|---|
| L1 | URL canonicalisation + exact match | the same page twice |
| L2 | normalised-text hash + MinHash (word 5-gram, Jaccard >= 0.8) | syndicated copies |
| L2b | shared long verbatim quotes (`syndication`) | copies wrapped in different page chrome |
| L3 | claim embeddings (+ same entity) | one fact phrased differently (`agent.clustering`) |
| L4 | normalised token Jaccard | the same query generated again |

The rule that ties them together: corroboration counts independent *origins*, never URLs - and
pages of one publisher (`site_of`) are never independent of each other.
"""

from research_agent.agent.dedup.minhash import OriginIndex, minhash_signature
from research_agent.agent.dedup.queries import QueryDeduplicator, normalise_query
from research_agent.agent.dedup.urls import canonicalize_url, domain_of, site_of

__all__ = [
    "OriginIndex",
    "QueryDeduplicator",
    "canonicalize_url",
    "domain_of",
    "minhash_signature",
    "normalise_query",
    "site_of",
]
