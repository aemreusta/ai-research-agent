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


def _cmd_run(args: argparse.Namespace) -> int:
    del args
    print(
        "the agent graph is not wired yet - it lands with the nodes in Faz 3.\n"
        "`research config` and `research contracts` already work.",
        file=sys.stderr,
    )
    return 3


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

    run = sub.add_parser("run", help="run a research question end to end")
    run.add_argument("question")
    run.add_argument("--out", help="directory for report.md, trace.jsonl, gate_result.json")
    run.set_defaults(func=_cmd_run)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    exit_code: int = args.func(args)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
