"""Adversarial node inputs that previously passed the happy-path graph suite."""

from __future__ import annotations

import asyncio
import uuid
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from research_agent.agent.deps import AgentDeps
from research_agent.agent.nodes.report import _verify
from research_agent.agent.nodes.search import search
from research_agent.agent.research import Toolkit, build_deps
from research_agent.agent.runtime import MemoryCache, MemoryCallRecorder, MemoryEventSink
from research_agent.agent.state import (
    ClaimCluster,
    QueryRecord,
    QueryStatus,
    Report,
    ReportSection,
    ReportSentence,
    ResearchState,
    SectionKey,
)
from research_agent.config.loader import load_settings
from research_agent.prompting.schemas import VerificationItem, VerificationOutput
from research_agent.providers.search.base import SearchHit, SearchRequest
from research_agent.providers.search.fake import FakeSearchProvider


class SlowEmptySearch(FakeSearchProvider):
    async def search(self, request: SearchRequest, *, timeout: float) -> list[SearchHit]:
        # All initial requests hold their slots when the first empty result arrives.
        await asyncio.sleep(0.01)
        return await super().search(request, timeout=timeout)


async def _deps(slots: int = 5) -> AgentDeps:
    settings = load_settings(overrides={"concurrency.max_parallel_searches": slots}).settings
    run_id = uuid.uuid4()
    return await build_deps(
        run_id=run_id,
        settings=settings,
        toolkit=Toolkit(llm={}, search={"tavily": SlowEmptySearch()}),
        events=MemoryEventSink(run_id),
        recorder=MemoryCallRecorder(),
        cache=MemoryCache(),
        today=date(2026, 9, 17),
    )


@pytest.mark.parametrize("slots", [1, 5])
async def test_empty_queries_broaden_without_holding_search_slots(slots: int) -> None:
    deps = await _deps(slots)
    state = ResearchState(run_id=deps.events.run_id, question="Research?", as_of=deps.today)
    state.iteration = 1
    state.queries = [
        QueryRecord(
            id=f"q{i + 1}", text=f"site:example.com 2026 topic{i}", subq_id="s1", iteration=1
        )
        for i in range(slots)
    ]
    await asyncio.wait_for(search(state, deps, deps.events), timeout=1)
    assert deps.meter.searches == slots * 2
    assert len(state.queries) == slots * 2
    assert all(query.status is QueryStatus.EMPTY for query in state.queries)


def _report_state(deps: AgentDeps) -> ResearchState:
    state = ResearchState(run_id=deps.events.run_id, question="Research?", as_of=deps.today)
    state.clusters["k1"] = ClaimCluster(
        id="k1",
        subq_id="s1",
        claim_ids=[],
        doc_ids=[],
        origin_ids=[],
        statement="Acme sells software.",
    )
    state.report = Report(
        title="Acme",
        language="en",
        sections=[
            ReportSection(
                key=SectionKey.KEY_FINDINGS,
                title="Findings",
                sentences=[ReportSentence(text="Acme sells software.", cluster_ids=["k1"])],
            )
        ],
    )
    return state


@pytest.mark.parametrize("response", ["empty", "duplicate", "wrong_id"])
async def test_missing_verdicts_are_unavailable_not_checked(
    response: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    deps = await _deps()
    state = _report_state(deps)
    item = VerificationItem(sentence_id="key_findings:0", supported=True, reason="entailed")
    items = [] if response == "empty" else [item, item]
    if response == "wrong_id":
        items = [item.model_copy(update={"sentence_id": "unknown:0"})]
    predictor = AsyncMock(return_value=SimpleNamespace(value=VerificationOutput(items=items)))
    monkeypatch.setattr(deps, "predictor", predictor)
    assert state.report is not None
    rejected = await _verify(state, deps, deps.events, state.report)
    assert "key_findings:0" in rejected
    assert state.verification["checked"] == 0
    assert state.verification["unavailable"] == 1
    assert predictor.await_count == 2, "one bounded retry for missing/invalid decisions"


async def test_a_missing_verdict_can_be_recovered_on_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    deps = await _deps()
    state = _report_state(deps)
    predictor = AsyncMock(
        side_effect=[
            SimpleNamespace(value=VerificationOutput(items=[])),
            SimpleNamespace(
                value=VerificationOutput(
                    items=[
                        VerificationItem(
                            sentence_id="key_findings:0", supported=True, reason="entailed"
                        )
                    ]
                )
            ),
        ]
    )
    monkeypatch.setattr(deps, "predictor", predictor)
    assert state.report is not None
    assert await _verify(state, deps, deps.events, state.report) == {}
    assert state.verification["checked"] == 1
    assert state.verification["unavailable"] == 0
