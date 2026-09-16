"""`LLMGateway`: the one door every model call goes through.

For each call, in order:

1. walk the provider chain (`models.yaml`), skipping providers without a key;
2. per provider, retry retryable failures with exponential backoff, then move on
   (`LLM_TIMEOUT` / `LLM_RATE_LIMIT` / `LLM_PROVIDER_ERROR` -> retry; `LLM_AUTH` -> next);
3. parse into the node's Pydantic model; on failure, one repair turn quoting the validation
   error (`LLM_INVALID_OUTPUT`), then give up (`LLM_REPAIR_FAILED`) - the node's deterministic
   fallback takes it from there;
4. record every attempt in `llm_calls`, add tokens and cost to the run's meter, and put each
   decision on the timeline as error -> decision -> outcome.

A fallback that saved the call is itself an event (`LLM_FALLBACK_USED`): a run that quietly
switched model is exactly what a reviewer needs to see.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel

from research_agent.agent.budget import BudgetMeter
from research_agent.agent.runtime import CallRecorder, EventSink, LlmCallRecord
from research_agent.config.schema import LlmSettings
from research_agent.errors import AgentError, AgentException, ErrorCode
from research_agent.observability.events import EventType, new_span_id
from research_agent.observability.logging import best_effort
from research_agent.providers.llm.base import Completion, LLMProvider, Message, ProviderError
from research_agent.providers.llm.catalog import ModelCatalog, Tier
from research_agent.providers.llm.schema import inline_refs, strict_json_schema
from research_agent.providers.llm.structured import (
    OutputParseError,
    parse_output,
    repair_instruction,
)

Sleep = Callable[[float], Awaitable[None]]


class Tracer(Protocol):
    """Receives every successful generation (Langfuse implements this)."""

    async def generation(
        self,
        *,
        name: str,
        model: str,
        provider: str,
        messages: list[Message],
        output: str,
        tokens_in: int,
        tokens_out: int,
        cost_usd: float,
        latency_ms: int,
        prompt_id: str | None,
        prompt_version: str | None,
        metadata: dict[str, Any],
    ) -> None: ...


class LLMFailure(AgentException):
    """No provider produced a usable answer. Nodes catch this and use their fallback."""


@dataclass(frozen=True, slots=True)
class LLMResult[T: BaseModel]:
    value: T
    provider: str
    model: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    latency_ms: int
    attempts: int
    repaired: bool
    fallback_from: str | None


class LLMGateway:
    def __init__(
        self,
        *,
        providers: dict[str, LLMProvider],
        catalog: ModelCatalog,
        settings: LlmSettings,
        recorder: CallRecorder,
        meter: BudgetMeter,
        tracer: Tracer | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        # Chain order from the catalog; providers without a key were never constructed.
        self._chain = [name for name in catalog.chain if name in providers]
        self._providers = providers
        self._catalog = catalog
        self._settings = settings
        self._recorder = recorder
        self._meter = meter
        self._tracer = tracer
        self._sleep = sleep
        self.models_used: dict[str, str] = {}

    @property
    def available(self) -> list[str]:
        return list(self._chain)

    @property
    def providers(self) -> dict[str, LLMProvider]:
        return dict(self._providers)

    async def generate[T: BaseModel](
        self,
        output: type[T],
        *,
        system: str,
        user: str,
        tier: Tier,
        events: EventSink,
        prompt_id: str | None = None,
        prompt_version: str | None = None,
    ) -> LLMResult[T]:
        if not self._chain:
            raise LLMFailure(
                AgentError(
                    code=ErrorCode.LLM_AUTH,
                    node=events.node,
                    decision="no LLM provider has a key",
                    outcome="add a Gemini or OpenAI key in Settings",
                )
            )

        schema = inline_refs(strict_json_schema(output))
        messages = [Message("system", system), Message("user", user)]
        first = self._chain[0]
        last_error: ProviderError | None = None

        for index, provider_name in enumerate(self._chain):
            model = self._catalog.model_for(tier, provider_name)
            if model is None:
                continue
            next_provider = self._chain[index + 1] if index + 1 < len(self._chain) else None
            try:
                result = await self._with_provider(
                    output,
                    provider_name=provider_name,
                    model=model,
                    tier=tier,
                    messages=messages,
                    schema=schema,
                    events=events,
                    prompt_id=prompt_id,
                    prompt_version=prompt_version,
                    fallback_from=first if provider_name != first else None,
                    next_provider=next_provider,
                )
            except ProviderError as exc:
                last_error = exc
                continue

            self.models_used[tier.value] = f"{provider_name}:{model}"
            if provider_name != first:
                await events.error(
                    AgentError(
                        code=ErrorCode.LLM_FALLBACK_USED,
                        node=events.node,
                        decision=f"fallback {first}->{provider_name}",
                        outcome=f"OK via {model}",
                        provider=provider_name,
                    )
                )
            return result

        code = last_error.code if last_error else ErrorCode.LLM_PROVIDER_ERROR
        failure = AgentError(
            code=code,
            node=events.node,
            decision="every provider in the chain failed",
            outcome=str(last_error) if last_error else None,
            provider=last_error.provider if last_error else None,
        )
        # The node's own fallback takes over; the timeline says so.
        await events.error(
            failure.model_copy(update={"decision": "use the node's deterministic fallback"})
        )
        raise LLMFailure(failure)

    async def _with_provider[T: BaseModel](
        self,
        output: type[T],
        *,
        provider_name: str,
        model: str,
        tier: Tier,
        messages: list[Message],
        schema: dict[str, Any],
        events: EventSink,
        prompt_id: str | None,
        prompt_version: str | None,
        fallback_from: str | None,
        next_provider: str | None,
    ) -> LLMResult[T]:
        provider = self._providers[provider_name]
        params = self._catalog.params_for(tier, provider_name)
        attempts = 0
        max_attempts = self._settings.retries + 1
        conversation = list(messages)
        repairs_left = self._settings.repair_retries
        repaired = False
        totals = [0, 0, 0.0, 0]  # tokens_in, tokens_out, cost, latency

        while True:
            attempts += 1
            span_id = new_span_id()
            try:
                completion = await provider.complete(
                    model=model,
                    messages=conversation,
                    json_schema=schema,
                    schema_name=output.__name__,
                    params=params,
                    timeout=float(self._settings.timeout_seconds),
                )
            except ProviderError as exc:
                await self._record(
                    span_id,
                    events,
                    provider_name,
                    model,
                    tier,
                    prompt_id,
                    prompt_version,
                    attempts,
                    fallback_from,
                    "error",
                    exc.code.value,
                    None,
                    conversation,
                )
                if exc.retryable and attempts < max_attempts:
                    delay = min(8.0, 0.5 * 2 ** (attempts - 1))
                    await events.error(
                        AgentError(
                            code=exc.code,
                            node=events.node,
                            decision=f"retry in {delay:.1f}s",
                            outcome=f"attempt {attempts}/{max_attempts}",
                            provider=provider_name,
                            attempt=attempts,
                            cause=exc.message,
                        )
                    )
                    await self._sleep(delay)
                    continue
                await events.error(
                    AgentError(
                        code=exc.code,
                        node=events.node,
                        decision=f"fallback->{next_provider}" if next_provider else "give up",
                        outcome=f"{provider_name} failed after {attempts} attempt(s)",
                        provider=provider_name,
                        attempt=attempts,
                        cause=exc.message,
                    )
                )
                raise

            cost = self._catalog.cost(model, completion.tokens_in, completion.tokens_out)
            self._meter.add_llm(
                tokens_in=completion.tokens_in, tokens_out=completion.tokens_out, cost_usd=cost
            )
            totals[0] += completion.tokens_in
            totals[1] += completion.tokens_out
            totals[2] += cost
            totals[3] += completion.latency_ms

            try:
                value = parse_output(output, completion.text)
            except OutputParseError as exc:
                await self._record(
                    span_id,
                    events,
                    provider_name,
                    model,
                    tier,
                    prompt_id,
                    prompt_version,
                    attempts,
                    fallback_from,
                    "invalid",
                    ErrorCode.LLM_INVALID_OUTPUT.value,
                    completion,
                    conversation,
                    cost,
                )
                if repairs_left > 0:
                    repairs_left -= 1
                    repaired = True
                    await events.error(
                        AgentError(
                            code=ErrorCode.LLM_INVALID_OUTPUT,
                            node=events.node,
                            decision="repair with the validation error",
                            outcome="retrying once",
                            provider=provider_name,
                            attempt=attempts,
                            cause=str(exc)[:500],
                        )
                    )
                    conversation = [
                        *conversation,
                        Message("assistant", completion.text[:4000]),
                        Message("user", repair_instruction(exc)),
                    ]
                    continue
                failure = AgentError(
                    code=ErrorCode.LLM_REPAIR_FAILED,
                    node=events.node,
                    decision="use the node's deterministic fallback",
                    outcome=str(exc)[:300],
                    provider=provider_name,
                    attempt=attempts,
                )
                await events.error(failure)
                raise LLMFailure(failure) from exc

            await self._record(
                span_id,
                events,
                provider_name,
                model,
                tier,
                prompt_id,
                prompt_version,
                attempts,
                fallback_from,
                "ok",
                None,
                completion,
                conversation,
                cost,
            )
            if self._tracer is not None:
                await self._trace(
                    output.__name__,
                    model,
                    provider_name,
                    conversation,
                    completion,
                    cost,
                    prompt_id,
                    prompt_version,
                    events,
                    tier,
                )
            return LLMResult(
                value=value,
                provider=provider_name,
                model=model,
                tokens_in=int(totals[0]),
                tokens_out=int(totals[1]),
                cost_usd=float(totals[2]),
                latency_ms=int(totals[3]),
                attempts=attempts,
                repaired=repaired,
                fallback_from=fallback_from,
            )

    async def _record(
        self,
        span_id: str,
        events: EventSink,
        provider: str,
        model: str,
        tier: Tier,
        prompt_id: str | None,
        prompt_version: str | None,
        attempt: int,
        fallback_from: str | None,
        status: str,
        error_code: str | None,
        completion: Completion | None,
        conversation: list[Message],
        cost: float = 0.0,
    ) -> None:
        record = LlmCallRecord(
            run_id=events.run_id,
            span_id=span_id,
            node=events.node,
            iteration=events.iteration,
            provider=provider,
            model=model,
            tier=tier.value,
            prompt_id=prompt_id,
            prompt_version=prompt_version,
            tokens_in=completion.tokens_in if completion else 0,
            tokens_out=completion.tokens_out if completion else 0,
            cost_usd=cost,
            latency_ms=completion.latency_ms if completion else 0,
            attempt=attempt,
            fallback_from=fallback_from,
            status=status,
            error_code=error_code,
            # Only the tail of the conversation: enough to debug, not a copy of every document.
            request={
                "messages": [
                    {"role": m.role, "content": m.content[:2000]} for m in conversation[-2:]
                ]
            },
            response={"text": completion.text[:4000]} if completion else None,
        )
        with best_effort("recording an llm call"):
            await self._recorder.llm_call(record)
        if completion is not None:
            await events.debug(
                EventType.LLM_CALLED,
                f"{provider}:{model} {status} ({completion.tokens_in}+{completion.tokens_out} "
                f"tokens, ${cost:.5f}, {completion.latency_ms} ms)",
                data={
                    "provider": provider,
                    "model": model,
                    "tier": tier.value,
                    "prompt": prompt_id,
                    "status": status,
                },
                latency_ms=completion.latency_ms,
                tokens_in=completion.tokens_in,
                tokens_out=completion.tokens_out,
                cost_usd=cost,
            )

    async def _trace(
        self,
        name: str,
        model: str,
        provider: str,
        conversation: list[Message],
        completion: Completion,
        cost: float,
        prompt_id: str | None,
        prompt_version: str | None,
        events: EventSink,
        tier: Tier,
    ) -> None:
        assert self._tracer is not None
        with best_effort("tracing a generation"):
            await self._tracer.generation(
                name=f"{events.node}:{name}",
                model=model,
                provider=provider,
                messages=conversation,
                output=completion.text,
                tokens_in=completion.tokens_in,
                tokens_out=completion.tokens_out,
                cost_usd=cost,
                latency_ms=completion.latency_ms,
                prompt_id=prompt_id,
                prompt_version=prompt_version,
                metadata={"tier": tier.value, "node": events.node, "iteration": events.iteration},
            )
