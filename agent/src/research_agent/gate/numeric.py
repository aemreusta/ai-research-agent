"""Numbers, money, percentages and dates - found in text and compared without an LLM.

This is the foundation of output-gate rule G4 (architecture v0.6 §11.3) and of the rule-based
contradiction candidates (§10). Both need the same answer to "do these two strings state the
same quantity?", in Turkish and English:

    1.2M == 1,2 milyon        %15 == 15% == yüzde 15        Mart 2026 ~ 2026-03-12

Parsing is deliberately generous: an ambiguous number ("1.234") yields every plausible reading,
ordered by the language's convention, and two quantities match if any readings do. A false
"match" costs a warning at worst; a false "mismatch" would delete a correct sentence.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final


class Kind(StrEnum):
    NUMBER = "number"
    PERCENT = "percent"
    MONEY = "money"
    DATE = "date"
    YEAR = "year"


@dataclass(frozen=True, slots=True)
class Quantity:
    kind: Kind
    raw: str
    start: int
    end: int
    values: tuple[float, ...] = ()
    unit: str | None = None
    iso: str | None = None
    granularity: str | None = None  # day | month | year
    decimals: int = 0
    """Digits after the decimal mark as written ("1.2 billion" -> 1), for rounding checks."""
    scale: float = 1.0

    @property
    def value(self) -> float | None:
        return self.values[0] if self.values else None


MONTHS: Final[dict[str, int]] = {
    # English
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "sept": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
    # Turkish
    "ocak": 1,
    "şubat": 2,
    "subat": 2,
    "mart": 3,
    "nisan": 4,
    "mayıs": 5,
    "mayis": 5,
    "haziran": 6,
    "temmuz": 7,
    "ağustos": 8,
    "agustos": 8,
    "eylül": 9,
    "eylul": 9,
    "ekim": 10,
    "kasım": 11,
    "kasim": 11,
    "aralık": 12,
    "aralik": 12,
}

SCALES: Final[dict[str, float]] = {
    "k": 1e3,
    "thousand": 1e3,
    "bin": 1e3,
    "m": 1e6,
    "mn": 1e6,
    "mm": 1e6,
    "million": 1e6,
    "millions": 1e6,
    "milyon": 1e6,
    "b": 1e9,
    "bn": 1e9,
    "billion": 1e9,
    "billions": 1e9,
    "milyar": 1e9,
    "mlr": 1e9,
    "t": 1e12,
    "tn": 1e12,
    "trillion": 1e12,
    "trilyon": 1e12,
}

CURRENCIES: Final[dict[str, str]] = {
    "$": "USD",
    "usd": "USD",
    "dollar": "USD",
    "dollars": "USD",
    "dolar": "USD",
    "€": "EUR",
    "eur": "EUR",
    "euro": "EUR",
    "euros": "EUR",
    "avro": "EUR",
    "£": "GBP",
    "gbp": "GBP",
    "pound": "GBP",
    "pounds": "GBP",
    "sterlin": "GBP",
    "₺": "TRY",
    "try": "TRY",
    "tl": "TRY",
    "lira": "TRY",
    "türk lirası": "TRY",
}

_NUM = r"\d{1,3}(?:[.,]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?"
_MONTH = "|".join(sorted(MONTHS, key=len, reverse=True))
_SCALE = r"(?:thousand|millions?|billions?|trillion|milyon|milyar|trilyon|bin|mn|bn|mlr|tn|[kmbt])"
_CUR_WORD = r"(?:usd|eur|gbp|try|tl|dollars?|dolar|euros?|avro|pounds?|sterlin|türk lirası|lira)"
_CUR_SYMBOL = r"[$€£₺]"
_SUFFIX = r"(?:'?(?:d[ae]|t[ae]|nda|nde|ında|inde|ı|i|u|ü))?"

_IGNORED = re.compile(r"<[^>]{1,40}>|\[[^\]]{1,20}\]|§\s*\d+(?:\.\d+)*")

_PATTERNS: Final[list[tuple[Kind, re.Pattern[str]]]] = [
    (Kind.DATE, re.compile(r"\b(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})\b")),
    (
        Kind.DATE,
        re.compile(
            rf"\b(?P<d>\d{{1,2}})\.?\s+(?P<mon>{_MONTH})\s+(?P<y>\d{{4}}){_SUFFIX}\b", re.IGNORECASE
        ),
    ),
    (
        Kind.DATE,
        re.compile(
            rf"\b(?P<mon>{_MONTH})\.?\s+(?P<d>\d{{1,2}}),?\s+(?P<y>\d{{4}})\b", re.IGNORECASE
        ),
    ),
    (Kind.DATE, re.compile(rf"\b(?P<mon>{_MONTH})\s+(?P<y>\d{{4}}){_SUFFIX}\b", re.IGNORECASE)),
    (Kind.PERCENT, re.compile(rf"%\s?(?P<n>{_NUM})|(?P<n2>{_NUM})\s?%", re.IGNORECASE)),
    (Kind.PERCENT, re.compile(rf"\byüzde\s+(?P<n>{_NUM})", re.IGNORECASE)),
    (Kind.PERCENT, re.compile(rf"\b(?P<n>{_NUM})\s+(?:percent|per\s+cent)\b", re.IGNORECASE)),
    # money with the currency before the number: $1.2B, USD 3.4 billion, €5 million
    (
        Kind.MONEY,
        re.compile(
            rf"(?P<cur>{_CUR_SYMBOL}|\b{_CUR_WORD}\b)\s?(?P<n>{_NUM})\s?(?P<scale>{_SCALE})?\b",
            re.IGNORECASE,
        ),
    ),
    # money with the currency after: 1,2 milyar TL, 500 bin TL, 15 milyon avro, 20 € ...
    (
        Kind.MONEY,
        re.compile(
            rf"\b(?P<n>{_NUM})\s?(?P<scale>{_SCALE})?\s?(?P<cur>{_CUR_SYMBOL}|{_CUR_WORD}\b)",
            re.IGNORECASE,
        ),
    ),
    (Kind.NUMBER, re.compile(rf"\b(?P<n>{_NUM})\s?(?P<scale>{_SCALE})\b", re.IGNORECASE)),
    (Kind.YEAR, re.compile(r"\b(?P<y>(?:19|20)\d{2})(?:'?(?:de|da|te|ta|nin|in|ün|un))?\b")),
    (Kind.NUMBER, re.compile(rf"(?<![\w.,])(?P<n>{_NUM})(?![\w])")),
]


def parse_number(text: str, language: str = "en") -> tuple[float, ...]:
    """Every plausible reading of a numeral, the language's convention first."""
    raw = text.strip()
    if not re.search(r"[.,]", raw):
        return (float(raw),)

    readings: list[float] = []
    if "." in raw and "," in raw:
        decimal = "." if raw.rfind(".") > raw.rfind(",") else ","
        thousands = "," if decimal == "." else "."
        readings.append(float(raw.replace(thousands, "").replace(decimal, ".")))
        return tuple(readings)

    separator = "." if "." in raw else ","
    groups = raw.split(separator)
    grouped = len(groups) > 1 and all(len(group) == 3 for group in groups[1:])
    as_thousands = float("".join(groups)) if grouped else None
    as_decimal = float(raw.replace(",", ".")) if len(groups) == 2 else None

    # Turkish writes 1.234,5 ; English writes 1,234.5
    prefers_thousands = (separator == "." and language == "tr") or (
        separator == "," and language != "tr"
    )
    ordered = (as_thousands, as_decimal) if prefers_thousands else (as_decimal, as_thousands)
    readings.extend(value for value in ordered if value is not None)
    return tuple(dict.fromkeys(readings))


def _scale(token: str | None) -> float:
    if not token:
        return 1.0
    lowered = token.lower()
    # A bare "m"/"b" after a number is a scale only when attached ("1.2M"), never "5 m" (metres).
    return SCALES.get(lowered, 1.0)


def _currency(token: str | None) -> str | None:
    if not token:
        return None
    return CURRENCIES.get(token.lower().strip())


def extract_quantities(text: str, *, language: str = "en") -> list[Quantity]:
    """All quantities in reading order; each character belongs to at most one quantity."""
    taken = [False] * len(text)
    for match in _IGNORED.finditer(text):
        for index in range(match.start(), match.end()):
            taken[index] = True

    found: list[Quantity] = []
    for kind, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            start, end = match.span()
            if any(taken[start:end]):
                continue
            quantity = _build(kind, match, language)
            if quantity is None:
                continue
            found.append(quantity)
            for index in range(start, end):
                taken[index] = True
    return sorted(found, key=lambda quantity: quantity.start)


def _build(kind: Kind, match: re.Match[str], language: str) -> Quantity | None:
    groups = match.groupdict()
    raw = match.group(0).strip()
    start, end = match.span()

    if kind is Kind.DATE:
        year = int(groups["y"])
        month = (
            int(groups["m"]) if groups.get("m") else MONTHS.get(groups["mon"].lower().rstrip("."))
        )
        if month is None or not 1 <= month <= 12:
            return None
        if groups.get("d"):
            day = int(groups["d"])
            if not 1 <= day <= 31:
                return None
            return Quantity(
                kind, raw, start, end, iso=f"{year:04d}-{month:02d}-{day:02d}", granularity="day"
            )
        return Quantity(kind, raw, start, end, iso=f"{year:04d}-{month:02d}", granularity="month")

    if kind is Kind.YEAR:
        year = int(groups["y"])
        return Quantity(
            kind, raw, start, end, values=(float(year),), iso=f"{year:04d}", granularity="year"
        )

    numeral = groups.get("n") or groups.get("n2")
    if numeral is None:
        return None
    scale_token = groups.get("scale")
    if scale_token and len(scale_token) == 1 and re.search(r"\s", raw):
        # "5 m" or "3 b" with a space: too ambiguous to be a magnitude.
        if kind is Kind.NUMBER:
            return None
        scale_token = None
    multiplier = _scale(scale_token)
    readings = parse_number(numeral, language)
    values = tuple(value * multiplier for value in readings)
    decimals = _decimals(numeral, readings[0])

    if kind is Kind.MONEY:
        unit = _currency(groups.get("cur"))
        if unit is None:
            return None
        return Quantity(
            kind, raw, start, end, values=values, unit=unit, decimals=decimals, scale=multiplier
        )
    return Quantity(kind, raw, start, end, values=values, decimals=decimals, scale=multiplier)


def _decimals(numeral: str, reading: float) -> int:
    """How many decimals the writer chose, under the preferred reading."""
    if reading == int(reading) and not re.search(r"[.,]\d{1,2}$", numeral):
        return 0
    match = re.search(r"[.,](\d+)$", numeral)
    return len(match.group(1)) if match else 0


def _close(left: float, right: float, relative: float) -> bool:
    if math.isclose(left, right, rel_tol=1e-9, abs_tol=1e-9):
        return True
    if relative <= 0:
        return False
    scale = max(abs(left), abs(right))
    return abs(left - right) <= relative * scale


def _rounds_to(precise: float, coarse: Quantity, coarse_value: float) -> bool:
    """`$1.23 billion` written as `$1.2 billion` is rounding, whatever the relative gap."""
    unit = coarse.scale or 1.0
    return math.isclose(
        round(precise / unit, coarse.decimals),
        round(coarse_value / unit, coarse.decimals),
        rel_tol=1e-9,
        abs_tol=1e-9,
    )


def _date_bounds(quantity: Quantity) -> tuple[str, str] | None:
    """The quantity as an inclusive ISO range at its own granularity."""
    iso = quantity.iso
    if iso is None:
        return None
    if quantity.granularity == "day":
        return iso, iso
    if quantity.granularity == "month":
        return f"{iso}-01", f"{iso}-31"
    return f"{iso}-01-01", f"{iso}-12-31"


def quantities_match(
    left: Quantity,
    right: Quantity,
    *,
    relative: float,
    percent_points: float,
    date_days: int,
) -> bool:
    """Whether two quantities state the same thing, within the given tolerances.

    With every tolerance at zero this is an exact comparison (modulo notation). Tolerances
    admit rounding (relative), percentage-point drift, and - for dates - a coarser statement
    that contains the finer one ("March 2026" for "2026-03-12"). Two day-precise dates must be
    identical: a deadline one day off is a different deadline.
    """
    dated = {Kind.DATE, Kind.YEAR}
    if left.kind in dated or right.kind in dated:
        if left.kind in dated and right.kind in dated:
            if left.iso == right.iso and left.granularity == right.granularity:
                return True
            if date_days <= 0 or left.granularity == right.granularity:
                return False
            lb, rb = _date_bounds(left), _date_bounds(right)
            if lb is None or rb is None:
                return False
            return (lb[0] <= rb[0] and rb[1] <= lb[1]) or (rb[0] <= lb[0] and lb[1] <= rb[1])
        # A bare year compared with a plain number ("2026" vs "2026 companies").
        year, other = (left, right) if left.kind is Kind.YEAR else (right, left)
        if year.kind is Kind.YEAR and other.kind is Kind.NUMBER:
            return any(_close(a, b, 0.0) for a in year.values for b in other.values)
        return False

    if Kind.PERCENT in (left.kind, right.kind):
        if left.kind is not right.kind:
            return False
        return any(
            abs(a - b) <= max(percent_points, 1e-9) for a in left.values for b in right.values
        )

    if left.kind is Kind.MONEY and right.kind is Kind.MONEY and left.unit != right.unit:
        return False

    if any(_close(a, b, relative) for a in left.values for b in right.values):
        return True
    if relative <= 0:
        return False
    return any(
        _rounds_to(b, left, a) or _rounds_to(a, right, b) for a in left.values for b in right.values
    )


def normalised_label(quantity: Quantity) -> str:
    """A short canonical form for traces and gate reports: `USD 1.2e9`, `15%`, `2026-03`."""
    if quantity.kind in (Kind.DATE, Kind.YEAR):
        return quantity.iso or quantity.raw
    value = quantity.value
    if value is None:
        return quantity.raw
    text = f"{value:g}"
    if quantity.kind is Kind.PERCENT:
        return f"{text}%"
    if quantity.kind is Kind.MONEY:
        return f"{quantity.unit} {text}"
    return text
