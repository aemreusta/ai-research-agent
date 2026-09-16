"""Duplicate detection layers L1, L2 and L4 (architecture v0.6 §8)."""

from __future__ import annotations

import pytest

from research_agent.agent.dedup import (
    OriginIndex,
    QueryDeduplicator,
    canonicalize_url,
    domain_of,
    minhash_signature,
)
from research_agent.agent.text import detect_language, fold, token_jaccard, tokens

# --- text ----------------------------------------------------------------------------------------


def test_folding_unifies_every_i_for_matching() -> None:
    """`str.lower()` turns İ into i + combining dot; mixed-language text needs one i."""
    assert fold("İSTANBUL") == "istanbul"
    assert fold("ApilexAI") == fold("apilexai")
    assert fold("ılık") == fold("ILIK") == "ilik"


def test_tokens_drop_stopwords_and_punctuation() -> None:
    assert tokens("What is the KVKK 2026 action plan?") == ["kvkk", "2026", "action", "plan"]
    assert "ve" not in tokens("KVKK ve GDPR için yükümlülükler")


@pytest.mark.parametrize(
    ("text", "language"),
    [
        ("Türk SaaS şirketleri için KVKK 2026 aksiyon planı nedir?", "tr"),
        ("ApilexAI'ın ürünleri ve iş ortaklıkları nelerdir?", "tr"),
        ("What changed in the EU AI Act implementation timeline?", "en"),
        ("Market size of legal tech in Europe", "en"),
    ],
)
def test_language_detection(text: str, language: str) -> None:
    assert detect_language(text) == language


def test_token_jaccard() -> None:
    assert token_jaccard("kvkk 2026 plan", "plan kvkk 2026") == 1.0
    assert token_jaccard("kvkk", "gdpr") == 0.0


# --- L1: URLs ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "canonical"),
    [
        (
            "HTTPS://WWW.Example.com/Path/?utm_source=x&b=2&a=1#section",
            "https://example.com/Path?a=1&b=2",
        ),
        ("http://example.com/path/", "https://example.com/path"),
        ("https://m.example.com/news/1", "https://example.com/news/1"),
        ("https://example.com/news/1/amp", "https://example.com/news/1"),
        ("https://example.com/news/1?amp=1&gclid=abc&fbclid=z", "https://example.com/news/1"),
        ("https://amp.example.com/news/1.amp", "https://example.com/news/1"),
        ("https://example.com", "https://example.com/"),
        ("https://example.com:443/a", "https://example.com/a"),
    ],
)
def test_url_canonicalization(raw: str, canonical: str) -> None:
    assert canonicalize_url(raw) == canonical


def test_non_http_urls_are_rejected() -> None:
    assert canonicalize_url("javascript:alert(1)") is None
    assert canonicalize_url("ftp://example.com/file") is None
    assert canonicalize_url("not a url") is None


def test_domain_of_strips_www() -> None:
    assert domain_of("https://www.kvkk.gov.tr/Icerik/1") == "kvkk.gov.tr"


# --- L2: near-duplicate documents ----------------------------------------------------------------

PRESS_RELEASE = (
    "Kişisel Verileri Koruma Kurumu, 2026 yılı için yurt dışına veri aktarımına ilişkin yeni "
    "rehberini yayımladı. Rehber, standart sözleşmelerin imzalanmasından itibaren beş iş günü "
    "içinde Kuruma bildirilmesi gerektiğini hatırlatıyor ve bağlayıcı şirket kurallarına dair "
    "başvuru sürecini adım adım açıklıyor. Kurum, veri sorumlularının aktarım envanterlerini "
    "güncel tutmalarını ve risk değerlendirmelerini belgelemelerini öneriyor."
)


def test_syndicated_copies_share_an_origin() -> None:
    """Five sites re-publishing one press release count as one confirmation (§8)."""
    index = OriginIndex(threshold=0.8, num_perm=128, shingle_size=5)
    original = index.assign("d1", PRESS_RELEASE)
    copy_with_byline = index.assign("d2", "Haber Merkezi | " + PRESS_RELEASE + " (AA)")
    assert copy_with_byline == original


def test_different_articles_get_different_origins() -> None:
    index = OriginIndex(threshold=0.8, num_perm=128, shingle_size=5)
    first = index.assign("d1", PRESS_RELEASE)
    other = index.assign(
        "d2",
        "Avrupa Birliği Yapay Zeka Yasası'nın uygulama takvimi, genel amaçlı yapay zeka "
        "modellerine ilişkin yükümlülüklerin Ağustos 2025'te başlamasını öngörüyor; yüksek "
        "riskli sistemler için yükümlülükler ise daha sonraki tarihlerde devreye giriyor.",
    )
    assert first != other


def test_exact_duplicates_are_caught_before_minhash() -> None:
    index = OriginIndex(threshold=0.8, num_perm=128, shingle_size=5)
    assert index.assign("d1", "Short text.") == index.assign("d2", "  short   TEXT. ")


def test_the_index_can_be_rebuilt_from_stored_signatures() -> None:
    """A resumed run rebuilds the index from the checkpoint instead of re-hashing documents."""
    index = OriginIndex(threshold=0.8, num_perm=128, shingle_size=5)
    origin = index.assign("d1", PRESS_RELEASE)
    signature = minhash_signature(PRESS_RELEASE, num_perm=128, shingle_size=5)

    rebuilt = OriginIndex(threshold=0.8, num_perm=128, shingle_size=5)
    rebuilt.restore("d1", origin, signature, content_hash=index.content_hash_of("d1"))
    assert rebuilt.assign("d9", PRESS_RELEASE + " Kaynak: AA") == origin


# --- L4: queries -----------------------------------------------------------------------------------


def test_reworded_queries_are_duplicates() -> None:
    dedup = QueryDeduplicator(threshold=0.9)
    assert dedup.admit("KVKK 2026 yurt dışı aktarım rehberi") is True
    assert dedup.admit("kvkk  2026 rehberi yurt dışı aktarım") is False
    assert dedup.admit("KVKK 2026 cezalar") is True


def test_stopword_only_differences_are_duplicates() -> None:
    dedup = QueryDeduplicator(threshold=0.9)
    dedup.admit("EU AI Act implementation timeline")
    assert dedup.admit("the EU AI Act implementation timeline") is False


def test_the_deduplicator_remembers_earlier_rounds() -> None:
    dedup = QueryDeduplicator(threshold=0.9, seen=["ApilexAI partnerships"])
    assert dedup.admit("apilexai partnerships") is False
