"""Tiny live structured-output probes of explicit model selection; never falls back."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import uuid
from pathlib import Path

from pydantic import BaseModel

from research_agent.agent.budget import BudgetMeter
from research_agent.agent.research import default_toolkit
from research_agent.agent.runtime import MemoryCallRecorder, MemoryEventSink
from research_agent.config.schema import BudgetSettings, LlmSettings
from research_agent.errors import AgentException
from research_agent.keys import ProviderKeys
from research_agent.providers.llm.catalog import Tier, catalog
from research_agent.providers.llm.gateway import LLMGateway


class Probe(BaseModel):
    answer: int


async def main(args: argparse.Namespace) -> int:
    toolkit = await default_toolkit(ProviderKeys.resolve())
    rows = []
    try:
        for model in args.models:
            events = MemoryEventSink(uuid.uuid4())
            meter = BudgetMeter(BudgetSettings())
            gateway = LLMGateway(
                providers=toolkit.llm,
                catalog=catalog(),
                settings=LlmSettings(reasoning_model=model, allow_fallback=False, retries=0),
                meter=meter,
                recorder=MemoryCallRecorder(),
            )
            started = time.perf_counter()
            try:
                result = await gateway.generate(
                    Probe,
                    system="Return the exact arithmetic result.",
                    user="What is 7 times 8?",
                    tier=Tier.REASONING,
                    events=events,
                )
                row = {
                    "requested": model,
                    "actual": result.model,
                    "provider": result.provider,
                    "passed": result.value.answer == 56 and result.model == model,
                    "cost_usd": result.cost_usd,
                }
            except AgentException as exc:
                row = {
                    "requested": model,
                    "passed": False,
                    "error_code": str(exc.error.code),
                    "detail": exc.error.outcome or exc.error.decision,
                }
            row["elapsed_seconds"] = round(time.perf_counter() - started, 2)
            rows.append(row)
            print(json.dumps(row), flush=True)
    finally:
        await toolkit.aclose()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(rows, indent=2) + "\n")
    return 0 if rows and all(row["passed"] for row in rows) else 1


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("models", nargs="+", choices=list(catalog().choices))
    p.add_argument("--out", type=Path, default=Path("evals/results/model-smoke.json"))
    raise SystemExit(asyncio.run(main(p.parse_args())))
