"""`research run` - the developer's door to the same core (D30)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from research_agent.cli import main


def test_simulated_run_writes_the_example_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "eu-ai-act"
    code = main(
        ["run", "What is the EU AI Act implementation timeline?", "--simulate", "--out", str(out)]
    )
    assert code == 0
    assert {p.name for p in out.iterdir()} == {
        "input.md",
        "report.md",
        "report.json",
        "gate_result.json",
        "state.json",
        "trace.jsonl",
    }
    trace = [json.loads(line) for line in (out / "trace.jsonl").read_text().splitlines()]
    assert any(event["data"].get("display", "").startswith("[Planner]") for event in trace)
    assert json.loads((out / "gate_result.json").read_text())["verdict"] in {
        "pass",
        "pass_with_warnings",
    }
    printed = capsys.readouterr().out
    assert "[Search] Received" in printed, "the timeline is echoed in the case's format"


def test_a_run_without_keys_explains_what_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in (
        "GEMINI_API_KEY",
        "OPENAI_API_KEY",
        "TAVILY_API_KEY",
        "BRAVE_API_KEY",
        "OLLAMA_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    code = main(["run", "Anything?", "--out", str(tmp_path / "x"), "--quiet"])
    assert code == 1
    assert "LLM_AUTH" in capsys.readouterr().err


def test_an_invalid_override_is_rejected(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["run", "q", "--simulate", "--override", "gate.enabled=false"]) == 2
