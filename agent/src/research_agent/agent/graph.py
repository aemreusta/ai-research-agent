"""The LangGraph wiring (architecture v0.6 §5).

    intake_guard -> analyze_query -> plan
      -> generate_queries -> search -> process_results -> evaluate_sources
      -> extract_claims -> cluster_and_corroborate -> detect_contradictions -> assess_coverage
         -- continue --> generate_queries
         -- stop -----> synthesize -> verify_citations -> output_gate -> END

The graph state is a single JSON document (`research`), so the Postgres checkpoint holds plain
data. Every node goes through `_wrap`, which checks for cancellation at the node boundary, gives
the node its own span on the timeline, and pushes the live counters to the run row.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from itertools import pairwise
from typing import Any, TypedDict, cast

from langgraph.graph import END, START, StateGraph

from research_agent.agent.deps import AgentDeps
from research_agent.agent.nodes import coverage, evidence, intake, report, search
from research_agent.agent.runner import CancelledByRequest
from research_agent.agent.runtime import EventSink
from research_agent.agent.state import ResearchState
from research_agent.observability.events import EventType
from research_agent.prompting.skills import guidance

NodeFn = Callable[[ResearchState, AgentDeps, EventSink], Awaitable[None]]


class GraphState(TypedDict):
    research: dict[str, Any]


NODES: list[tuple[str, NodeFn]] = [
    ("intake_guard", intake.intake_guard),
    ("analyze_query", intake.analyze_query),
    ("plan", intake.plan),
    ("generate_queries", search.generate_queries),
    ("search", search.search),
    ("process_results", search.process_results),
    ("evaluate_sources", evidence.evaluate_sources),
    ("extract_claims", evidence.extract_claims),
    ("cluster_and_corroborate", evidence.cluster_and_corroborate),
    ("detect_contradictions", evidence.detect_contradictions),
    ("assess_coverage", coverage.assess_coverage),
    ("synthesize", report.synthesize),
    ("verify_citations", report.verify_citations),
    ("output_gate", report.output_gate),
]


def _wrap(name: str, fn: NodeFn, deps: AgentDeps) -> Callable[[GraphState], Awaitable[GraphState]]:
    async def node(graph_state: GraphState) -> GraphState:
        if await deps.is_cancelled():
            raise CancelledByRequest
        state = ResearchState.model_validate(graph_state["research"])
        if state.budget and deps.meter.llm_calls == 0 and deps.meter.searches == 0:
            deps.meter.restore(state.budget)  # resumed from a checkpoint
        # These are runtime dependencies, not checkpoint fields. Restore them after a crash.
        chosen = [deps.skills[key] for key in state.skills if key in deps.skills]
        deps.predictor.skill_guidance = guidance(chosen)
        if state.skill_domains:
            deps.tiers = deps.tiers.extended(state.skill_domains)
        events = deps.events.child(name, iteration=state.iteration or None)
        started = time.perf_counter()
        await events.debug(EventType.NODE_STARTED, f"{name} started.")
        await fn(state, deps, events)
        state.budget = deps.meter.snapshot()
        await events.debug(
            EventType.NODE_FINISHED,
            f"{name} finished.",
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        await deps.report_progress(
            {
                "iteration": state.iteration,
                "searches_used": deps.meter.searches,
                "tokens_in": deps.meter.tokens_in,
                "tokens_out": deps.meter.tokens_out,
                "cost_usd": deps.meter.cost_usd,
            }
        )
        return {"research": state.model_dump(mode="json")}

    node.__name__ = name
    return node


def build_graph(deps: AgentDeps) -> StateGraph[GraphState]:
    graph: StateGraph[GraphState] = StateGraph(GraphState)
    for name, fn in NODES:
        # LangGraph's overloads cannot see through the wrapper; the runtime contract is simple.
        graph.add_node(name, cast(Any, _wrap(name, fn, deps)))

    graph.add_edge(START, "intake_guard")
    sequence = [name for name, _ in NODES]
    loop_end = sequence.index("assess_coverage")
    for left, right in pairwise(sequence[: loop_end + 1]):
        graph.add_edge(left, right)

    def route(graph_state: GraphState) -> str:
        return coverage.route_after_coverage(ResearchState.model_validate(graph_state["research"]))

    graph.add_conditional_edges(
        "assess_coverage",
        route,
        {"generate_queries": "generate_queries", "synthesize": "synthesize"},
    )
    graph.add_edge("synthesize", "verify_citations")
    graph.add_edge("verify_citations", "output_gate")
    graph.add_edge("output_gate", END)
    return graph


def recursion_limit(deps: AgentDeps) -> int:
    """Enough steps for the configured rounds, and not one more: a bug cannot loop forever."""
    per_round = 8
    return 3 + per_round * deps.settings.budget.max_iterations + 3 + 5
