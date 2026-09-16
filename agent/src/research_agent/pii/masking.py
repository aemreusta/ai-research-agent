"""Intake masking: replace sensitive identifiers with stable, numbered placeholders.

Unlike `observability.redaction`, which is one-way and shaped for logs, this is shaped for the
question that goes on to be researched:

* **Stable placeholders.** The same value always becomes the same `<TCKN_1>`, so a model reading
  "<TCKN_1> ve <TCKN_1>" understands it is one person; a blanket `<TCKN>` would suggest two.
* **Checksum-validated.** An eleven-digit number is only a national id if the checksum agrees,
  and a card number only if Luhn does. Masking every long number would mangle exactly the kind
  of question this system is for ("ciro 12345678901 TL").
* **Names survive on purpose** (D20), unless `pii.mask_question_person_names` is on.

`RegexMasker` needs nothing running. `PresidioMasker` adds Presidio's recognisers on top - the
result is always the union with the regex findings, so Presidio can only add coverage - and
falls back to regex alone, flagged as degraded (`PII_ENGINE_DEGRADED`), when Presidio does not
answer. Known patterns are masked in every mode (v0.6 §12).
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Final, Protocol

import httpx

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

# Presidio entity -> our placeholder label, with the validator that must agree.
_PRESIDIO: Final[dict[str, tuple[str, Callable[[str], bool] | None]]] = {
    "EMAIL_ADDRESS": ("EMAIL", None),
    "PHONE_NUMBER": ("PHONE", None),
    "TR_PHONE": ("PHONE", None),
    "CREDIT_CARD": ("CREDIT_CARD", is_valid_card),
    "IBAN_CODE": ("IBAN", None),
    "IP_ADDRESS": ("IP_ADDRESS", None),
    "TR_TCKN": ("TCKN", is_valid_tckn),
    "PERSON": ("PERSON", None),
}

_AD_HOC: Final[list[dict[str, Any]]] = [
    {
        "name": "Turkish national id",
        "supported_language": "en",
        "supported_entity": "TR_TCKN",
        "patterns": [{"name": "tckn", "regex": r"\b[1-9][0-9]{10}\b", "score": 0.6}],
        "context": ["tckn", "kimlik", "tc"],
    },
    {
        "name": "Turkish phone",
        "supported_language": "en",
        "supported_entity": "TR_PHONE",
        "patterns": [
            {
                "name": "tr phone",
                "regex": r"(\+90|0)?\s?5[0-9]{2}\s?[0-9]{3}\s?[0-9]{2}\s?[0-9]{2}",
                "score": 0.5,
            }
        ],
    },
]


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
    engine: str = "regex"

    def summary(self) -> dict[str, int]:
        """`{"TCKN": 1, "EMAIL": 1}` - what the run timeline and the UI show."""
        counted = Counter(entity.kind for entity in self.entities)
        return dict(sorted(counted.items()))


@dataclass(frozen=True, slots=True)
class Span:
    kind: str
    start: int
    end: int


class Masker(Protocol):
    def mask(self, text: str) -> MaskResult: ...
    async def amask(self, text: str) -> MaskResult: ...


def regex_spans(text: str) -> list[Span]:
    spans: list[Span] = []
    for kind, pattern, validator in _PATTERNS:
        for match in pattern.finditer(text):
            if validator is not None and not validator(match.group(0)):
                continue
            spans.append(Span(kind, match.start(), match.end()))
    return spans


def apply_spans(text: str, spans: list[Span], *, degraded: bool, engine: str) -> MaskResult:
    """Replace non-overlapping spans (earliest first, then longest) with stable placeholders."""
    chosen: list[Span] = []
    for span in sorted(spans, key=lambda s: (s.start, -(s.end - s.start))):
        if chosen and span.start < chosen[-1].end:
            continue
        chosen.append(span)

    counters: Counter[str] = Counter()
    assigned: dict[tuple[str, str], str] = {}
    occurrences: Counter[str] = Counter()
    pieces: list[str] = []
    cursor = 0
    for span in chosen:
        value = text[span.start : span.end]
        key = (span.kind, re.sub(r"\s|-", "", value))
        if key not in assigned:
            counters[span.kind] += 1
            assigned[key] = f"<{span.kind}_{counters[span.kind]}>"
        placeholder = assigned[key]
        occurrences[placeholder] += 1
        pieces.append(text[cursor : span.start])
        pieces.append(placeholder)
        cursor = span.end
    pieces.append(text[cursor:])

    entities = [
        MaskedEntity(kind=kind, placeholder=placeholder, occurrences=occurrences[placeholder])
        for (kind, _), placeholder in assigned.items()
    ]
    return MaskResult(text="".join(pieces), entities=entities, degraded=degraded, engine=engine)


class RegexMasker:
    """Pattern- and checksum-based masking. No dependencies, always available."""

    def __init__(self, *, degraded: bool = False) -> None:
        self._degraded = degraded

    def mask(self, text: str) -> MaskResult:
        return apply_spans(text, regex_spans(text), degraded=self._degraded, engine="regex")

    async def amask(self, text: str) -> MaskResult:
        return self.mask(text)


class PresidioMasker:
    """Presidio analyzer + our regex rules; degrades to regex alone when Presidio is down."""

    def __init__(
        self,
        analyzer_url: str,
        *,
        mask_person_names: bool = False,
        score_threshold: float = 0.5,
        timeout: float = 5.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._url = analyzer_url.rstrip("/")
        self._names = mask_person_names
        self._threshold = score_threshold
        self._timeout = timeout
        self._client = client

    def mask(self, text: str) -> MaskResult:
        """Synchronous callers get the regex floor; use `amask` for Presidio."""
        return RegexMasker().mask(text)

    async def amask(self, text: str) -> MaskResult:
        entities = [name for name in _PRESIDIO if name != "PERSON" or self._names]
        body = {
            "text": text,
            "language": "en",
            "entities": entities,
            "score_threshold": self._threshold,
            "ad_hoc_recognizers": _AD_HOC,
        }
        try:
            if self._client is not None:
                response = await self._client.post(
                    f"{self._url}/analyze", json=body, timeout=self._timeout
                )
            else:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        f"{self._url}/analyze", json=body, timeout=self._timeout
                    )
            response.raise_for_status()
            findings: list[dict[str, Any]] = response.json()
        except (httpx.HTTPError, ValueError):
            return apply_spans(text, regex_spans(text), degraded=True, engine="regex")

        spans = regex_spans(text)
        for finding in findings:
            mapping = _PRESIDIO.get(str(finding.get("entity_type")))
            if mapping is None:
                continue
            kind, validator = mapping
            start, end = int(finding["start"]), int(finding["end"])
            value = text[start:end]
            if validator is not None and not validator(value.replace(" ", "").replace("-", "")):
                continue
            spans.append(Span(kind, start, end))
        return apply_spans(text, spans, degraded=False, engine="presidio")
