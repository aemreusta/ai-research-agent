"""Conservative applicability checks independent of the model's source authority score.

Publication, effect and observation dates are different facts. Undated content and old mutable
values can stay in the audit ledger, but cannot establish a current answer or close a facet.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from research_agent.agent.state import Claim, Document, ResearchState
from research_agent.agent.text import token_set
from research_agent.providers.search.base import parse_date


def evidence_date(value: str | None) -> date | None:
    if not value:
        return None
    if parsed := parse_date(value):
        return parsed
    # Explicit day/month/year (the Turkish legal corpus commonly uses 11/03/2021).
    match = re.fullmatch(r"\s*(\d{1,2})[/.](\d{1,2})[/.](20\d{2})\s*", value)
    if match:
        try:
            return date(int(match[3]), int(match[2]), int(match[1]))
        except ValueError:
            return None
    for fmt in ("%B %d, %Y", "%d %B %Y", "%b %d, %Y", "%d %b %Y"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    if match := re.fullmatch(r"(?:FY\s*)?(20\d{2})", value.strip(), re.IGNORECASE):
        return date(int(match[1]), 1, 1)
    return None


def assess_claim_freshness(claim: Claim, document: Document, state: ResearchState) -> None:
    mutable_terms = re.compile(
        r"threshold|price|revenue|market size|valuation|annual turnover|balance sheet|"
        r"eşik|bilanço|ciro|pazar büyüklüğü|fiyat|kayıt yükümlülüğü",
        re.IGNORECASE,
    )
    claim.time_sensitive = claim.time_sensitive or bool(
        mutable_terms.search(f"{claim.attribute or ''} {claim.text}")
    )
    if not claim.time_sensitive:
        claim.freshness = "not_time_sensitive"
        claim.requires_fresh_confirmation = False
        return
    scope = state.analysis.time_scope
    target = min(scope.end, state.as_of) if scope.end else state.as_of
    start = scope.start or target - timedelta(days=365)
    # A value's own observation date wins over the publication date of a page quoting it.
    observed = evidence_date(claim.as_of) or document.published_at
    expires = evidence_date(claim.effective_until)
    if observed is None:
        claim.freshness = "undated"
    elif observed < start or observed > target or (expires is not None and expires < target):
        claim.freshness = "historical"
    else:
        claim.freshness = "current"
    claim.requires_fresh_confirmation = claim.freshness != "current"


def source_context(document: Document, quote: str, *, radius: int = 1400) -> str:
    """Bounded source context for final verification; retain qualifiers around the quote."""
    content = document.content or document.snippet
    index = content.casefold().find(quote.casefold())
    local = content[max(0, index - radius) : index + len(quote) + radius] if index >= 0 else ""
    related = document_evidence(document, quote, limit=6000)
    return local + "\n\nOther relevant source context:\n" + related


def applicability_passages(document: Document, claim: Claim) -> list[str]:
    """Find restrictions on the same precisely identified provision, including late paragraphs.

    This is a conservative trigger for a scope review, not an interpretation of the law.
    Article 50 and Article 50(2) remain distinct; unrelated conditional text does not trigger it.
    """
    reference = re.compile(r"\b(?:article|madde)\s+(\d+(?:\(\d+\))?)", re.IGNORECASE)
    references = set(reference.findall(f"{claim.entity or ''} {claim.text}"))
    if not references:
        return []
    restriction = re.compile(
        r"\b(?:only|unless|except|provided that|certain existing|existing systems|"
        r"placed on the market before|yalnızca|sadece|hariç|şartıyla)\b",
        re.IGNORECASE,
    )
    return [
        paragraph[:2400]
        for paragraph in re.split(r"\n\s*\n", document.content or document.snippet)
        if references & set(reference.findall(paragraph)) and restriction.search(paragraph)
    ][:5]


def document_evidence(document: Document, focus: str, *, limit: int = 24_000) -> str:
    """Keep relevant paragraphs and their neighbours instead of silently losing a late caveat.

    The original EU example's applicability restriction begins after character 15,500; taking
    the first 14,000 characters systematically hides it from both extractor and verifier.
    """
    content = document.content or document.snippet
    if len(content) <= limit:
        return content
    paragraphs = re.split(r"\n\s*\n", content)
    wanted = token_set(focus)
    scores = [(len(wanted & token_set(part)), index) for index, part in enumerate(paragraphs)]
    selected: set[int] = set()
    size = 0
    for score, index in sorted(scores, reverse=True):
        if not score:
            continue
        for neighbour in (index, index + 1, index - 1):
            if neighbour < 0 or neighbour >= len(paragraphs) or neighbour in selected:
                continue
            length = len(paragraphs[neighbour]) + 2
            if size + length <= limit:
                selected.add(neighbour)
                size += length
    return "\n\n".join(paragraphs[i] for i in sorted(selected)) or content[:limit]


def dated_header(content: str, url: str) -> tuple[date | None, str | None]:
    """Use explicit publication labels or a complete date in the URL; never copyright years."""
    match = re.search(
        r"(?:published(?: on)?|last updated|publication date|yayın(?:lanma)? tarihi|güncelleme)"
        r"\s*[:|\-]?\s*(20\d{2}-\d{2}-\d{2}|\d{1,2}[/.]\d{1,2}[/.]20\d{2})",
        content[:1800],
        re.IGNORECASE,
    )
    if match and (value := evidence_date(match[1])):
        return value, "content_header"
    # A standalone date immediately below a Markdown article heading is a publication marker.
    match = re.search(
        r"^#{1,2} [^\n]+\n+\s*([A-Z][a-z]+ \d{1,2}, 20\d{2}|\d{1,2} [A-Z][a-z]+ 20\d{2})\s*\n",
        content[:5000],
        re.MULTILINE,
    )
    if match and (value := evidence_date(match[1])):
        return value, "article_header"
    match = re.search(r"/(20\d{2})/(\d{2})/(\d{2})(?:/|$)", url)
    if match and (value := evidence_date(f"{match[1]}-{match[2]}-{match[3]}")):
        return value, "url_date"
    return None, None
