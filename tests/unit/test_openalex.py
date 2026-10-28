from datetime import date

import httpx
import pytest
import respx
from pydantic import SecretStr

from footnote.config import Settings, SourcesConfig
from footnote.models import Family, QueryPlan
from footnote.sources.academic.openalex import (
    API_URL,
    OpenAlexAdapter,
    abstract_from_inverted_index,
)
from footnote.sources.base import run_adapter
from tests.conftest import load_fixture

PLAN = QueryPlan(intent="academic", queries={Family.ACADEMIC: ["numpy array programming"]})


@pytest.fixture
async def adapter(settings: Settings, sources: SourcesConfig) -> OpenAlexAdapter:
    config = next(s for s in sources.native() if s.id == "openalex")
    return OpenAlexAdapter(config, settings, httpx.AsyncClient())  # type: ignore[return-value]


@respx.mock
async def test_maps_works_to_results(adapter: OpenAlexAdapter) -> None:
    route = respx.get(API_URL).respond(json=load_fixture("openalex_works.json"))

    results = await adapter.search(PLAN)

    params = route.calls.last.request.url.params
    assert params["search"] == "numpy array programming"
    assert params["mailto"] == "dev@example.org"
    assert "api_key" not in params

    assert len(results) == 2  # the untitled work is skipped
    numpy, preprint = results
    assert numpy.title == "Array programming with <i>NumPy</i>"  # HTML is normalize's job
    assert numpy.url == "https://www.nature.com/articles/s41586-020-2649-2"
    assert numpy.doi == "10.1038/s41586-020-2649-2"
    assert numpy.pmid == "32939066"
    assert numpy.venue == "Nature"
    assert numpy.authors == ["Charles R. Harris", "K. Jarrod Millman"]
    assert numpy.snippet == "Array programming provides a powerful syntax."
    assert numpy.published_at == date(2020, 9, 16)
    assert numpy.citation_count == 15000
    assert numpy.trust == 0.9
    assert not numpy.is_preprint

    assert preprint.is_preprint
    assert preprint.url == "https://openalex.org/W3000000001"
    assert preprint.snippet == ""


@respx.mock
async def test_time_window_becomes_filter(adapter: OpenAlexAdapter) -> None:
    route = respx.get(API_URL).respond(json={"results": []})
    plan = PLAN.model_copy(update={"time_window": (date(2024, 1, 1), date(2024, 12, 31))})

    await adapter.search(plan)

    assert route.calls.last.request.url.params["filter"] == (
        "from_publication_date:2024-01-01,to_publication_date:2024-12-31"
    )


@respx.mock
async def test_runs_each_query_and_dedupes(adapter: OpenAlexAdapter) -> None:
    route = respx.get(API_URL).respond(json=load_fixture("openalex_works.json"))
    plan = QueryPlan(intent="academic", queries={Family.ACADEMIC: ["q1", "q2"]})

    results = await adapter.search(plan)

    assert route.call_count == 2
    assert len(results) == 2


@respx.mock
async def test_http_error_becomes_failed_result_without_leaking_key(
    settings: Settings, sources: SourcesConfig
) -> None:
    keyed = settings.model_copy(update={"openalex_api_key": SecretStr("sk-secret-123")})
    config = next(s for s in sources.native() if s.id == "openalex")
    adapter = OpenAlexAdapter(config, keyed, httpx.AsyncClient())
    respx.get(API_URL).respond(status_code=503)

    outcome = await run_adapter(adapter, PLAN, limit_seconds=1)

    assert not outcome.ok
    assert outcome.error == "HTTP 503"
    assert "sk-secret" not in outcome.model_dump_json()


def test_abstract_from_inverted_index_orders_by_position() -> None:
    assert abstract_from_inverted_index({"world": [1], "hello": [0, 2]}) == "hello world hello"
    assert abstract_from_inverted_index(None) == ""
