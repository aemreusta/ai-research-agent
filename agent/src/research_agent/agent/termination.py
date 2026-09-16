"""The router: stop or search again (architecture v0.6 §7).

Checked in this order after every round:

1. success   - every `must` sub-question is sufficient -> `sufficient`
2. hard limit - iterations, searches, and (when enabled) wall clock and cost -> `budget` /
               `max_iterations`
3. no progress - nothing left open (all sufficient or exhausted) -> `no_progress`
4. otherwise continue, but only for the gaps that remain

Success is checked before the limits so that a run which finished its job on its last allowed
round is reported as successful rather than as having run out of budget. Either way it stops.
Whatever the reason, synthesis always runs and exhausted sub-questions become Known Gaps.
"""

from __future__ import annotations

from dataclasses import dataclass

from research_agent.agent.budget import BudgetLimit, BudgetMeter
from research_agent.agent.state import ResearchState, StopReason, SubQuestionStatus
from research_agent.config.schema import BudgetSettings


@dataclass(frozen=True, slots=True)
class RouteDecision:
    stop: bool
    reason: StopReason | None
    rule: str
    detail: str


def decide(state: ResearchState, meter: BudgetMeter, settings: BudgetSettings) -> RouteDecision:
    musts = state.must_subquestions() or state.plan
    if musts and all(subq.status is SubQuestionStatus.SUFFICIENT for subq in musts):
        return RouteDecision(
            True,
            StopReason.SUFFICIENT,
            "success",
            f"all {len(musts)} required sub-questions are answered",
        )

    if state.iteration >= settings.max_iterations:
        return RouteDecision(
            True,
            StopReason.MAX_ITERATIONS,
            "hard_limit",
            f"max_iterations={settings.max_iterations} reached",
        )

    exceeded = meter.exceeded()
    if exceeded is not None:
        detail = {
            BudgetLimit.SEARCHES: f"max_searches={settings.max_searches} used",
            BudgetLimit.WALL_CLOCK: (
                f"max_wall_clock_seconds={settings.max_wall_clock_seconds} elapsed "
                f"({meter.elapsed_seconds:.0f}s)"
            ),
            BudgetLimit.COST: (
                f"max_cost_usd={settings.max_cost_usd} spent (${meter.cost_usd:.4f})"
            ),
        }.get(exceeded, exceeded.value)
        return RouteDecision(True, StopReason.BUDGET, "hard_limit", detail)

    open_subqs = state.open_subquestions()
    if not open_subqs:
        return RouteDecision(
            True,
            StopReason.NO_PROGRESS,
            "no_progress",
            "no open sub-questions left (remaining ones are exhausted)",
        )

    names = ", ".join(subq.id for subq in open_subqs)
    return RouteDecision(
        False, None, "gaps_remain", f"{len(open_subqs)} sub-question(s) still open: {names}"
    )
