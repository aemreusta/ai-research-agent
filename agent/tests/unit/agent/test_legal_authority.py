"""Legal advice must not turn commentary or an official overview into a complete rule."""

from datetime import date
from uuid import uuid4

import pytest

from research_agent.agent.legal_authority import legal_evidence_gap
from research_agent.agent.state import Claim, Document, ResearchState, SourceScore


@pytest.mark.parametrize(
    "url,primary,text,held",
    [
        (
            "https://firm.example/saas",
            False,
            "Processors must report breaches within 24 hours.",
            True,
        ),
        (
            "https://council.example/press/press-releases/2026/rules",
            True,
            "The transparency deadline is 2 December 2026.",
            True,
        ),
        (
            "https://commission.example/en/policies/ai",
            True,
            "Providers must implement transparency solutions by December 2026.",
            True,
        ),
        (
            "https://commission.example/en/ai-act/timeline/implementation",
            True,
            "The grace period ends in December 2026.",
            True,
        ),
        (
            "https://kvkk.gov.tr/Icerik/7938/Standart-Sozlesmeler",
            True,
            "Standart sözleşmeler 5 iş günü içinde bildirilmelidir.",
            False,
        ),
        (
            "https://eur-lex.europa.eu/eli/reg/2024/1689/2026-07-27/eng",
            True,
            "The deadline applies only to existing systems.",
            False,
        ),
        (
            "https://kvkk.gov.tr/SharedFolderServer/guide.pdf",
            True,
            "The controller must implement appropriate security measures.",
            False,
        ),
        ("https://news.example/launch", False, "The consultation opened on 1 May 2026.", False),
    ],
)
def test_rule_authority_policy(url: str, primary: bool, text: str, held: bool) -> None:
    state = ResearchState(
        run_id=uuid4(), question="2026 legal obligations?", as_of=date(2026, 9, 17)
    )
    state.analysis.domain = "data protection regulation"
    claim = Claim(id="c1", subq_id="s1", doc_id="d1", origin_id="o1", text=text, quote=text)
    document = Document(
        id="d1",
        url=url,
        canonical_url=url,
        domain="example",
        content=text,
        score=SourceScore(is_primary=primary),
    )
    assert bool(legal_evidence_gap(claim, document, state)) is held
    state.analysis.domain = "software engineering"
    assert legal_evidence_gap(claim, document, state) is None
