"""`research` - run the agent core without the service layer.

Used to produce the examples in `examples/`, to calibrate thresholds and to debug (D30). The UI is
the product surface; this is the developer's door to the same code.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from typing import Any

from research_agent.config import load_settings, tunable_fields
from research_agent.config.loader import ConfigError
from research_agent.contracts import error_code_specs, run_state_machine


def _parse_override(item: str) -> tuple[str, Any]:
    path, sep, raw = item.partition("=")
    if not sep:
        raise argparse.ArgumentTypeError(f"expected path=value, got {item!r}")
    try:
        return path, json.loads(raw)
    except json.JSONDecodeError:
        return path, raw  # bare strings such as level=DEBUG


def _cmd_config(args: argparse.Namespace) -> int:
    overrides = dict(args.override or [])
    try:
        effective = load_settings(overrides=overrides)
    except ConfigError as exc:
        print(f"{exc.error.code.value}: {exc.error.decision}", file=sys.stderr)
        if exc.error.cause:
            print(exc.error.cause, file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({"hash": effective.config_hash, "settings": effective.snapshot}, indent=2))
        return 0
    print(f"config_hash: {effective.config_hash}")
    for row in tunable_fields():
        value = effective.snapshot
        for part in row.path.split("."):
            value = value[part]
        source = effective.source_of(row.path)
        marker = "" if source == "yaml" else f"  ({source})"
        print(f"  {row.path:<55} {value!r}{marker}")
    return 0


def _cmd_contracts(_args: argparse.Namespace) -> int:
    machine = run_state_machine()
    print(f"run states (initial={machine.initial}, max_attempts={machine.max_attempts}):")
    for status, state in machine.states.items():
        targets = ", ".join(state.transitions) or "-"
        print(f"  {status:<25} owner={state.owner:<10} -> {targets}")
    specs = error_code_specs()
    print(f"\nerror codes ({len(specs)}):")
    for name, code_spec in specs.items():
        flags = "expected" if code_spec.expected else "BUG"
        retry = ", retryable" if code_spec.retryable else ""
        print(f"  {name:<28} {code_spec.category:<8} [{flags}{retry}]")
    return 0


def _slug(text: str) -> str:
    import re
    import unicodedata

    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")[:60] or "run"


def _cmd_run(args: argparse.Namespace) -> int:
    import asyncio

    return asyncio.run(_run(args))


async def _run(args: argparse.Namespace) -> int:
    """The agent core without the service layer: in-memory events, no database (D30)."""
    import uuid
    from pathlib import Path

    from langgraph.checkpoint.memory import InMemorySaver

    from research_agent.agent import research
    from research_agent.agent.runtime import MemoryCache, MemoryCallRecorder, MemoryEventSink
    from research_agent.keys import ProviderKeys
    from research_agent.observability.logging import configure_logging

    overrides = dict(args.override or [])
    try:
        effective = load_settings(overrides=overrides)
    except ConfigError as exc:
        print(f"{exc.error.code.value}: {exc.error.cause or exc.error.decision}", file=sys.stderr)
        return 2
    configure_logging(
        effective.settings.logging.model_copy(update={"level": "WARNING"}), service="cli"
    )

    def echo(event: dict[str, Any]) -> None:
        if args.quiet or event["level"] == "debug":
            return
        line = event["data"].get("display") or f"[{event['node']}] {event['message']}"
        marker = {"warn": "! ", "error": "x "}.get(event["level"], "  ")
        print(f"{marker}{line}", flush=True)

    if args.persist:
        return await _run_persisted(args, effective, overrides, echo)

    run_id = uuid.uuid4()
    events = MemoryEventSink(run_id, echo=echo)
    if args.simulate:
        from research_agent.agent.simulated import simulated_toolkit

        toolkit = simulated_toolkit()
    else:
        toolkit = await research.default_toolkit(ProviderKeys.resolve())
    try:
        deps = await research.build_deps(
            run_id=run_id,
            settings=effective.settings,
            toolkit=toolkit,
            events=events,
            recorder=MemoryCallRecorder(),
            cache=MemoryCache(),
        )
        if (problem := research.preflight(deps)) is not None:
            print(f"{problem.code.value}: {problem.outcome}", file=sys.stderr)
            return 1
        state = await research.execute(
            deps, run_id=run_id, question=args.question, checkpointer=InMemorySaver()
        )
    finally:
        await toolkit.aclose()

    metadata = research.metadata_for(deps, effective.settings)
    artifacts = research.artifacts_for(state, metadata)
    out = Path(args.out) if args.out else Path("examples") / _slug(args.question)
    search_label = (
        "built-in offline corpus (3 pages)"
        if args.simulate
        else ", ".join(metadata["search_providers"])
    )
    out.mkdir(parents=True, exist_ok=True)
    (out / "input.md").write_text(
        f"# Input\n\n**Question:** {state.question}\n\n"
        f"- Date: {deps.today.isoformat()}\n"
        f"- Mode: {'simulated (offline)' if args.simulate else 'live providers'}\n"
        f"- LLM chain: {', '.join(metadata['llm_providers'])}\n"
        f"- Search: {search_label}\n"
        f"- Overrides: {json.dumps(overrides, ensure_ascii=False) if overrides else 'none'}\n"
        f"- Config hash: `{effective.config_hash}`\n",
        encoding="utf-8",
    )
    (out / "report.md").write_text(artifacts["report_md"][1], encoding="utf-8")
    (out / "report.json").write_text(artifacts["report_json"][1], encoding="utf-8")
    (out / "gate_result.json").write_text(artifacts["gate_result"][1], encoding="utf-8")
    (out / "state.json").write_text(artifacts["state"][1], encoding="utf-8")
    (out / "trace.jsonl").write_text(events.jsonl(), encoding="utf-8")
    verdict = (state.gate_result or {}).get("verdict")
    print(
        f"\nstop_reason={state.stop_reason} gate={verdict} "
        f"cost=${deps.meter.cost_usd:.4f} -> {out}/",
        file=sys.stderr,
    )
    return 0


async def _run_persisted(
    args: argparse.Namespace, effective: Any, overrides: dict[str, Any], echo: Any
) -> int:
    """Same run, recorded in the database so it shows up in the UI (timeline, costs, ledger).

    The dispatcher's watchdog would requeue a run without a heartbeat, so the CLI beats too.
    """
    import asyncio

    from langgraph.checkpoint.memory import InMemorySaver

    from research_agent.agent import research
    from research_agent.agent.runtime import DbCache, DbCallRecorder
    from research_agent.contracts import RunStatus
    from research_agent.db import session as db
    from research_agent.db.models import RunArtifact
    from research_agent.db.repository import RunRepository
    from research_agent.keys import ProviderKeys
    from research_agent.observability.events import EventWriter
    from research_agent.pii.masking import RegexMasker

    sessionmaker = db.session_factory()
    masked = RegexMasker().mask(args.question)
    async with sessionmaker() as session:
        run = await RunRepository(session).create_local(
            question_masked=masked.text,
            config_snapshot=effective.snapshot,
            config_hash=effective.config_hash,
            overrides=overrides,
        )
    run_id = run.id
    events = EventWriter(sessionmaker, run_id=run_id, node="agent")

    async def heartbeat() -> None:
        while True:
            await asyncio.sleep(5)
            async with sessionmaker() as session:
                await RunRepository(session).heartbeat(run_id)

    beating = asyncio.create_task(heartbeat())
    if args.simulate:
        from research_agent.agent.simulated import simulated_toolkit

        toolkit = simulated_toolkit()
    else:
        toolkit = await research.default_toolkit(ProviderKeys.resolve())
    try:
        deps = await research.build_deps(
            run_id=run_id,
            settings=effective.settings,
            toolkit=toolkit,
            events=events,
            recorder=DbCallRecorder(sessionmaker),
            cache=DbCache(sessionmaker),
        )

        async def progress(counters: dict[str, Any]) -> None:
            async with sessionmaker() as session:
                await RunRepository(session).update_counters(run_id, **counters)

        deps.report_progress = progress
        state = await research.execute(
            deps, run_id=run_id, question=masked.text, checkpointer=InMemorySaver()
        )
        metadata = research.metadata_for(deps, effective.settings)
        async with sessionmaker() as session:
            repository = RunRepository(session)
            for kind, (content_type, content) in research.artifacts_for(state, metadata).items():
                session.add(
                    RunArtifact(
                        run_id=run_id,
                        kind=kind,
                        content_type=content_type,
                        content=content,
                        size_bytes=len(content.encode()),
                    )
                )
            await repository.update_metadata(
                run_id,
                prompt_versions=metadata["prompt_versions"],
                models_used=metadata["models_used"],
                skills_used=state.skills,
            )
            await repository.finish(
                run_id,
                status=research.outcome_status(state),
                stop_reason=state.stop_reason.value if state.stop_reason else None,
                gate_status=(state.gate_result or {}).get("verdict"),
            )
    except BaseException as exc:
        async with sessionmaker() as session:
            await RunRepository(session).finish(
                run_id,
                status=RunStatus.FAILED,
                error_code="UNEXPECTED_EXCEPTION",
                error_message=f"{type(exc).__name__}: {exc}"[:500],
            )
        raise
    finally:
        beating.cancel()
        await toolkit.aclose()
        await db.dispose()
    print(f"\nrecorded as run {run_id}", file=sys.stderr)
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    from research_agent import services

    {"api": services.serve_api, "agent": services.serve_agent}[args.role]()
    return 0


def _cmd_migrate(_args: argparse.Namespace) -> int:
    from research_agent import services

    services.migrate()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="research", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    config = sub.add_parser("config", help="show the effective configuration and its hash")
    config.add_argument(
        "--override",
        action="append",
        type=_parse_override,
        metavar="PATH=VALUE",
        help="per-run override, e.g. budget.max_searches=30",
    )
    config.add_argument("--json", action="store_true", help="machine-readable output")
    config.set_defaults(func=_cmd_config)

    contracts = sub.add_parser("contracts", help="show the run state machine and error codes")
    contracts.set_defaults(func=_cmd_contracts)

    run = sub.add_parser("run", help="run a research question end to end, without the services")
    run.add_argument("question")
    run.add_argument("--out", help="output directory (default: examples/<slug>)")
    run.add_argument(
        "--override",
        action="append",
        type=_parse_override,
        metavar="PATH=VALUE",
        help="per-run override, e.g. budget.max_iterations=2",
    )
    run.add_argument(
        "--simulate",
        action="store_true",
        help="offline: rule-based model and a built-in corpus, no keys needed",
    )
    run.add_argument("--quiet", action="store_true", help="do not print the timeline")
    run.add_argument(
        "--persist",
        action="store_true",
        help="record the run in the database (DATABASE_URL) so it appears in the UI",
    )
    run.set_defaults(func=_cmd_run)

    serve = sub.add_parser("serve", help="run a service: the browser-facing api or an agent")
    serve.add_argument("role", choices=["api", "agent"])
    serve.set_defaults(func=_cmd_serve)

    migrate = sub.add_parser(
        "migrate", help="bring the schema to head and create the encryption key if missing"
    )
    migrate.set_defaults(func=_cmd_migrate)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    exit_code: int = args.func(args)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
