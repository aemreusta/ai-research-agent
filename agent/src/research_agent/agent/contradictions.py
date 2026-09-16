"""Rule-based contradiction candidates (architecture v0.6 §10, step 1).

Only candidates are found here; an LLM judge classifies them (true conflict, different time,
different scope, rounding, consistent) in the node. The rule is: the same entity and attribute,
the same period when both state one, and values (with their units) that do not match within the
configured tolerance.

Pairs the rules can settle are not sent to the judge: a 2025 figure next to a 2030 forecast is
not a conflict, and in live runs such pairs made up most of the judge's work.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from itertools import combinations

from research_agent.agent.clustering import entities_equivalent, entity_contains, value_text
from research_agent.agent.state import ClaimCluster
from research_agent.agent.text import fold, token_jaccard, token_set
from research_agent.config.schema import ContradictionSettings
from research_agent.gate.numeric import extract_quantities, quantities_match

# Hedges and qualifiers that do not change which property is meant.
_QUALIFIERS = frozenset(
    {
        "projected",
        "estimated",
        "expected",
        "forecast",
        "forecasted",
        "anticipated",
        "current",
        "total",
        "overall",
        "tahmini",
        "öngörülen",
        "beklenen",
        "toplam",
    }
)
_SYNONYMS = {"valuation": "size", "value": "size", "worth": "size", "büyüklüğü": "büyüklük"}
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")


def _attribute_key(attribute: str | None) -> str | None:
    if not attribute:
        return None
    words = [
        _SYNONYMS.get(word, word) for word in fold(attribute).split() if word not in _QUALIFIERS
    ]
    return " ".join(words) or None


def _periods_differ(left: str | None, right: str | None) -> bool:
    """Both name a period and they are not the same one (2025 vs 2030, 2023-2030 vs 2025-2030)."""
    if not left or not right:
        return False
    a, b = _YEAR.findall(left), _YEAR.findall(right)
    if a and b:
        return a != b
    if a or b:
        return False  # "Q3" against "2025": cannot tell
    return fold(left).split() != fold(right).split()


def _same_entity(left: ClaimCluster, right: ClaimCluster) -> bool:
    if entities_equivalent(left.entity, right.entity):
        return True
    # "Europe" and "Europe legal tech market" are the same thing only in the same period.
    return (
        left.as_of is not None
        and left.as_of == right.as_of
        and entity_contains(left.entity, right.entity)
    )


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
    a = extract_quantities(value_text(left.value, left.unit), language=language)
    b = extract_quantities(value_text(right.value, right.unit), language=language)
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
        if not _same_entity(left, right):
            continue
        if _periods_differ(left.as_of, right.as_of):
            continue
        if not _same_attribute(left.attribute, right.attribute):
            continue
        if _values_differ(left, right, settings, language):
            pairs.append((left.id, right.id))
    return pairs
