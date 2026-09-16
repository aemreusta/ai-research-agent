"""Verbatim quote verification: no quote in the document, no claim (architecture v0.6 §11.1).

This single check does double duty. It stops the extractor from inventing evidence, and it is
the structural defence against prompt injection: an instruction hidden in a page can only ever
become a claim if the page literally says it - and then it is reported with its source (§11.4).

Matching tolerates what copying from HTML does to text (whitespace, case, quote styles, a word
or two changed) but never a different number: numerals must match exactly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from research_agent.agent.text import fold

MIN_QUOTE_CHARS = 20
FUZZY_THRESHOLD = 0.88

_PUNCT = str.maketrans(
    {
        "\u201c": '"',
        "\u201d": '"',
        "\u201e": '"',  # curly double quotes
        "\u2019": "'",
        "\u2018": "'",  # curly single quotes
        "\u2013": "-",
        "\u2014": "-",  # en and em dash
        "\u00a0": " ",  # no-break space
    }
)
_WORD = re.compile(r"\w+")
_DIGITS = re.compile(r"\d")

# Numbers written as words are numbers too: "beş iş günü" must not verify "on iş günü".
# Folded spelling (every i-variant is "i"), so these compare against `_words()` output.
NUMBER_WORDS = frozenset(
    {
        "sifir",
        "bir",
        "iki",
        "üç",
        "dört",
        "beş",
        "alti",
        "yedi",
        "sekiz",
        "dokuz",
        "on",
        "yirmi",
        "otuz",
        "kirk",
        "elli",
        "altmiş",
        "yetmiş",
        "seksen",
        "doksan",
        "yüz",
        "bin",
        "milyon",
        "milyar",
        "trilyon",
        "yarim",
        "çeyrek",
        "zero",
        "one",
        "two",
        "three",
        "four",
        "five",
        "six",
        "seven",
        "eight",
        "nine",
        "ten",
        "eleven",
        "twelve",
        "twenty",
        "thirty",
        "forty",
        "fifty",
        "sixty",
        "seventy",
        "eighty",
        "ninety",
        "hundred",
        "thousand",
        "million",
        "billion",
        "trillion",
        "half",
        "quarter",
        "first",
        "second",
        "third",
        "fourth",
        "fifth",
        "dozen",
    }
)


def _is_numeric(word: str) -> bool:
    return bool(_DIGITS.search(word)) or word in NUMBER_WORDS


@dataclass(frozen=True, slots=True)
class QuoteCheck:
    ok: bool
    method: str
    similarity: float = 0.0


def _words(text: str) -> list[str]:
    return _WORD.findall(fold(text.translate(_PUNCT)))


def verify_quote(quote: str, content: str) -> QuoteCheck:
    if len(quote.strip()) < MIN_QUOTE_CHARS:
        return QuoteCheck(False, "too_short")
    quote_words = _words(quote)
    content_words = _words(content)
    if not quote_words or not content_words:
        return QuoteCheck(False, "empty")

    joined_quote = " ".join(quote_words)
    joined_content = " ".join(content_words)
    if joined_quote in joined_content:
        return QuoteCheck(True, "exact", 1.0)

    numbers = [word for word in quote_words if _is_numeric(word)]
    size = len(quote_words)
    anchors = {quote_words[0], quote_words[min(1, size - 1)], quote_words[-1]}
    best = 0.0
    for index, word in enumerate(content_words):
        if word not in anchors:
            continue
        # The anchor may be the quote's first, second or last word; try windows around it that
        # are one word shorter or longer than the quote (a word dropped or added in copying).
        starts = {index, index - 1, index - size + 1, index - size + 2}
        for start in starts:
            if start < 0:
                continue
            for length in (size - 1, size, size + 1):
                window = content_words[start : start + length]
                if len(window) < max(3, size - 1):
                    continue
                # Numbers are facts: a window that lacks any of the quote's numerals is not it.
                if numbers and not all(number in window for number in numbers):
                    continue
                ratio = SequenceMatcher(None, quote_words, window, autojunk=False).ratio()
                best = max(best, ratio)
                if ratio >= FUZZY_THRESHOLD:
                    return QuoteCheck(True, "fuzzy", ratio)
    return QuoteCheck(False, "not_found", best)
