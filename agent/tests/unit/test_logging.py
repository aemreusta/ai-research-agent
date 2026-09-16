"""The log pipeline itself has to redact, not just the helper function."""

from __future__ import annotations

import json

import pytest
import structlog

from research_agent.config.schema import LoggingSettings
from research_agent.observability.logging import bind_context, clear_context, configure_logging


def _emit_and_read(capsys: pytest.CaptureFixture[str], **kwargs: object) -> dict[str, object]:
    """Log one record through the real processor chain and parse it back from stdout.

    `structlog.testing.capture_logs` short-circuits the processors, which is exactly the part
    under test here, so the assertions go through stdout.
    """
    structlog.get_logger().warning("search failed", **kwargs)
    record: dict[str, object] = json.loads(capsys.readouterr().out.strip())
    return record


def test_shared_fields_and_redaction_survive_the_pipeline(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(LoggingSettings(level="INFO", format="json"), service="agent")
    bind_context(run_id="run-1", span_id="span-1", node="search")
    record = _emit_and_read(
        capsys, error_code="SEARCH_TIMEOUT", api_key="sk-" + "z" * 32, note="mail a@b.co"
    )
    clear_context()

    assert record["run_id"] == "run-1"
    assert record["span_id"] == "span-1"
    assert record["node"] == "search"
    assert record["error_code"] == "SEARCH_TIMEOUT"
    assert record["api_key"] == "<REDACTED>"
    assert record["note"] == "mail <EMAIL>"


def test_json_renderer_emits_one_object_per_line(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(LoggingSettings(level="INFO", format="json"), service="api")
    structlog.get_logger().info("[Search] Received 8 results.", node="search")
    record = json.loads(capsys.readouterr().out.strip())
    assert record["event"] == "[Search] Received 8 results."
    assert record["service"] == "api"
    assert record["node"] == "search"
    assert record["level"] == "info"
    assert "timestamp" in record


def test_level_filter_drops_quieter_records(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(LoggingSettings(level="WARNING", format="json"), service="agent")
    structlog.get_logger().info("not interesting")
    assert capsys.readouterr().out == ""
