"""Execute a bounded live case suite through the real HTTP/dispatcher/agent path and export it.

Polls only compact run state; a stopped script can resume by reusing manifest.json. Each case
keeps its run ID, wall time and reported model cost. Full exports remain auditable separately.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any

import httpx


async def main(args: argparse.Namespace) -> None:
    cases = json.loads(args.cases.read_text())
    args.out.mkdir(parents=True, exist_ok=True)
    manifest_path = args.out / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}

    def save() -> None:
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")

    semaphore = asyncio.Semaphore(args.concurrency)
    async with httpx.AsyncClient(
        base_url=args.api, timeout=30, limits=httpx.Limits(keepalive_expiry=2)
    ) as client:

        async def get(path: str, **kwargs: Any) -> httpx.Response:
            for attempt in range(4):
                try:
                    return await client.get(path, **kwargs)
                except httpx.TransportError:
                    if attempt == 3:
                        raise
                    await asyncio.sleep(attempt + 1)
            raise AssertionError("unreachable")

        async def one(case: dict[str, Any]) -> None:
            async with semaphore:
                slug = case["slug"]
                if slug in manifest and manifest[slug].get("exported"):
                    print(slug, "already exported", flush=True)
                    return
                if slug not in manifest:
                    response = await client.post(
                        "/api/runs",
                        json={
                            "question": case["question"],
                            "overrides": case.get("overrides", {}),
                        },
                    )
                    response.raise_for_status()
                    manifest[slug] = {
                        "run_id": response.json()["run_id"],
                        "question": case["question"],
                        "started": time.time(),
                    }
                    save()
                item = manifest[slug]
                rid = item["run_id"]
                print(slug, rid, "running", flush=True)
                while True:
                    response = await get(f"/api/runs/{rid}")
                    response.raise_for_status()
                    run = response.json()
                    if run["status"] not in {"queued", "dispatched", "running"}:
                        break
                    await asyncio.sleep(5)
                item.update(
                    status=run["status"],
                    elapsed_seconds=round(time.time() - item["started"], 2),
                    gate_status=run.get("gate_status"),
                    stop_reason=run.get("stop_reason"),
                    metrics={
                        key: run.get(key)
                        for key in [
                            "cost_usd",
                            "tokens_in",
                            "tokens_out",
                            "searches_used",
                            "iteration",
                            "duration_seconds",
                        ]
                    },
                    error_code=run.get("error_code"),
                    attempts=run.get("attempts"),
                    models_used=run.get("models_used"),
                    selected_models={
                        key: run.get("config_snapshot", {}).get("llm", {}).get(key)
                        for key in ["reasoning_model", "fast_model", "allow_fallback"]
                    },
                )
                folder = args.out / slug
                folder.mkdir(exist_ok=True)
                (folder / "input.md").write_text(
                    f"# Input\n\n{case['question']}\n\nRun: {rid}\nMode: live API\n"
                )
                for artifact, filename in [
                    ("report", "report.md"),
                    ("report_json", "report.json"),
                    ("state", "state.json"),
                    ("gate", "gate_result.json"),
                    ("trace", "trace.jsonl"),
                ]:
                    export = await get(f"/api/runs/{rid}/export", params={"artifact": artifact})
                    if export.status_code == 200:
                        (folder / filename).write_text(export.text)
                    elif export.status_code != 404:
                        export.raise_for_status()
                item["exported"] = True
                save()
                print(
                    slug, item["status"], item["elapsed_seconds"], item["gate_status"], flush=True
                )

        await asyncio.gather(*(one(case) for case in cases))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--api", default="http://localhost:8000")
    p.add_argument("--cases", type=Path, default=Path("evals/live_research_cases.json"))
    p.add_argument("--out", type=Path, default=Path("examples/2026-09-17"))
    p.add_argument("--concurrency", type=int, default=2)
    asyncio.run(main(p.parse_args()))
