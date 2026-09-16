"""Small, dependency-free text utilities shared by dedup, scoring, quotes and the gate.

Turkish is a first-class language here, which is why folding does not use `str.lower()`:
that maps "İ" to "i̇" (a combining dot that breaks equality). And because the same text mixes
languages ("ApilexAI", "AI Act", "KVKK İhlal"), folding is for *matching*, not display: every
dotted and dotless i becomes a plain "i". Distinguishing "ılık" from "ilik" is worth far less
than recognising "AI" and "ai" as the same token.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final

_TR_FOLD = str.maketrans({"İ": "i", "I": "i", "ı": "i"})
_WORD = re.compile(r"[\w%€$₺]+", re.UNICODE)
_TR_CHARS = frozenset("çğıöşüÇĞİÖŞÜ")

STOPWORDS: Final = frozenset(
    {
        # English
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "has",
        "have",
        "how",
        "in",
        "into",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "was",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
        "about",
        "do",
        "does",
        "did",
        "can",
        "should",
        "their",
        "there",
        "these",
        "those",
        # Turkish
        "ve",
        "veya",
        "ile",
        "için",
        "bir",
        "bu",
        "şu",
        "o",
        "da",
        "de",
        "ki",
        "mi",
        "mu",
        "mü",
        "ne",
        "nedir",
        "nelerdir",
        "nasil",
        "neden",
        "hangi",
        "olan",
        "olarak",
        "gibi",
        "daha",
        "en",
        "çok",
        "her",
        "ise",
        "ama",
        "fakat",
        "kadar",
        "sonra",
        "önce",
        "üzere",
        "göre",
        "yani",
        "hem",
        "ya",
    }
)

_TR_MARKERS: Final = frozenset(
    {
        "ve",
        "için",
        "bir",
        "nedir",
        "nelerdir",
        "nasil",
        "hangi",
        "ile",
        "olan",
        "şirketleri",
        "plani",
        "yili",
        "hakkinda",
        "göre",
        "mi",
    }
)
_EN_MARKERS: Final = frozenset(
    {"the", "and", "what", "which", "how", "of", "for", "is", "are", "in", "changes", "size"}
)


def fold(text: str) -> str:
    """Case-fold for comparison: NFC, lower case, all i-variants unified."""
    return unicodedata.normalize("NFC", text.translate(_TR_FOLD)).lower()


def normalise_space(text: str) -> str:
    return " ".join(text.split())


def tokens(text: str, *, keep_stopwords: bool = False) -> list[str]:
    words = _WORD.findall(fold(text))
    if keep_stopwords:
        return words
    return [word for word in words if word not in STOPWORDS]


def token_set(text: str) -> frozenset[str]:
    return frozenset(tokens(text))


def token_jaccard(left: str, right: str) -> float:
    a, b = token_set(left), token_set(right)
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def overlap_ratio(query: str, text: str) -> float:
    """Share of the query's content words that appear in the text (0..1)."""
    wanted = token_set(query)
    if not wanted:
        return 0.0
    return len(wanted & token_set(text)) / len(wanted)


def detect_language(text: str) -> str:
    """ "tr" or "en" - enough for this system, which answers in the question's language (D4).

    Turkish-specific letters are decisive; otherwise stopword votes decide.
    """
    if any(char in _TR_CHARS for char in text):
        return "tr"
    words = set(tokens(text, keep_stopwords=True))
    tr_votes = len(words & _TR_MARKERS)
    en_votes = len(words & _EN_MARKERS)
    return "tr" if tr_votes > en_votes else "en"


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    boundary = cut.rfind(". ")
    return (cut[: boundary + 1] if boundary > limit * 0.6 else cut) + " …"
