"""Selection changes the actual call, preserves fallback policy and records its true cost."""

import uuid

import pytest
from pydantic import BaseModel

from research_agent.agent.budget import BudgetMeter
from research_agent.agent.runtime import MemoryCallRecorder, MemoryEventSink
from research_agent.config.loader import ConfigError, load_settings
from research_agent.config.schema import BudgetSettings, LlmSettings
from research_agent.errors import ErrorCode
from research_agent.providers.llm.base import ProviderError
from research_agent.providers.llm.catalog import Tier, catalog
from research_agent.providers.llm.fake import FakeLLMProvider
from research_agent.providers.llm.gateway import LLMFailure, LLMGateway, LLMKeysRejected


class Answer(BaseModel):
    value: int


@pytest.mark.parametrize(
    "model,provider",
    [
        ("gemini-3.1-pro-preview", "gemini"),
        ("gpt-5.6-sol", "openai"),
    ],
)
async def test_selected_model_is_called_and_costed(model: str, provider: str) -> None:
    events = MemoryEventSink(uuid.uuid4())
    recorder = MemoryCallRecorder()
    gateway = LLMGateway(
        providers={
            name: FakeLLMProvider(name, script={"Answer": {"value": 7}})
            for name in ["gemini", "openai"]
        },
        catalog=catalog(),
        settings=LlmSettings(reasoning_model=model),
        recorder=recorder,
        meter=BudgetMeter(BudgetSettings()),
    )
    result = await gateway.generate(
        Answer, system="s", user="u", tier=Tier.REASONING, events=events
    )
    assert result.model == model and result.provider == provider
    assert recorder.llm_calls[0].model == model
    assert result.cost_usd == catalog().cost(model, result.tokens_in, result.tokens_out)
    assert result.fallback_from is None


@pytest.mark.parametrize("fallback", [True, False])
async def test_selection_controls_fallback_without_changing_the_other_tier(fallback: bool) -> None:
    events = MemoryEventSink(uuid.uuid4())
    gemini = FakeLLMProvider("gemini", script={"Answer": {"value": 7}})
    openai = FakeLLMProvider(
        "openai",
        script={
            "Answer": ProviderError(ErrorCode.LLM_PROVIDER_ERROR, "openai", "unavailable", False)
        },
    )
    gateway = LLMGateway(
        providers={"gemini": gemini, "openai": openai},
        catalog=catalog(),
        settings=LlmSettings(reasoning_model="gpt-5.6-sol", allow_fallback=fallback, retries=0),
        recorder=MemoryCallRecorder(),
        meter=BudgetMeter(BudgetSettings()),
    )
    if fallback:
        result = await gateway.generate(
            Answer, system="s", user="u", tier=Tier.REASONING, events=events
        )
        assert result.provider == "gemini" and result.fallback_from == "openai"
    else:
        with pytest.raises(LLMFailure):
            await gateway.generate(Answer, system="s", user="u", tier=Tier.REASONING, events=events)
        assert not gemini.calls
    fast = await gateway.generate(Answer, system="s", user="u", tier=Tier.FAST, events=events)
    assert fast.model == "gemini-3.1-flash-lite"


async def test_missing_selected_key_is_not_silently_substituted() -> None:
    events = MemoryEventSink(uuid.uuid4())
    gemini = FakeLLMProvider("gemini", script={"Answer": {"value": 7}})
    gateway = LLMGateway(
        providers={"gemini": gemini},
        catalog=catalog(),
        settings=LlmSettings(reasoning_model="gpt-5.6-sol"),
        recorder=MemoryCallRecorder(),
        meter=BudgetMeter(BudgetSettings()),
    )
    with pytest.raises(LLMKeysRejected) as exc:
        await gateway.generate(Answer, system="s", user="u", tier=Tier.REASONING, events=events)
    assert "openai key" in (exc.value.error.outcome or "")
    assert not gemini.calls


def test_selection_is_validated_and_included_in_config_hash() -> None:
    default = load_settings()
    selected = load_settings(overrides={"llm.reasoning_model": "gemini-3.1-pro-preview"})
    assert selected.snapshot["llm"]["reasoning_model"] == "gemini-3.1-pro-preview"
    assert selected.config_hash != default.config_hash
    with pytest.raises(ConfigError, match="unknown research model"):
        load_settings(overrides={"llm.fast_model": "not-a-real-model"})


def test_pro_long_context_pricing_boundary() -> None:
    models = catalog()
    assert models.cost("gemini-3.1-pro-preview", 200_000, 1000) == pytest.approx(0.412)
    assert models.cost("gemini-3.1-pro-preview", 200_001, 1000) == pytest.approx(0.818004)
