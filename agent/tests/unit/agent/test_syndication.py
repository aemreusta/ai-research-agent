"""L2b: republished press releases are one origin even when the pages around them differ."""

from __future__ import annotations

from research_agent.agent.dedup.syndication import merge_syndicated_origins
from research_agent.agent.state import Claim, Document

FOUNDERS = (
    "Apilex.ai'ın kurucu ortakları Av. Cebrail Ergül, Kemal Tamer ve Batuhan İpek, şirketin "
    "Avrupa pazarına açılacağını duyurdu"
)
FUNDING = (
    "Pre-Seed yatırım turunu tamamlayan şirket, uluslararası büyüme stratejisi kapsamında "
    "öncelikle Fransa pazarına odaklanıyor"
)
OTHER = (
    "Şirketin ürün yol haritasında Türkçe içtihat araması ve sözleşme incelemesi için yeni "
    "modüller yer alıyor"
)


def _doc(doc_id: str) -> Document:
    return Document(
        id=doc_id,
        url=f"https://{doc_id}.com",
        canonical_url=f"https://{doc_id}.com",
        domain=f"{doc_id}.com",
        origin_id=f"o-{doc_id}",
    )


def _claims(*items: tuple[str, str]) -> dict[str, Claim]:
    return {
        f"c{i}": Claim(
            id=f"c{i}",
            subq_id="s1",
            doc_id=doc_id,
            origin_id=f"o-{doc_id}",
            text=quote,
            quote=quote,
        )
        for i, (doc_id, quote) in enumerate(items)
    }


def _merge(docs: dict[str, Document], claims: dict[str, Claim]) -> dict[str, str]:
    return merge_syndicated_origins(docs, claims, min_shared_quotes=2, min_quote_words=12)


def test_pages_sharing_several_long_quotes_become_one_origin() -> None:
    docs = {d: _doc(d) for d in ("d69", "d51", "d84", "d9")}
    claims = _claims(
        ("d69", FOUNDERS),
        ("d69", FUNDING),
        ("d51", FOUNDERS),
        ("d51", FUNDING.upper()),  # folding and spacing do not matter
        ("d84", "  " + FOUNDERS),
        ("d84", FUNDING),
        ("d9", OTHER),
    )
    moved = _merge(docs, claims)
    assert moved == {"o-d69": "o-d51", "o-d84": "o-d51"}
    assert {docs[d].origin_id for d in ("d69", "d51", "d84")} == {"o-d51"}
    assert docs["d9"].origin_id == "o-d9"
    assert {c.origin_id for c in claims.values() if c.doc_id != "d9"} == {"o-d51"}


def test_one_shared_quote_is_not_enough() -> None:
    """Two independent articles may quote the same sentence of a statement."""
    docs = {d: _doc(d) for d in ("a", "b")}
    claims = _claims(("a", FOUNDERS), ("a", OTHER), ("b", FOUNDERS), ("b", FUNDING))
    assert _merge(docs, claims) == {}
    assert docs["a"].origin_id != docs["b"].origin_id


def test_short_quotes_do_not_count() -> None:
    docs = {d: _doc(d) for d in ("a", "b")}
    claims = _claims(
        ("a", "The market grew 8.8%"),
        ("a", "Founded in Istanbul"),
        ("b", "The market grew 8.8%"),
        ("b", "Founded in Istanbul"),
    )
    assert _merge(docs, claims) == {}


def test_documents_already_sharing_an_origin_are_left_alone() -> None:
    docs = {d: _doc(d) for d in ("a", "b")}
    docs["b"].origin_id = "o-a"
    claims = _claims(("a", FOUNDERS), ("a", FUNDING), ("b", FOUNDERS), ("b", FUNDING))
    claims["c2"].origin_id = claims["c3"].origin_id = "o-a"
    assert _merge(docs, claims) == {}
