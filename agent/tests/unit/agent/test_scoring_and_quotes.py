"""Source scoring (v0.6 §9) and verbatim quote verification (§11.1)."""

from __future__ import annotations

from datetime import date

import pytest

from research_agent.agent.quotes import verify_quote
from research_agent.agent.scoring import DomainTiers, recency_score, score_source
from research_agent.agent.state import TimeScope
from research_agent.config.schema import ScoringSettings

TIERS = DomainTiers.load()
AS_OF = date(2026, 9, 16)


@pytest.mark.parametrize(
    ("domain", "tier"),
    [
        ("kvkk.gov.tr", 1),
        ("resmigazete.gov.tr", 1),
        ("digital-strategy.ec.europa.eu", 1),
        ("reuters.com", 2),
        ("some-blog.medium.com", 3),
        ("medium.com", 3),
        ("unknown-site.io", 3),
    ],
)
def test_domain_tiers(domain: str, tier: int) -> None:
    assert TIERS.tier_of(domain) == tier


def test_skill_references_can_add_domains() -> None:
    extended = TIERS.extended({1: ["mevzuat.gov.tr", "btk.gov.tr"], 2: ["webrazzi.com"]})
    assert extended.tier_of("webrazzi.com") == 2
    assert TIERS.tier_of("webrazzi.com") == 3, "extension must not mutate the base tiers"


def test_recency_rewards_in_scope_and_penalises_out_of_scope() -> None:
    scope = TimeScope(start=date(2026, 1, 1), end=date(2026, 12, 31))
    assert recency_score(date(2026, 3, 1), AS_OF, scope) > recency_score(
        date(2024, 3, 1), AS_OF, scope
    )
    assert recency_score(None, AS_OF, scope) == pytest.approx(0.4)
    assert recency_score(date(2023, 1, 1), AS_OF, scope) <= 0.2


def test_the_weighted_score_and_its_rationale() -> None:
    score = score_source(
        domain="kvkk.gov.tr",
        published_at=date(2026, 3, 12),
        as_of=AS_OF,
        scope=TimeScope(start=date(2026, 1, 1)),
        relevance=0.85,
        entities=[],
        tiers=TIERS,
        settings=ScoringSettings(),
    )
    assert score.tier == 1 and score.is_primary
    expected = 0.35 * 1.0 + 0.25 * 1.0 + 0.15 * score.recency + 0.25 * 0.85
    assert score.total == pytest.approx(expected, abs=1e-3)
    assert score.rationale.startswith("kvkk.gov.tr → ")
    assert "T1" in score.rationale and "primary" in score.rationale


def test_a_company_site_is_primary_for_claims_about_itself() -> None:
    score = score_source(
        domain="apilex.ai",
        published_at=None,
        as_of=AS_OF,
        scope=TimeScope(),
        relevance=0.9,
        entities=["ApilexAI"],
        tiers=TIERS,
        settings=ScoringSettings(),
    )
    assert score.is_primary
    other = score_source(
        domain="techblog.com",
        published_at=None,
        as_of=AS_OF,
        scope=TimeScope(),
        relevance=0.9,
        entities=["ApilexAI"],
        tiers=TIERS,
        settings=ScoringSettings(),
    )
    assert not other.is_primary


def test_the_llm_can_nudge_authority_but_not_cross_a_tier() -> None:
    """Prompt injection cannot promote a blog to a regulator (v0.6 §11.4, point 4)."""
    nudged = score_source(
        domain="random-blog.net",
        published_at=None,
        as_of=AS_OF,
        scope=TimeScope(),
        relevance=1.0,
        entities=[],
        tiers=TIERS,
        settings=ScoringSettings(),
        authority_adjustment=+0.9,
        llm_primary=True,
    )
    assert nudged.tier == 3
    assert nudged.authority <= 0.4
    assert nudged.authority < TIERS.tier_score(2)


# --- quotes ----------------------------------------------------------------------------------------

CONTENT = """Kişisel Verileri Koruma Kurumu (KVKK) tarafından yayımlanan rehbere göre,
standart sözleşmeler imzalandıktan sonra   beş iş günü içinde Kuruma bildirilmelidir.
Bu yükümlülük 1 Haziran 2024 tarihinde yürürlüğe girmiştir."""


def test_an_exact_quote_verifies_despite_whitespace_and_case() -> None:
    result = verify_quote(
        "standart sözleşmeler imzalandıktan sonra beş iş günü içinde Kuruma bildirilmelidir",
        CONTENT,
    )
    assert result.ok and result.method == "exact"


def test_a_lightly_paraphrased_quote_verifies_fuzzily() -> None:
    result = verify_quote(
        "Standart sözleşmeler imzalandıktan sonra beş iş günü içerisinde Kuruma bildirilmelidir.",
        CONTENT,
    )
    assert result.ok and result.method == "fuzzy"


def test_an_invented_quote_is_rejected() -> None:
    """The core of the anti-hallucination and anti-injection design: no quote, no claim."""
    assert not verify_quote("Kurum, 2026'da tüm şirketlere 500 milyon TL ceza kesti.", CONTENT).ok


def test_a_changed_number_is_rejected_even_if_the_words_match() -> None:
    assert not verify_quote(
        "standart sözleşmeler imzalandıktan sonra on iş günü içinde Kuruma bildirilmelidir", CONTENT
    ).ok
    assert not verify_quote(
        "Bu yükümlülük 1 Haziran 2025 tarihinde yürürlüğe girmiştir.", CONTENT
    ).ok


def test_trivially_short_quotes_are_not_evidence() -> None:
    assert not verify_quote("KVKK", CONTENT).ok
