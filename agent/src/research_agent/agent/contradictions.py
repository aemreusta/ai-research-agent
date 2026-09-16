"""Rule-based contradiction candidates (architecture v0.6 §10, step 1).

Only candidates are found here; an LLM judge classifies them (true conflict, different time,
different scope, rounding) in the node. The rule is: the same normalised entity and attribute,
and values that do not match within the configured tolerance.
"""

from __future__ import annotations

from collections.abc import Mapping
from itertools import combinations

from research_agent.agent.clustering import normalise_entity
from research_agent.agent.state import ClaimCluster
from research_agent.agent.text import fold, token_jaccard, token_set
from research_agent.config.schema import ContradictionSettings
from research_agent.gate.numeric import extract_quantities, quantities_match


def _attribute_key(attribute: str | None) -> str | None:
    if not attribute:
        return None
    return " ".join(fold(attribute).split())


def _same_attribute(left: str | None, right: str | None) -> bool:
    """Same property, allowing one description to be a more detailed version of the other.

    Overlap relative to the shorter phrase ("rules high risk apply" inside "rules high risk
    apply under a proposed delay") - these are only candidates, the judge filters them.
    """
    a, b = _attribute_key(left), _attribute_key(right)
    if a is None or b is None:
        return False
    if a == b or token_jaccard(a, b) >= 0.6:
        return True
    ta, tb = token_set(a), token_set(b)
    shorter = min(len(ta), len(tb))
    return shorter >= 2 and len(ta & tb) / shorter >= 0.75


def _values_differ(
    left: ClaimCluster, right: ClaimCluster, settings: ContradictionSettings, language: str
) -> bool:
    if not left.value or not right.value:
        return False
    a = extract_quantities(left.value, language=language)
    b = extract_quantities(right.value, language=language)
    if a and b:
        return not any(
            quantities_match(
                x,
                y,
                relative=settings.numeric_relative_tolerance,
                percent_points=settings.numeric_relative_tolerance * 10,
                date_days=settings.date_granularity_days,
            )
            for x in a
            for y in b
        )
    if a or b:
        return True  # one side is a quantity, the other is not: worth a look
    return token_jaccard(left.value, right.value) < 0.5


def contradiction_candidates(
    clusters: Mapping[str, ClaimCluster],
    settings: ContradictionSettings,
    *,
    language: str,
    already_judged: set[frozenset[str]] | None = None,
) -> list[tuple[str, str]]:
    judged = already_judged or set()
    ordered = sorted(clusters.values(), key=lambda cluster: cluster.id)
    pairs: list[tuple[str, str]] = []
    for left, right in combinations(ordered, 2):
        if frozenset((left.id, right.id)) in judged:
            continue
        if normalise_entity(left.entity) != normalise_entity(right.entity):
            continue
        if left.entity is None:
            continue
        if not _same_attribute(left.attribute, right.attribute):
            continue
        if _values_differ(left, right, settings, language):
            pairs.append((left.id, right.id))
    return pairs
