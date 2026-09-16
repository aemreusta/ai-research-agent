"""Nothing sensitive reaches a log, an event or a trace (v0.6 §12 B2, §15.3)."""

from __future__ import annotations

import pytest

from research_agent.observability.redaction import is_valid_tckn, redact, redact_text


@pytest.mark.parametrize(
    ("raw", "label"),
    [
        ("AIza" + "b" * 35, "GOOGLE_API_KEY"),
        ("sk-" + "c" * 32, "OPENAI_API_KEY"),
        ("tvly-" + "d" * 24, "TAVILY_API_KEY"),
        ("BSA" + "e" * 28, "BRAVE_API_KEY"),
        ("sk-lf-" + "f" * 20, "LANGFUSE_KEY"),
    ],
)
def test_provider_keys_are_redacted(raw: str, label: str) -> None:
    redacted = redact_text(f"calling provider with key={raw}")
    assert raw not in redacted
    assert f"<{label}>" in redacted


def test_bearer_token_and_connection_string_are_redacted() -> None:
    assert "abc123def456" not in redact_text("Authorization: Bearer abc123def456")
    dsn = "postgresql+asyncpg://research:s3cr3t@postgres:5432/research"
    assert "s3cr3t" not in redact_text(f"connecting to {dsn}")


def test_sensitive_identifiers_are_redacted() -> None:
    assert "<EMAIL>" in redact_text("reach me at someone@example.com")
    assert "<IBAN>" in redact_text("IBAN TR330006100519786457841326 please")
    assert "<IP_ADDRESS>" in redact_text("client 192.168.1.44 connected")


def test_valid_tckn_is_redacted_but_an_ordinary_number_is_kept() -> None:
    # Redacting every 11-digit number would mangle ordinary logs, so the checksum decides.
    assert is_valid_tckn("10000000146")
    assert not is_valid_tckn("12345678901")
    assert "<TCKN>" in redact_text("kimlik 10000000146")
    assert "12345678901" in redact_text("request id 12345678901")


def test_luhn_filter_guards_the_card_pattern() -> None:
    assert "<CREDIT_CARD>" in redact_text("card 4111111111111111 charged")
    assert "4111111111111112" in redact_text("order 4111111111111112")


def test_values_under_sensitive_keys_are_replaced_whatever_their_shape() -> None:
    payload = {
        "run_id": "r-1",
        "api_key": "whatever-format-this-is",
        "nested": {"token": "opaque", "note": "mail me at a@b.co"},
        "providers": [{"secret": "x"}, "plain"],
    }
    redacted = redact(payload)
    assert redacted == {
        "run_id": "r-1",
        "api_key": "<REDACTED>",
        "nested": {"token": "<REDACTED>", "note": "mail me at <EMAIL>"},
        "providers": [{"secret": "<REDACTED>"}, "plain"],
    }


def test_redaction_preserves_non_string_values() -> None:
    assert redact({"iteration": 3, "cost_usd": 0.12, "ok": True, "none": None}) == {
        "iteration": 3,
        "cost_usd": 0.12,
        "ok": True,
        "none": None,
    }


def test_clean_text_is_left_alone() -> None:
    message = "[Planner] Created 4 research tasks."
    assert redact_text(message) == message


def test_new_style_google_keys_are_redacted() -> None:
    """Google AI Studio issues `AQ.`-prefixed keys now (seen 2026-09-16)."""
    fake = "AQ.Ab8RN6" + "x" * 20 + "_" + "Y" * 22
    out = redact_text(f"calling with {fake} now")
    assert fake not in out
    assert "<GOOGLE_API_KEY>" in out
