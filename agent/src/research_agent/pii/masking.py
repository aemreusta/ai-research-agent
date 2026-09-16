"""Intake masking: replace sensitive identifiers with stable, numbered placeholders.

Unlike `observability.redaction`, which is one-way and shaped for logs, this is shaped for the
question that goes on to be researched:

* **Stable placeholders.** The same value always becomes the same `<TCKN_1>`, so a model reading
  "<TCKN_1> ve <TCKN_1>" understands it is one person; a blanket `<TCKN>` would suggest two.
* **Checksum-validated.** An eleven-digit number is only a national id if the checksum agrees,
  and a card number only if Luhn does. Masking every long number would mangle exactly the kind
  of question this system is for ("ciro 12345678901 TL").
* **Names survive on purpose** (D20).

`RegexMasker` is the floor: it needs nothing running and is also the degraded mode when Presidio
is unreachable (`PII_ENGINE_DEGRADED`). The Presidio-backed masker in Faz 2 implements the same
protocol and adds entity types regex cannot reach.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Final, Protocol

from research_agent.observability.redaction import is_valid_card, is_valid_tckn

# Ordered most specific first, so a card number is never reported as a phone number.
_PATTERNS: Final[tuple[tuple[str, re.Pattern[str], Callable[[str], bool] | None], ...]] = (
    ("IBAN", re.compile(r"\bTR[0-9]{2}[0-9A-Z]{20,26}\b"), None),
    ("IBAN", re.compile(r"\b[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}\b"), None),
    ("CREDIT_CARD", re.compile(r"\b(?:\d[ -]?){12,18}\d\b"), is_valid_card),
    ("TCKN", re.compile(r"\b[1-9][0-9]{10}\b"), is_valid_tckn),
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), None),
    ("PHONE", re.compile(r"(?:\+90|0)?\s?5\d{2}\s?\d{3}\s?\d{2}\s?\d{2}\b"), None),
    ("IP_ADDRESS", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), None),
)


@dataclass(frozen=True, slots=True)
class MaskedEntity:
    """One masked identifier. Deliberately holds no value - this record is written to the trace."""

    kind: str
    placeholder: str
    occurrences: int


@dataclass(frozen=True, slots=True)
class MaskResult:
    text: str
    entities: list[MaskedEntity] = field(default_factory=list)
    degraded: bool = False
    """True when the full engine was unavailable and only patterns were applied."""

    def summary(self) -> dict[str, int]:
        """`{"TCKN": 1, "EMAIL": 1}` - what the run timeline and the UI show."""
        counted = Counter(entity.kind for entity in self.entities)
        return dict(sorted(counted.items()))


class Masker(Protocol):
    def mask(self, text: str) -> MaskResult: ...


class RegexMasker:
    """Pattern- and checksum-based masking. No dependencies, always available."""

    def __init__(self, *, degraded: bool = False) -> None:
        self._degraded = degraded

    def mask(self, text: str) -> MaskResult:
        counters: Counter[str] = Counter()
        assigned: dict[tuple[str, str], str] = {}
        occurrences: Counter[str] = Counter()
        masked = text

        for kind, pattern, validator in _PATTERNS:
            for value in _unique_matches(pattern, masked, validator):
                key = (kind, value)
                if key not in assigned:
                    counters[kind] += 1
                    assigned[key] = f"<{kind}_{counters[kind]}>"
                placeholder = assigned[key]
                masked, replaced = re.subn(re.escape(value), placeholder, masked)
                occurrences[placeholder] += replaced

        entities = [
            MaskedEntity(kind=kind, placeholder=placeholder, occurrences=occurrences[placeholder])
            for (kind, _), placeholder in assigned.items()
        ]
        return MaskResult(text=masked, entities=entities, degraded=self._degraded)


def _unique_matches(
    pattern: re.Pattern[str], text: str, validator: Callable[[str], bool] | None
) -> Iterable[str]:
    """Distinct matches in order of appearance, filtered by the checksum where there is one."""
    seen: list[str] = []
    for match in pattern.finditer(text):
        value = match.group(0)
        if validator is not None and not validator(value):
            continue
        if value not in seen:
            seen.append(value)
    return seen
