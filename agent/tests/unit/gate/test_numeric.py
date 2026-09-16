"""Number and date normalisation: the ground G4 and contradiction detection stand on."""

from __future__ import annotations

import pytest

from research_agent.gate.numeric import Kind, Quantity, extract_quantities, quantities_match


def _one(text: str, language: str = "en") -> Quantity:
    found = extract_quantities(text, language=language)
    assert len(found) == 1, f"{text!r} -> {found}"
    return found[0]


@pytest.mark.parametrize(
    ("text", "language", "kind", "value", "unit"),
    [
        ("growth of 15%", "en", Kind.PERCENT, 15.0, None),
        ("yüzde 15 büyüme", "tr", Kind.PERCENT, 15.0, None),
        ("%2,5 artış", "tr", Kind.PERCENT, 2.5, None),
        ("a 2.5 percent rise", "en", Kind.PERCENT, 2.5, None),
        ("revenue of $1.2B", "en", Kind.MONEY, 1.2e9, "USD"),
        ("€5 million fine", "en", Kind.MONEY, 5e6, "EUR"),
        ("1,2 milyar TL gelir", "tr", Kind.MONEY, 1.2e9, "TRY"),
        ("500 bin TL ceza", "tr", Kind.MONEY, 5e5, "TRY"),
        ("USD 3.4 billion market", "en", Kind.MONEY, 3.4e9, "USD"),
        ("15 milyon avro", "tr", Kind.MONEY, 15e6, "EUR"),
        ("1.2M users", "en", Kind.NUMBER, 1.2e6, None),
        ("1,2 milyon kullanıcı", "tr", Kind.NUMBER, 1.2e6, None),
        ("12 bin şirket", "tr", Kind.NUMBER, 12e3, None),
        ("45 searches", "en", Kind.NUMBER, 45.0, None),
    ],
)
def test_quantities(text: str, language: str, kind: Kind, value: float, unit: str | None) -> None:
    quantity = _one(text, language)
    assert quantity.kind is kind
    assert value in quantity.values
    assert quantity.unit == unit


def test_thousands_separators_follow_the_language() -> None:
    assert 1234.0 in _one("1.234 şirket", "tr").values
    assert 1234.0 in _one("1,234 companies", "en").values
    assert 1.234 in _one("1.234 ratio", "en").values


@pytest.mark.parametrize(
    ("text", "language", "iso", "granularity"),
    [
        ("on 2026-03-12", "en", "2026-03-12", "day"),
        ("12 Mart 2026 tarihinde", "tr", "2026-03-12", "day"),
        ("March 12, 2026", "en", "2026-03-12", "day"),
        ("2 August 2026", "en", "2026-08-02", "day"),
        ("Mart 2026'da", "tr", "2026-03", "month"),
        ("in August 2025", "en", "2025-08", "month"),
        ("2027 yılında", "tr", "2027", "year"),
    ],
)
def test_dates(text: str, language: str, iso: str, granularity: str) -> None:
    quantity = _one(text, language)
    assert quantity.kind in (Kind.DATE, Kind.YEAR)
    assert quantity.iso == iso
    assert quantity.granularity == granularity


def test_a_date_is_not_also_counted_as_a_number() -> None:
    kinds = [q.kind for q in extract_quantities("12 Mart 2026 itibarıyla %20", language="tr")]
    assert sorted(kinds) == sorted([Kind.DATE, Kind.PERCENT])


def test_citation_markers_and_placeholders_are_ignored() -> None:
    assert extract_quantities("As reported [3] by <TCKN_1> and [c12].", language="en") == []


def test_several_quantities_in_one_sentence() -> None:
    found = extract_quantities("In 2025 revenue grew 23% to $4.5 billion.", language="en")
    assert [q.kind for q in found] == [Kind.YEAR, Kind.PERCENT, Kind.MONEY]


# --- matching ----------------------------------------------------------------------------------


def _q(text: str, language: str = "en") -> Quantity:
    return _one(text, language)


@pytest.mark.parametrize(
    ("left", "right", "language", "exact", "tolerant"),
    [
        ("1.2M users", "1,2 milyon kullanıcı", "mixed", True, True),
        ("%15", "15%", "mixed", True, True),
        ("$1.23 billion", "$1.2 billion", "en", False, True),  # rounding: within tolerance
        ("$1.5 billion", "$1.2 billion", "en", False, False),
        ("Mart 2026", "2026-03-12", "mixed", False, True),  # granularity
        ("2026-03-12", "2026-03-13", "en", False, False),
        ("€5 million", "$5 million", "en", False, False),  # currency differs
        ("23%", "23.4%", "en", False, True),
        ("23%", "25%", "en", False, False),
        ("2026", "2026-05-01", "en", False, True),
    ],
)
def test_matching(left: str, right: str, language: str, exact: bool, tolerant: bool) -> None:
    lang_left = (
        "tr" if any(c in left for c in "ığşçöü") or "Mart" in left or left.startswith("%") else "en"
    )
    lang_right = "tr" if any(c in right for c in "ığşçöü") else "en"
    a, b = _q(left, lang_left), _q(right, lang_right)
    assert quantities_match(a, b, relative=0.0, percent_points=0.0, date_days=0) is exact
    assert quantities_match(a, b, relative=0.05, percent_points=0.5, date_days=31) is tolerant


def test_rounding_to_the_written_precision_is_tolerated_regardless_of_the_gap() -> None:
    precise = _one("$1.23 billion")
    rounded = _one("$1.2 billion")
    assert quantities_match(precise, rounded, relative=0.02, percent_points=0.5, date_days=31)
    assert not quantities_match(
        _one("$1.26 billion"),
        _one("$1.2 billion"),
        relative=0.02,
        percent_points=0.5,
        date_days=31,
    )


def test_rounding_is_not_accepted_when_tolerance_is_off() -> None:
    assert not quantities_match(
        _one("$1.23 billion"),
        _one("$1.2 billion"),
        relative=0,
        percent_points=0,
        date_days=0,
    )
