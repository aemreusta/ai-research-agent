"""L4: a query the agent already ran is not run again, however it is reworded."""

from __future__ import annotations

from collections.abc import Iterable

from research_agent.agent.text import token_set


def normalise_query(text: str) -> frozenset[str]:
    return token_set(text)


class QueryDeduplicator:
    def __init__(self, *, threshold: float, seen: Iterable[str] = ()) -> None:
        self._threshold = threshold
        self._seen: list[frozenset[str]] = [normalise_query(query) for query in seen]

    def is_duplicate(self, query: str) -> bool:
        candidate = normalise_query(query)
        if not candidate:
            return True
        for previous in self._seen:
            union = candidate | previous
            if union and len(candidate & previous) / len(union) >= self._threshold:
                return True
        return False

    def admit(self, query: str) -> bool:
        """Remember the query and report whether it was new."""
        if self.is_duplicate(query):
            return False
        self._seen.append(normalise_query(query))
        return True
