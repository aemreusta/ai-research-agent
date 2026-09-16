"""A deterministic tripwire for prompt injection in fetched pages (architecture v0.6 §11.4).

The structural defences do the heavy lifting: web content is fenced as untrusted data, a claim
needs a verbatim quote, and the gate re-checks every number. This adds one cheap heuristic on
top: a sentence that addresses the model ("ignore previous instructions", "you are now ...") is
never evidence about the world, so it is never admitted as a claim, even though it is, strictly,
on the page.
"""

from __future__ import annotations

import re

_PATTERNS = re.compile(
    r"(?:ignore|disregard|forget)\s+(?:all\s+|any\s+|the\s+)?(?:previous|prior|above|earlier)\s+"
    r"(?:instructions|prompts?|rules)"
    r"|\b(?:system|developer)\s+prompt\b"
    r"|\byou\s+are\s+now\b"
    r"|\bas\s+an?\s+(?:ai|language\s+model)\b"
    r"|\b(?:önceki|yukarıdaki)\s+(?:tüm\s+)?(?:talimatları|komutları)\s+(?:yok\s*say|unut|görmezden)"
    r"|\bartık\s+sen\b"
    r"|</?\s*untrusted_source",
    re.IGNORECASE,
)


def looks_like_injection(text: str) -> bool:
    return bool(_PATTERNS.search(text))
