"""Search: provider requests, and the gateway's cache / retry / fallback decisions."""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from research_agent.agent.budget import BudgetMeter
from research_agent.agent.runtime import MemoryCache, MemoryCallRecorder, MemoryEventSink
from research_agent.config.schema import BudgetSettings, FetchSettings, SearchSettings
from research_agent.errors import ErrorCode
from research_agent.providers.fetch import ContentFetcher
from research_agent.providers.search.base import SearchHit, SearchProviderError, SearchRequest
from research_agent.providers.search.brave import BraveProvider
from research_agent.providers.search.fake import CorpusPage, FakeSearchProvider
from research_agent.providers.search.gateway import SearchGateway
from research_agent.providers.search.tavily import TavilyProvider


def _errors(events: MemoryEventSink) -> list[dict[str, Any]]:
    return [event for event in events.events if event.get("error_code")]


# --- providers ---------------------------------------------------------------------------------


async def test_tavily_request_and_mapping() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "url": "https://kvkk.gov.tr/a",
                        "title": "KVKK",
                        "content": "snippet",
                        "raw_content": "full text",
                        "score": 0.9,
                        "published_date": "2026-03-12T10:00:00Z",
                    },
                    {"title": "no url"},
                ]
            },
        )

    provider = TavilyProvider(
        "tvly-k", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    hits = await provider.search(SearchRequest("kvkk 2026", language="tr"), timeout=5)

    body = json.loads(seen[0].content)
    assert seen[0].headers["Authorization"] == "Bearer tvly-k"
    assert body["include_raw_content"] == "markdown"
    assert body["country"] == "turkey"
    assert body["search_depth"] == "basic"
    assert len(hits) == 1
    assert hits[0].content == "full text"
    assert hits[0].published_at is not None and hits[0].published_at.isoformat() == "2026-03-12"


async def test_brave_request_and_html_stripping() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "web": {
                    "results": [
                        {
                            "url": "https://x.com",
                            "title": "<strong>EU</strong> AI Act",
                            "description": "The <strong>act</strong> &amp; timeline",
                            "extra_snippets": ["more"],
                            "page_age": "2026-02-01T00:00:00",
                        },
                    ]
                }
            },
        )

    provider = BraveProvider(
        "BSA-k", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    hits = await provider.search(SearchRequest("eu ai act", language="tr"), timeout=5)
    assert seen[0].headers["X-Subscription-Token"] == "BSA-k"
    assert seen[0].url.params["country"] == "TR"
    assert hits[0].title == "EU AI Act"
    assert hits[0].snippet == "The act & timeline more"
    assert hits[0].content is None


@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [
        (401, ErrorCode.SEARCH_AUTH, False),
        (429, ErrorCode.SEARCH_RATE_LIMIT, True),
        (432, ErrorCode.SEARCH_RATE_LIMIT, False),
        (502, ErrorCode.SEARCH_PROVIDER_ERROR, True),
    ],
)
async def test_search_status_mapping(status: int, code: ErrorCode, retryable: bool) -> None:
    provider = TavilyProvider(
        "k",
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(status, text="x"))
        ),
    )
    with pytest.raises(SearchProviderError) as caught:
        await provider.search(SearchRequest("q"), timeout=5)
    assert (caught.value.code, caught.value.retryable) == (code, retryable)


# --- gateway -------------------------------------------------------------------------------------

PAGE = CorpusPage(
    url="https://kvkk.gov.tr/duyuru",
    title="KVKK duyurusu",
    text="KVKK 2026 yılında veri sorumlularına yeni yükümlülükler getirdi.",
)


class Harness:
    def __init__(self, *providers: FakeSearchProvider, retries: int = 2) -> None:
        self.events = MemoryEventSink(uuid.uuid4(), node="search", iteration=1)
        self.recorder = MemoryCallRecorder()
        self.meter = BudgetMeter(BudgetSettings(max_searches=10))
        self.cache = MemoryCache()
        self.sleeps: list[float] = []

        async def sleep(seconds: float) -> None:
            self.sleeps.append(seconds)

        self.gateway = SearchGateway(
            providers={p.name: p for p in providers},
            chain=["tavily", "brave"],
            settings=SearchSettings(retries=retries),
            cache=self.cache,
            recorder=self.recorder,
            meter=self.meter,
            sleep=sleep,
        )

    async def search(self, query: str = "kvkk 2026", **kwargs: object) -> object:
        return await self.gateway.search(SearchRequest(query), events=self.events, **kwargs)  # type: ignore[arg-type]

    def codes(self) -> list[str]:
        return [e["error_code"] for e in self.events.events if e.get("error_code")]


async def test_a_successful_search_is_announced_in_the_case_format() -> None:
    h = Harness(FakeSearchProvider("tavily", corpus=[PAGE]))
    outcome = await h.search()
    assert outcome.hits and outcome.provider == "tavily"  # type: ignore[attr-defined]
    event = next(e for e in h.events.events if e["event_type"] == "search_called")
    assert event["data"]["display"] == "[Search] Received 1 results."
    assert h.meter.searches == 1


async def test_the_second_identical_search_is_served_from_cache() -> None:
    fake = FakeSearchProvider("tavily", corpus=[PAGE])
    h = Harness(fake)
    await h.search("KVKK   2026")
    outcome = await h.search("kvkk 2026")
    assert outcome.cache_hit  # type: ignore[attr-defined]
    assert len(fake.requests) == 1
    assert h.meter.searches == 2, "cache hits still count, so re-runs decide identically"
    assert [r.cache_hit for r in h.recorder.search_calls] == [False, True]


async def test_timeouts_are_retried_then_the_fallback_answers() -> None:
    tavily = FakeSearchProvider(
        "tavily",
        failures=[SearchProviderError(ErrorCode.SEARCH_TIMEOUT, "tavily", "slow", True)] * 3,
    )
    brave = FakeSearchProvider("brave", corpus=[PAGE])
    h = Harness(tavily, brave)
    outcome = await h.search()

    assert outcome.provider == "brave"  # type: ignore[attr-defined]
    assert len(tavily.requests) == 3
    assert h.sleeps == [1.0, 2.0]
    assert h.codes() == ["SEARCH_TIMEOUT", "SEARCH_TIMEOUT", "SEARCH_TIMEOUT"]
    final = _errors(h.events)[-1]
    assert final["data"]["decision"] == "fallback->brave"
    assert h.meter.searches == 1, "one logical search, however many providers it took"


async def test_a_rejected_key_is_not_retried() -> None:
    tavily = FakeSearchProvider(
        "tavily",
        failures=[SearchProviderError(ErrorCode.SEARCH_AUTH, "tavily", "bad key", False, 401)],
    )
    h = Harness(tavily, FakeSearchProvider("brave", corpus=[PAGE]))
    outcome = await h.search()
    assert len(tavily.requests) == 1
    assert outcome.provider == "brave"  # type: ignore[attr-defined]
    assert "key rejected" in _errors(h.events)[0]["data"]["decision"]


async def test_all_providers_failing_is_an_outcome_not_an_exception() -> None:
    failing = [SearchProviderError(ErrorCode.SEARCH_PROVIDER_ERROR, "x", "down", False)]
    h = Harness(
        FakeSearchProvider("tavily", failures=list(failing)),
        FakeSearchProvider("brave", failures=list(failing)),
    )
    outcome = await h.search()
    assert outcome.hits == []  # type: ignore[attr-defined]
    assert outcome.failed  # type: ignore[attr-defined]
    assert [e["data"]["decision"] for e in h.events.events if e.get("error_code")][
        -1
    ] == "mark query FAILED"


async def test_an_empty_result_tries_the_other_provider() -> None:
    h = Harness(FakeSearchProvider("tavily", corpus=[]), FakeSearchProvider("brave", corpus=[PAGE]))
    outcome = await h.search()
    assert outcome.provider == "brave"  # type: ignore[attr-defined]
    assert h.codes() == ["SEARCH_EMPTY"]


async def test_empty_everywhere_is_search_empty() -> None:
    h = Harness(FakeSearchProvider("tavily"), FakeSearchProvider("brave"))
    outcome = await h.search()
    assert outcome.error_code is ErrorCode.SEARCH_EMPTY  # type: ignore[attr-defined]
    assert not outcome.failed  # type: ignore[attr-defined]


async def test_preferred_provider_goes_first_for_diversity() -> None:
    tavily = FakeSearchProvider("tavily", corpus=[PAGE])
    brave = FakeSearchProvider("brave", corpus=[PAGE])
    h = Harness(tavily, brave)
    outcome = await h.search(preferred="brave")
    assert outcome.provider == "brave"  # type: ignore[attr-defined]
    assert tavily.requests == []


async def test_no_search_key_at_all_is_actionable() -> None:
    h = Harness()
    outcome = await h.search()
    assert outcome.error_code is ErrorCode.SEARCH_AUTH  # type: ignore[attr-defined]
    assert "Settings" in h.events.events[0]["data"]["outcome"]


# --- fetch ---------------------------------------------------------------------------------------

HTML = """<html><head><title>Karar</title>
<meta property="article:published_time" content="2026-04-02"></head>
<body><nav>menu menu</nav><article><h1>Kurul Kararı</h1>
<p>Kişisel Verileri Koruma Kurulu, 2026 yılı için yeni bir rehber yayımladı. Rehber, veri
sorumlularının yurt dışına aktarım süreçlerini ayrıntılı biçimde açıklamaktadır.</p>
<p>Rehberde ayrıca standart sözleşmelerin bildirim süresinin beş iş günü olduğu belirtiliyor.</p>
</article><footer>footer</footer></body></html>"""


async def test_fetch_extracts_main_text() -> None:
    fetcher = ContentFetcher(
        FetchSettings(),
        cache=MemoryCache(),
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200, text=HTML, headers={"content-type": "text/html; charset=utf-8"}
                )
            )
        ),
    )
    page = await fetcher.fetch("https://kvkk.gov.tr/karar")
    assert page.ok
    assert "beş iş günü" in page.text
    assert "menu menu" not in page.text


async def test_fetch_refuses_binary_and_failed_responses() -> None:
    for response in (
        httpx.Response(200, content=b"%PDF", headers={"content-type": "application/pdf"}),
        httpx.Response(404, text="missing"),
    ):
        fetcher = ContentFetcher(
            FetchSettings(retries=0),
            client=httpx.AsyncClient(transport=httpx.MockTransport(lambda _, r=response: r)),
        )
        page = await fetcher.fetch("https://x.com/doc")
        assert not page.ok and page.error


async def test_fetch_is_cached() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, text=HTML, headers={"content-type": "text/html"})

    cache = MemoryCache()
    fetcher = ContentFetcher(
        FetchSettings(),
        cache=cache,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    await fetcher.fetch("https://kvkk.gov.tr/karar")
    again = await fetcher.fetch("https://kvkk.gov.tr/karar")
    assert again.ok and calls == 1


def test_search_hit_is_immutable() -> None:
    hit = SearchHit(url="u", provider="p", rank=1)
    with pytest.raises(ValidationError):
        hit.url = "v"  # type: ignore[misc]
