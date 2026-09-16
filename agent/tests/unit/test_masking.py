"""Intake masking (boundary B1): the raw question never reaches the database or a provider."""

from __future__ import annotations

import pytest

from research_agent.pii.masking import MaskResult, RegexMasker


@pytest.fixture
def masker() -> RegexMasker:
    return RegexMasker()


def test_a_clean_question_is_returned_unchanged(masker: RegexMasker) -> None:
    question = "KVKK 2026 aksiyon planı için Türk SaaS şirketleri ne yapmalı?"
    result = masker.mask(question)
    assert result.text == question
    assert result.entities == []
    assert result.degraded is False


def test_a_tckn_is_replaced_by_a_numbered_placeholder(masker: RegexMasker) -> None:
    result = masker.mask("Müvekkilim 10000000146 hakkında dava var mı?")
    assert result.text == "Müvekkilim <TCKN_1> hakkında dava var mı?"
    assert [entity.kind for entity in result.entities] == ["TCKN"]


def test_the_same_value_twice_gets_the_same_placeholder(masker: RegexMasker) -> None:
    """Otherwise the model would think two different people were being discussed."""
    result = masker.mask("10000000146 ve 10000000146 aynı kişi")
    assert result.text == "<TCKN_1> ve <TCKN_1> aynı kişi"
    assert len(result.entities) == 1


def test_different_values_get_different_placeholders(masker: RegexMasker) -> None:
    result = masker.mask("10000000146 ile 19191919190 arasındaki dava")
    assert "<TCKN_1>" in result.text and "<TCKN_2>" in result.text


def test_an_eleven_digit_number_that_is_not_a_tckn_survives(masker: RegexMasker) -> None:
    """Masking every 11-digit number would mangle ordinary questions about figures."""
    result = masker.mask("Ciro 12345678901 TL olarak açıklandı")
    assert result.text == "Ciro 12345678901 TL olarak açıklandı"


@pytest.mark.parametrize(
    ("question", "placeholder"),
    [
        ("Bana ali@example.com adresinden yaz", "<EMAIL_1>"),
        ("IBAN TR330006100519786457841326 doğru mu", "<IBAN_1>"),
        ("Kart 4111 1111 1111 1111 iptal edildi", "<CREDIT_CARD_1>"),
        ("Numaram 0532 123 45 67", "<PHONE_1>"),
    ],
)
def test_sensitive_identifiers_are_masked(
    masker: RegexMasker, question: str, placeholder: str
) -> None:
    assert placeholder in masker.mask(question).text


def test_company_and_person_names_are_left_alone(masker: RegexMasker) -> None:
    """They are the subject of the research (D20); masking them would defeat the question."""
    result = masker.mask("ApilexAI ve Muhammed Bilgin hakkında ne biliyoruz?")
    assert result.text == "ApilexAI ve Muhammed Bilgin hakkında ne biliyoruz?"


def test_masked_text_contains_no_original_identifier(masker: RegexMasker) -> None:
    secret = "10000000146"
    assert secret not in masker.mask(f"TCKN {secret} için").text


def test_the_entity_record_does_not_carry_the_value(masker: RegexMasker) -> None:
    """The entity list goes into `run_events`, so it holds positions and kinds only."""
    result = masker.mask("TCKN 10000000146 için")
    assert "10000000146" not in repr(result.entities)


def test_summary_is_what_the_timeline_shows(masker: RegexMasker) -> None:
    result = masker.mask("10000000146 ve ali@example.com")
    assert result.summary() == {"TCKN": 1, "EMAIL": 1}


def test_an_empty_result_summarises_to_nothing(masker: RegexMasker) -> None:
    assert MaskResult(text="x", entities=[], degraded=False).summary() == {}


# --- Presidio --------------------------------------------------------------------------------------

import httpx  # noqa: E402

from research_agent.pii.masking import PresidioMasker  # noqa: E402


def _presidio(handler: object) -> PresidioMasker:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]
    return PresidioMasker("http://presidio-analyzer:3000", client=client)


async def test_presidio_findings_are_masked_and_checksums_still_apply() -> None:
    text = "Call Ayşe at +44 20 7946 0958, TCKN 12345678901, IBAN issue."
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        start = text.index("+44")
        return httpx.Response(
            200,
            json=[
                {"entity_type": "PHONE_NUMBER", "start": start, "end": start + 16, "score": 0.75},
                # Presidio's pattern hit on an invalid national id must not be masked.
                {
                    "entity_type": "TR_TCKN",
                    "start": text.index("1234"),
                    "end": text.index("1234") + 11,
                    "score": 0.6,
                },
            ],
        )

    result = await _presidio(handler).amask(text)
    assert result.engine == "presidio" and not result.degraded
    assert "<PHONE_1>" in result.text
    assert "12345678901" in result.text, "checksum failed, so it is not a national id"
    assert "Ayşe" in result.text, "names are kept unless configured otherwise"
    body = seen[0].read()
    assert b"TR_TCKN" in body and b"ad_hoc_recognizers" in body


async def test_presidio_can_only_add_to_the_regex_findings() -> None:
    result = await _presidio(lambda _: httpx.Response(200, json=[])).amask(
        "mail me at ali@example.com"
    )
    assert "<EMAIL_1>" in result.text


async def test_an_unreachable_presidio_degrades_to_regex() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    result = await _presidio(down).amask("TCKN 10000000146")
    assert result.degraded and result.engine == "regex"
    assert "<TCKN_1>" in result.text


async def test_overlapping_findings_are_replaced_once() -> None:
    text = "card 4111 1111 1111 1111 here"
    start = text.index("4111")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {"entity_type": "CREDIT_CARD", "start": start, "end": start + 19, "score": 1.0},
                {
                    "entity_type": "PHONE_NUMBER",
                    "start": start + 5,
                    "end": start + 14,
                    "score": 0.4,
                },
            ],
        )

    result = await _presidio(handler).amask(text)
    assert result.text == "card <CREDIT_CARD_1> here"
