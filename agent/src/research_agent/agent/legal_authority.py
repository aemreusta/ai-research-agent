"""Conservative admission policy for rule-bearing claims in regulatory research.

Official press releases and overview pages are primary evidence of announcements, but can omit
exceptions in the operative rule. A publisher score alone does not establish legal authority.
This heuristic sacrifices recall; it does not certify that an admitted rule is correct.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from research_agent.agent.state import Claim, Document, ResearchState

_LEGAL_DOMAIN = re.compile(
    r"\blaw\b|regulat|mevzuat|hukuk|data protection|compliance", re.IGNORECASE
)
_RULE = re.compile(
    r"\b(?:must|required|shall|obligation\w*|deadline\w*|prohibit\w*|grace period|"
    r"threshold\w*|exemption\w*|penalt\w*|notify|notification|enforce\w*|appl\w*|transition\w*)\b|"
    r"zorun|yükümlü|bildiril|bildirim|ceza|muaf|eşik|kayıt|yürürlü|uygulan|gerekm|izin",
    re.IGNORECASE,
)
_OVERVIEW = re.compile(
    r"/(?:news|press|press-releases|policies|faqs|timeline)(?:/|$)|"
    r"\b(?:press release|news release|overview|implementation timeline)\b",
    re.IGNORECASE,
)


def legal_evidence_gap(claim: Claim, document: Document, state: ResearchState) -> str | None:
    if (
        state.analysis.answer_type == "profile"
        or not _LEGAL_DOMAIN.search(state.analysis.domain)
        or not (claim.time_sensitive or _RULE.search(f"{claim.text} {claim.attribute or ''}"))
    ):
        return None
    if not document.score.is_primary:
        return (
            "Legal authority: retrieve the primary rule or regulator guidance; "
            "commentary alone is insufficient."
        )
    if _OVERVIEW.search(f"{urlsplit(document.canonical_url).path} {document.title}"):
        return (
            "Legal authority: retrieve the operative rule or detailed regulator guidance; "
            "an overview may omit exceptions."
        )
    # A regulator can publish an old enforcement decision on a new webpage. Its case year,
    # unlike the webpage date, cannot establish today's monetary penalty range.
    decision = re.search(r"/(20\d{2})[-/]\d+/?$", urlsplit(document.canonical_url).path)
    penalty_amount = re.search(r"penalt|fine\b|ceza", claim.text, re.IGNORECASE) and re.search(
        r"\d[\d.,]*\s*(?:TL|Türk lirası|USD|EUR|dollars|euros)|[$€₺]\s*\d",
        claim.text,
        re.IGNORECASE,
    )
    target_year = (state.analysis.time_scope.start or state.as_of).year
    if decision and penalty_amount and int(decision[1]) < target_year:
        return (
            "Legal authority: an earlier enforcement decision does not establish the current "
            "penalty range; retrieve the applicable year's official penalty schedule."
        )
    return None
