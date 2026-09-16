"""Redaction for everything that leaves the process: logs, run events, Langfuse payloads.

This is boundary B2 of the PII design (architecture v0.6 §12) and the enforcement point for "keys
never appear in a log, an event or a trace" (§15.3). Two rules:

  * value patterns - provider keys, bearer tokens, connection strings and sensitive identifiers
    are matched by regex, with a checksum post-filter where the pattern alone is too loose
    (an 11-digit number is not a TCKN unless the checksum agrees, and redacting every 11-digit
    number would mangle perfectly ordinary logs),
  * key names - anything stored under `api_key`, `authorization`, `password`, ... is replaced
    wholesale, because the value may be in a format we do not recognise.

Reversible per-run masking of the user's question (`<TCKN_1>` placeholders) belongs to the intake
boundary and lands with the Presidio client; this module is deliberately one-way.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any, Final

PLACEHOLDER: Final = "<{label}>"

# Key names whose value is replaced regardless of shape.
SENSITIVE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "ciphertext",
        "cookie",
        "credentials",
        "key",
        "keys",
        "password",
        "secret",
        "secret_key",
        "session",
        "token",
    }
)


def _luhn(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def is_valid_card(value: str) -> bool:
    """Luhn check, so a long invoice number is not reported as a credit card."""
    return _luhn(re.sub(r"\D", "", value))


def is_valid_tckn(value: str) -> bool:
    """Turkish national id checksum (also used by the Presidio ad-hoc recogniser post-filter)."""
    if len(value) != 11 or not value.isdigit() or value[0] == "0":
        return False
    digits = [int(char) for char in value]
    odd_sum = sum(digits[0:9:2])
    even_sum = sum(digits[1:8:2])
    if (odd_sum * 7 - even_sum) % 10 != digits[9]:
        return False
    return sum(digits[:10]) % 10 == digits[10]


# Ordered: the most specific pattern wins, so a card number is never reported as a phone number.
# `validator` keeps loose numeric patterns honest.
_RULES: Final[tuple[tuple[str, re.Pattern[str], Callable[[str], bool] | None], ...]] = (
    ("GOOGLE_API_KEY", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"), None),
    # Newer Google AI Studio keys look like `AQ.` followed by ~50 URL-safe characters.
    ("GOOGLE_API_KEY", re.compile(r"\bAQ\.[0-9A-Za-z_\-.]{30,}"), None),
    # Langfuse keys are `sk-lf-...`, so they have to be matched before the OpenAI pattern.
    ("LANGFUSE_KEY", re.compile(r"\b(?:pk|sk)-lf-[A-Za-z0-9_\-]{8,}\b"), None),
    ("OPENAI_API_KEY", re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b"), None),
    ("TAVILY_API_KEY", re.compile(r"\btvly-[A-Za-z0-9_\-]{8,}\b"), None),
    ("BRAVE_API_KEY", re.compile(r"\bBSA[A-Za-z0-9_\-]{20,}\b"), None),
    ("BEARER_TOKEN", re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{8,}", re.IGNORECASE), None),
    (
        "DATABASE_URL",
        re.compile(r"\b(?P<scheme>[a-z+]+)://[^\s:/@]+:[^\s/@]+@[^\s]+", re.IGNORECASE),
        None,
    ),
    ("IBAN", re.compile(r"\b[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}\b"), None),
    ("CREDIT_CARD", re.compile(r"\b(?:\d[ -]?){12,18}\d\b"), is_valid_card),
    ("TCKN", re.compile(r"\b[1-9][0-9]{10}\b"), is_valid_tckn),
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), None),
    (
        "PHONE",
        re.compile(r"(?:\+90|0)?\s?5\d{2}\s?\d{3}\s?\d{2}\s?\d{2}\b"),
        None,
    ),
    ("IP_ADDRESS", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), None),
)


def _keep_unless(
    validator: Callable[[str], bool], replacement: str
) -> Callable[[re.Match[str]], str]:
    """Wrap a checksum validator so a loose numeric pattern only redacts genuine matches."""

    def substitute(match: re.Match[str]) -> str:
        return replacement if validator(match.group(0)) else match.group(0)

    return substitute


def redact_text(text: str) -> str:
    """Replace every recognised secret or sensitive identifier with a typed placeholder."""
    for label, pattern, validator in _RULES:
        replacement = PLACEHOLDER.format(label=label)
        if validator is None:
            text = pattern.sub(replacement, text)
        else:
            text = pattern.sub(_keep_unless(validator, replacement), text)
    return text


def redact(value: Any) -> Any:
    """Redact recursively through mappings and sequences, by key name and by value pattern."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {
            key: "<REDACTED>" if str(key).lower() in SENSITIVE_KEYS else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact(item) for item in value)
    if isinstance(value, set):
        return {redact(item) for item in value}
    return value
