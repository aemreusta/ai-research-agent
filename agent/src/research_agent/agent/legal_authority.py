"""Conservative admission policy for rule-bearing claims in regulatory research.

Official press releases and overview pages are primary evidence of announcements, but can omit
exceptions in the operative rule. A publisher score alone does not establish legal authority.
This heuristic sacrifices recall; it does not certify that an admitted rule is correct.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from research_agent.agent.state import Claim, Document, ResearchState

_LEGAL_DOMAIN = re.compile(r"\blaw\b|legal|regulat|mevzuat|hukuk|data protection", re.IGNORECASE)
_RULE = re.compile(
    r"\b(?:must|required|shall|obligation\w*|deadline\w*|prohibit\w*|grace period|"
    r"threshold\w*|exemption\w*|penalt\w*|notify|notification|enforce\w*|appl(?:y|ies|ication))\b|"
    r"zorunlu|yükümlü|bildiril|bildirim|ceza|muaf|eşik|kayıt şart|son kayıt|yürürlü|uygulan",
    re.IGNORECASE,
)
_OVERVIEW = re.compile(
    r"/(?:news|press|press-releases|policies|faqs|timeline)(?:/|$)|"
    r"\b(?:press release|news release|overview|implementation timeline)\b",
    re.IGNORECASE,
)


def legal_evidence_gap(claim: Claim, document: Document, state: ResearchState) -> str | None:
    if not _LEGAL_DOMAIN.search(state.analysis.domain) or not _RULE.search(
        f"{claim.text} {claim.attribute or ''}"
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
    return None
