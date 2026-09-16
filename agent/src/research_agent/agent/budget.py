"""Live counters and the hard limits they are checked against (architecture v0.6 §7, D11).

The counters always run - searches, tokens, cost, elapsed time are shown live in the UI whether
or not a limit is set. Wall-clock and cost limits ship disabled (`null`) and are enabled per run;
their code path is the same either way, and a scenario test switches them on to prove it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum

from research_agent.config.schema import BudgetSettings


class BudgetLimit(StrEnum):
    ITERATIONS = "max_iterations"
    SEARCHES = "max_searches"
    WALL_CLOCK = "max_wall_clock_seconds"
    COST = "max_cost_usd"


@dataclass
class BudgetMeter:
    settings: BudgetSettings
    clock: object = field(default=None, repr=False)
    started_at: float = field(init=False)
    searches: int = 0
    llm_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    iteration: int = 0

    def __post_init__(self) -> None:
        self.started_at = self._now()

    def _now(self) -> float:
        # Looked up on every call, so a test can patch `time.monotonic`.
        clock = self.clock if self.clock is not None else time.monotonic
        now: float = clock()  # type: ignore[operator]
        return now

    @property
    def elapsed_seconds(self) -> float:
        return self._now() - self.started_at

    def add_llm(self, *, tokens_in: int, tokens_out: int, cost_usd: float) -> None:
        self.llm_calls += 1
        self.tokens_in += tokens_in
        self.tokens_out += tokens_out
        self.cost_usd += cost_usd

    def add_search(self, count: int = 1) -> None:
        self.searches += count

    def searches_left(self) -> int:
        return max(0, self.settings.max_searches - self.searches)

    def exceeded(self) -> BudgetLimit | None:
        """The first hard limit that is spent, or None. Iterations are checked by the router."""
        if self.searches >= self.settings.max_searches:
            return BudgetLimit.SEARCHES
        wall_clock = self.settings.max_wall_clock_seconds
        if wall_clock is not None and self.elapsed_seconds >= wall_clock:
            return BudgetLimit.WALL_CLOCK
        cost = self.settings.max_cost_usd
        if cost is not None and self.cost_usd >= cost:
            return BudgetLimit.COST
        return None

    def restore(self, snapshot: dict[str, float | int]) -> None:
        """Continue counting after a resume, including the time already spent."""
        self.searches = int(snapshot.get("searches", 0))
        self.llm_calls = int(snapshot.get("llm_calls", 0))
        self.tokens_in = int(snapshot.get("tokens_in", 0))
        self.tokens_out = int(snapshot.get("tokens_out", 0))
        self.cost_usd = float(snapshot.get("cost_usd", 0.0))
        self.iteration = int(snapshot.get("iteration", 0))
        self.started_at = self._now() - float(snapshot.get("elapsed_seconds", 0.0))

    def snapshot(self) -> dict[str, float | int]:
        return {
            "iteration": self.iteration,
            "searches": self.searches,
            "searches_left": self.searches_left(),
            "llm_calls": self.llm_calls,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "cost_usd": round(self.cost_usd, 6),
            "elapsed_seconds": round(self.elapsed_seconds, 2),
        }
