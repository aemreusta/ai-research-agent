"""L2b: pages that share long verbatim quotes are one origin.

MinHash compares whole pages, so a press release republished by three news sites - each with its
own menu, related-articles box and footer - stays below the near-duplicate threshold and would
count as three confirmations. The extracted quotes are the article body the claims came from; when
two pages share several long ones word for word, they are copies of one text.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping
from itertools import combinations

from research_agent.agent.state import Claim, Document
from research_agent.agent.text import tokens


def merge_syndicated_origins(
    documents: Mapping[str, Document],
    claims: Mapping[str, Claim],
    *,
    min_shared_quotes: int,
    min_quote_words: int,
) -> dict[str, str]:
    """Give copies one origin; returns `{old_origin: new_origin}` for every origin that moved.

    Documents and claims are updated in place. The surviving origin is the smallest id of the
    group, so the result does not depend on the order claims arrive in.
    """
    holders: dict[str, set[str]] = defaultdict(set)
    for claim in claims.values():
        words = tokens(claim.quote, keep_stopwords=True)
        if len(words) >= min_quote_words and claim.doc_id in documents:
            holders[" ".join(words)].add(documents[claim.doc_id].origin_id)

    shared: Counter[tuple[str, str]] = Counter()
    for origins in holders.values():
        for pair in combinations(sorted(origins), 2):
            shared[pair] += 1

    parent: dict[str, str] = {}

    def root(origin: str) -> str:
        while parent.get(origin, origin) != origin:
            origin = parent[origin]
        return origin

    for (left, right), count in sorted(shared.items()):
        if count < min_shared_quotes:
            continue
        a, b = root(left), root(right)
        if a != b:
            parent[max(a, b)] = min(a, b)

    moved = {origin: root(origin) for origin in parent if root(origin) != origin}
    if moved:
        for document in documents.values():
            document.origin_id = moved.get(document.origin_id, document.origin_id)
        for claim in claims.values():
            claim.origin_id = moved.get(claim.origin_id, claim.origin_id)
    return moved
