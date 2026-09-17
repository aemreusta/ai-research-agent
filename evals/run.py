"""Labeled evidence evaluation, independent of report-gate pass rates.

Offline: verify dataset quotes and score the old quote-only admission baseline.
Live: ask the real claim validator, then apply the production temporal policy; expected labels
are never sent to the model. A run that rejects everything fails recall, and missing verdicts
fail availability. Synthetic cases are explicitly labeled, not asserted as real legal facts.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import uuid
from datetime import date
from pathlib import Path

from research_agent.agent.freshness import assess_claim_freshness
from research_agent.agent.quotes import verify_quote
from research_agent.agent.research import build_deps, default_toolkit
from research_agent.agent.runtime import MemoryCache, MemoryCallRecorder, MemoryEventSink
from research_agent.agent.state import Claim, Document, ResearchState, TimeScope
from research_agent.agent.validation import validate_claims
from research_agent.config.loader import load_settings
from research_agent.keys import ProviderKeys


def metrics(rows: list[dict[str, object]], key: str) -> dict[str, float | int]:
    tp = sum(bool(r[key]) and bool(r["expected"]) for r in rows)
    fp = sum(bool(r[key]) and not bool(r["expected"]) for r in rows)
    fn = sum(not bool(r[key]) and bool(r["expected"]) for r in rows)
    return {
        "cases": len(rows),
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "precision": tp / (tp + fp) if tp + fp else 0,
        "recall": tp / (tp + fn) if tp + fn else 0,
        "accuracy": sum(bool(r[key]) == bool(r["expected"]) for r in rows) / len(rows),
    }


async def run(args: argparse.Namespace) -> int:
    started = time.perf_counter()
    cases = json.loads(args.cases.read_text())["cases"]
    rows = []
    toolkit = await default_toolkit(ProviderKeys.resolve()) if args.live else None
    deps = None
    try:
        if toolkit:
            rid = uuid.uuid4()
            deps = await build_deps(
                run_id=rid,
                settings=load_settings().settings,
                toolkit=toolkit,
                events=MemoryEventSink(rid),
                recorder=MemoryCallRecorder(),
                cache=MemoryCache(),
            )
            if not deps.llm.available:
                raise RuntimeError("live evaluation requires an LLM key")
        for item in cases:
            claim = Claim.model_validate(item["claim"])
            document = Document.model_validate(item["document"])
            state = ResearchState(
                run_id=uuid.uuid4(),
                question=item["question"],
                as_of=date.fromisoformat(item["as_of"]),
            )
            state.analysis.time_scope = TimeScope.model_validate(item["time_scope"])
            quote = verify_quote(claim.quote, document.content)
            if not quote.ok:
                raise ValueError(f"Dataset error: {item['id']} has a nonmatching quote")
            row = {
                "id": item["id"],
                "category": item["category"],
                "expected": item["expected_usable"],
                "baseline": quote.ok,
                "expected_entailment": item["expected_entailment"],
            }
            if deps:
                await validate_claims([claim], document, state, deps, deps.events)
                row.update(
                    predicted=claim.validation_status == "supported"
                    and not claim.requires_fresh_confirmation,
                    entailment=claim.validation_status == "supported",
                    availability=claim.validation_status != "unavailable",
                    freshness=claim.freshness,
                    reason=claim.validation_reason,
                )
            else:
                # This is a deterministic-policy check only, not a substitute for a model eval.
                assess_claim_freshness(claim, document, state)
                row.update(temporal_hold=claim.requires_fresh_confirmation)
            rows.append(row)
            print(item["id"], row.get("predicted", "quote verified"), flush=True)
    finally:
        if toolkit:
            await toolkit.aclose()
    result = {
        "mode": "live" if args.live else "offline_dataset_and_baseline",
        "elapsed_seconds": round(time.perf_counter() - started, 2),
        "baseline": metrics(rows, "baseline"),
        "cases": rows,
    }
    passed = True
    if deps:
        measured = metrics(rows, "predicted")
        availability = sum(bool(r["availability"]) for r in rows) / len(rows)
        entailment_accuracy = sum(r["entailment"] == r["expected_entailment"] for r in rows) / len(
            rows
        )
        result.update(
            improved=measured,
            availability=availability,
            entailment_accuracy=entailment_accuracy,
            cost_usd=deps.meter.cost_usd,
            models=deps.llm.models_used,
            prompt_versions=deps.prompts.references(),
            thresholds={
                "precision": 0.95,
                "recall": 0.8,
                "availability": 1.0,
                "entailment_accuracy": 0.9,
            },
        )
        passed = (
            measured["precision"] >= 0.95
            and measured["recall"] >= 0.8
            and availability == 1
            and entailment_accuracy >= 0.9
        )
        result["passed"] = passed
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(
        json.dumps(
            {k: v for k, v in result.items() if k not in {"cases", "prompt_versions"}}, indent=2
        )
    )
    return 0 if passed else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--cases", type=Path, default=Path("evals/cases.json"))
    parser.add_argument("--output", type=Path, default=Path("evals/results/offline.json"))
    raise SystemExit(asyncio.run(run(parser.parse_args())))
