import asyncio
import json
import time
from datetime import date

import httpx
import pytest
import respx

from footnote.config import SourcesConfig
from footnote.models import Family, QueryPlan
from footnote.sources.search.base import WebHit, WebSearchAdapter
from footnote.sources.search.tavily import API_URL, TavilyProvider
from tests.conftest import load_fixture

PLAN = QueryPlan(
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


class SlowProvider:
    """Each query takes `delay`; queries listed in `failing` raise."""

    name = "slow"

    def __init__(self, delay: float, failing: frozenset[str] = frozenset()) -> None:
        self.delay = delay
        self.failing = failing

    async def search(
        self,
        query: str,
        *,
        domains: list[str],
        family: Family,
        time_window: tuple[date, date] | None,
        max_results: int,
    ) -> list[WebHit]:
        await asyncio.sleep(self.delay)
        if query in self.failing:
            raise httpx.ConnectError("down")
        return [WebHit(title=query, url=f"https://www.reuters.com/{query}")]


THREE = QueryPlan(queries={Family.NEWS: ["a", "b", "c"]})


async def test_queries_run_concurrently(sources: SourcesConfig) -> None:
    adapter = WebSearchAdapter(Family.NEWS, SlowProvider(0.1), sources)
    start = time.perf_counter()
    results = await adapter.search(THREE)
    assert time.perf_counter() - start < 0.25  # three 0.1s queries, not 0.3s+
    assert [r.title for r in results] == ["a", "b", "c"]


async def test_one_failed_query_keeps_the_others(sources: SourcesConfig) -> None:
    adapter = WebSearchAdapter(Family.NEWS, SlowProvider(0, frozenset({"b"})), sources)
    assert [r.title for r in await adapter.search(THREE)] == ["a", "c"]


async def test_all_queries_failing_is_an_error(sources: SourcesConfig) -> None:
    adapter = WebSearchAdapter(Family.NEWS, SlowProvider(0, frozenset({"a", "b", "c"})), sources)
    with pytest.raises(httpx.ConnectError):
        await adapter.search(THREE)
