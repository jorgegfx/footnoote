import json
from datetime import date

import httpx
import respx

from footnote.config import SourcesConfig
from footnote.models import Family, QueryPlan
from footnote.sources.search.base import WebSearchAdapter
from footnote.sources.search.tavily import API_URL, TavilyProvider
from tests.conftest import load_fixture

PLAN = QueryPlan(
    intent="news",
    queries={Family.NEWS: ["fed rate decision"]},
    time_window=(date(2026, 9, 1), date(2026, 10, 15)),
)


@respx.mock
async def test_tavily_request_carries_whitelist_and_window(sources: SourcesConfig) -> None:
    route = respx.post(API_URL).respond(json={"results": []})
    adapter = WebSearchAdapter(Family.NEWS, TavilyProvider("key", httpx.AsyncClient()), sources)

    await adapter.search(PLAN)

    request = route.calls.last.request
    body = json.loads(request.content)
    assert request.headers["Authorization"] == "Bearer key"
    assert body["topic"] == "news"
    assert body["include_domains"] == sources.search_domains(Family.NEWS)
    assert body["start_date"] == "2026-09-01"
    assert body["end_date"] == "2026-10-15"


@respx.mock
async def test_results_are_rechecked_against_whitelist(sources: SourcesConfig) -> None:
    respx.post(API_URL).respond(json=load_fixture("tavily_news.json"))
    adapter = WebSearchAdapter(Family.NEWS, TavilyProvider("key", httpx.AsyncClient()), sources)

    results = await adapter.search(PLAN)

    assert [r.url for r in results] == [
        "https://www.reuters.com/markets/us/fed-holds-rates-2026-10-01/",
        "https://www.bbc.co.uk/news/business-123",
    ]
    reuters, bbc = results
    assert (reuters.source_id, reuters.trust) == ("reuters", 0.95)
    assert reuters.published_at == date(2026, 10, 1)
    assert (bbc.source_id, bbc.trust) == ("bbc", 0.9)
    assert bbc.published_at == date(2026, 10, 2)


@respx.mock
async def test_academic_search_uses_general_topic_and_academic_domains(
    sources: SourcesConfig,
) -> None:
    route = respx.post(API_URL).respond(json=load_fixture("tavily_news.json"))
    adapter = WebSearchAdapter(Family.ACADEMIC, TavilyProvider("key", httpx.AsyncClient()), sources)
    plan = QueryPlan(intent="academic", queries={Family.ACADEMIC: ["crispr off-target"]})

    results = await adapter.search(plan)

    body = json.loads(route.calls.last.request.content)
    assert body["topic"] == "general"
    assert "nature.com" in body["include_domains"]
    assert "reuters.com" not in body["include_domains"]
    assert results == []  # news URLs aren't on the academic whitelist
