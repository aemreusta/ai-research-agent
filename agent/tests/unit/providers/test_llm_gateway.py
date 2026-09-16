"""The gateway's decision table: retry, fall back, repair, give up - and account for all of it."""

from __future__ import annotations

import uuid

import pytest
from pydantic import BaseModel

from research_agent.agent.budget import BudgetMeter
from research_agent.agent.runtime import MemoryCallRecorder, MemoryEventSink
from research_agent.config.schema import BudgetSettings, LlmSettings
from research_agent.errors import ErrorCode
from research_agent.providers.llm.base import ProviderError
from research_agent.providers.llm.catalog import Tier, catalog
from research_agent.providers.llm.fake import FakeLLMProvider
from research_agent.providers.llm.gateway import LLMFailure, LLMGateway


class Answer(BaseModel):
    rationale: str
    value: int


GOOD = {"rationale": "because", "value": 7}


def _error(code: ErrorCode, *, retryable: bool, provider: str = "gemini") -> ProviderError:
    return ProviderError(code, provider, "boom", retryable=retryable)


class Harness:
    def __init__(self, *providers: FakeLLMProvider, retries: int = 2) -> None:
        self.events = MemoryEventSink(uuid.uuid4(), node="plan", iteration=1)
        self.recorder = MemoryCallRecorder()
        self.meter = BudgetMeter(BudgetSettings())
        self.sleeps: list[float] = []

        async def sleep(seconds: float) -> None:
            self.sleeps.append(seconds)

        self.gateway = LLMGateway(
            providers={p.name: p for p in providers},
            catalog=catalog(),
            settings=LlmSettings(retries=retries, repair_retries=1),
            recorder=self.recorder,
            meter=self.meter,
            sleep=sleep,
        )

    async def ask(self) -> object:
        return await self.gateway.generate(
            Answer,
            system="s",
            user="u",
            tier=Tier.FAST,
            events=self.events,
            prompt_id="test",
            prompt_version="1",
        )

    def codes(self) -> list[str]:
        return [str(e["error_code"]) for e in self.events.events if e.get("error_code")]


async def test_a_clean_answer_is_parsed_costed_and_recorded() -> None:
    h = Harness(FakeLLMProvider("gemini", script={"Answer": GOOD}))
    result = await h.ask()
    assert result.value == Answer(**GOOD)  # type: ignore[attr-defined]
    assert result.provider == "gemini"  # type: ignore[attr-defined]
    assert result.model == "gemini-3.1-flash-lite"  # type: ignore[attr-defined]
    assert h.meter.llm_calls == 1 and h.meter.cost_usd > 0
    assert [r.status for r in h.recorder.llm_calls] == ["ok"]
    assert h.recorder.llm_calls[0].prompt_id == "test"
    assert h.gateway.models_used == {"fast": "gemini:gemini-3.1-flash-lite"}


async def test_retryable_failures_are_retried_with_backoff() -> None:
    fake = FakeLLMProvider(
        "gemini",
        script={
            "Answer": [
                _error(ErrorCode.LLM_TIMEOUT, retryable=True),
                _error(ErrorCode.LLM_RATE_LIMIT, retryable=True),
                GOOD,
            ]
        },
    )
    h = Harness(fake)
    result = await h.ask()
    assert result.attempts == 3  # type: ignore[attr-defined]
    assert h.sleeps == [0.5, 1.0]
    assert h.codes() == ["LLM_TIMEOUT", "LLM_RATE_LIMIT"]
    assert [r.status for r in h.recorder.llm_calls] == ["error", "error", "ok"]


async def test_auth_failure_falls_back_without_retrying() -> None:
    gemini = FakeLLMProvider(
        "gemini", script={"Answer": _error(ErrorCode.LLM_AUTH, retryable=False)}
    )
    openai = FakeLLMProvider("openai", script={"Answer": GOOD})
    h = Harness(gemini, openai)
    result = await h.ask()

    assert result.provider == "openai"  # type: ignore[attr-defined]
    assert result.fallback_from == "gemini"  # type: ignore[attr-defined]
    assert len(gemini.calls) == 1, "a rejected key must not be retried"
    assert h.codes() == ["LLM_AUTH", "LLM_FALLBACK_USED"]
    auth_event = h.events.events[0]
    assert auth_event["data"]["decision"] == "fallback->openai"
    assert h.recorder.llm_calls[-1].fallback_from == "gemini"


async def test_exhausted_retries_fall_back_to_the_next_provider() -> None:
    gemini = FakeLLMProvider(
        "gemini", script={"Answer": _error(ErrorCode.LLM_PROVIDER_ERROR, retryable=True)}
    )
    openai = FakeLLMProvider("openai", script={"Answer": GOOD})
    h = Harness(gemini, openai, retries=1)
    result = await h.ask()
    assert len(gemini.calls) == 2
    assert result.provider == "openai"  # type: ignore[attr-defined]


async def test_invalid_output_gets_exactly_one_repair_turn() -> None:
    fake = FakeLLMProvider("gemini", script={"Answer": ["not json at all", GOOD]})
    h = Harness(fake)
    result = await h.ask()

    assert result.repaired is True  # type: ignore[attr-defined]
    repair_turn = fake.calls[1][1]
    assert repair_turn[-1].role == "user"
    assert "could not be used" in repair_turn[-1].content
    assert repair_turn[-2].role == "assistant"
    assert h.codes() == ["LLM_INVALID_OUTPUT"]
    # Both calls cost money and both are counted.
    assert h.meter.llm_calls == 2


async def test_a_failed_repair_raises_for_the_node_fallback() -> None:
    fake = FakeLLMProvider("gemini", script={"Answer": ['{"value": "x"}', '{"still": "wrong"}']})
    h = Harness(fake)
    with pytest.raises(LLMFailure) as caught:
        await h.ask()
    assert caught.value.error.code is ErrorCode.LLM_REPAIR_FAILED
    assert len(fake.calls) == 2, "never a third attempt at a malformed answer"


async def test_schema_violations_are_quoted_back_to_the_model() -> None:
    fake = FakeLLMProvider(
        "gemini", script={"Answer": ['{"rationale": "r", "value": "seven"}', GOOD]}
    )
    h = Harness(fake)
    await h.ask()
    assert "value" in fake.calls[1][1][-1].content


async def test_every_provider_failing_is_one_coded_failure() -> None:
    gemini = FakeLLMProvider(
        "gemini", script={"Answer": _error(ErrorCode.LLM_AUTH, retryable=False)}
    )
    openai = FakeLLMProvider(
        "openai", script={"Answer": _error(ErrorCode.LLM_AUTH, retryable=False, provider="openai")}
    )
    h = Harness(gemini, openai)
    with pytest.raises(LLMFailure) as caught:
        await h.ask()
    assert caught.value.error.code is ErrorCode.LLM_AUTH
    assert caught.value.error.decision == "every provider in the chain failed"


async def test_no_configured_provider_is_an_actionable_error() -> None:
    h = Harness()
    with pytest.raises(LLMFailure) as caught:
        await h.ask()
    assert "Settings" in (caught.value.error.outcome or "")


async def test_the_chain_order_comes_from_the_catalog_not_the_dict() -> None:
    openai = FakeLLMProvider("openai", script={"Answer": GOOD})
    gemini = FakeLLMProvider("gemini", script={"Answer": GOOD})
    h = Harness(openai, gemini)
    result = await h.ask()
    assert result.provider == "gemini"  # type: ignore[attr-defined]


async def test_the_llm_call_event_carries_tokens_and_cost() -> None:
    h = Harness(FakeLLMProvider("gemini", script={"Answer": GOOD}))
    await h.ask()
    call = next(e for e in h.events.events if e["event_type"] == "llm_called")
    assert call["tokens_in"] > 0 and call["cost_usd"] > 0
