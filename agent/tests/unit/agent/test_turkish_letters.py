"""Turkish reports written in ASCII: detected, rewritten once by synthesis, flagged by G10."""

from __future__ import annotations

import uuid
from datetime import date
from types import SimpleNamespace
from typing import Any, cast

from research_agent.agent.deps import AgentDeps
from research_agent.agent.nodes.report import synthesize
from research_agent.agent.render import render_markdown
from research_agent.agent.runtime import MemoryEventSink
from research_agent.agent.state import (
    ClaimCluster,
    ClusterStatus,
    Document,
    ResearchState,
    SourceScore,
)
from research_agent.agent.text import lacks_turkish_letters
from research_agent.gate import GateConfig
from research_agent.gate.rules import evaluate
from research_agent.prompting.schemas import SentenceOut, SynthesisOutput

ASCII = (
    "Sirket, hukukcular icin yasal kaynaklara dayali guvenilir sonuclar sunan bir yapay zeka "
    "platformudur ve yil sonuna kadar bir yatirim turuna cikmayi planlamaktadir."
)
PROPER = (
    "Şirket, hukukçular için yasal kaynaklara dayalı güvenilir sonuçlar sunan bir yapay zekâ "
    "platformudur ve yıl sonuna kadar bir yatırım turuna çıkmayı planlamaktadır."
)


def test_ascii_turkish_is_detected_and_proper_turkish_is_not() -> None:
    assert lacks_turkish_letters(ASCII)
    assert not lacks_turkish_letters(PROPER)
    assert not lacks_turkish_letters("Sirket yatirim")  # too short to judge


def _state() -> ResearchState:
    doc = Document(
        id="d1",
        url="https://apilex.ai/",
        canonical_url="https://apilex.ai/",
        domain="apilex.ai",
        origin_id="o1",
        title="Apilex",
        score=SourceScore(total=0.6, is_primary=True),
    )
    cluster = ClaimCluster(
        id="k1",
        subq_id="s1",
        claim_ids=["c1"],
        doc_ids=["d1"],
        origin_ids=["o1"],
        statement="Apilex is an AI platform for lawyers.",
        status=ClusterStatus.SINGLE_SOURCE,
    )
    return ResearchState(
        run_id=uuid.uuid4(),
        question="ApilexAI'ın ürünleri nelerdir?",
        language="tr",
        as_of=date(2026, 9, 17),
        documents={"d1": doc},
        clusters={"k1": cluster},
    )


class _Synth:
    def __init__(self, drafts: list[str]) -> None:
        self.drafts = drafts
        self.feedback: list[str] = []

    async def __call__(self, name: str, events: object, **inputs: Any) -> SimpleNamespace:
        self.feedback.append(inputs["feedback"])
        text = self.drafts[len(self.feedback) - 1]
        sentence = SentenceOut(text=text, cluster_ids=["k1"])
        output = SynthesisOutput(
            rationale="r",
            title="Apilex",
            summary=[sentence],
            key_findings=[sentence],
            conflicting=[],
            recommendations=[],
            conclusion=[sentence],
        )
        return SimpleNamespace(value=output)


async def _synthesize(synth: _Synth) -> ResearchState:
    state = _state()
    deps = cast(AgentDeps, SimpleNamespace(predictor=synth, today=date(2026, 9, 17)))
    await synthesize(state, deps, MemoryEventSink(state.run_id, node="synthesize"))
    return state


async def test_an_ascii_turkish_draft_is_rewritten_once() -> None:
    synth = _Synth([ASCII, PROPER])
    state = await _synthesize(synth)
    assert len(synth.feedback) == 2 and "Turkish letters" in synth.feedback[1]
    assert state.report is not None
    assert state.report.sections[0].sentences[0].text == PROPER


async def test_a_proper_turkish_draft_is_kept() -> None:
    synth = _Synth([PROPER])
    await _synthesize(synth)
    assert synth.feedback == ["(none)"]


async def test_g10_flags_a_turkish_report_that_stays_ascii() -> None:
    state = await _synthesize(_Synth([ASCII, ASCII]))
    assert state.report is not None
    g10 = [v for v in evaluate(state.report, state, GateConfig.load()) if v.rule == "G10"]
    assert [v.details["problem"] for v in g10] == ["missing_turkish_letters"]


async def test_the_single_source_label_says_independent() -> None:
    state = await _synthesize(_Synth([PROPER]))
    assert state.report is not None
    assert "tek bağımsız kaynak" in render_markdown(state, metadata={})
