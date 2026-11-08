"""Field mapping and request shape for each native adapter, against recorded fixtures."""

from datetime import date

import httpx
import pytest
import respx
from pydantic import SecretStr

from footnote.config import Settings, SourcesConfig
from footnote.models import Family, QueryPlan
from footnote.sources import build_adapters
from footnote.sources.base import NativeAdapter, native_adapter_class, run_adapter
from footnote.sources.news import gdelt, guardian, nyt
from tests.conftest import load_fixture

NEWS = QueryPlan(queries={Family.NEWS: ["fed rates", "central bank"]})
WINDOW = (date(2026, 9, 1), date(2026, 10, 15))


def make(source_id: str, settings: Settings, sources: SourcesConfig) -> NativeAdapter:
    cls = native_adapter_class(source_id)
    config = next((s for s in sources.sources if s.id == source_id), None)  # even if disabled
    assert cls is not None
    assert config is not None
    return cls(config, settings, httpx.AsyncClient(), sources)


def keyed(settings: Settings, **keys: str) -> Settings:
    return settings.model_copy(update={k: SecretStr(v) for k, v in keys.items()})


# --- News ---


@respx.mock
async def test_guardian(settings: Settings, sources: SourcesConfig) -> None:
    route = respx.get(guardian.API_URL).respond(json=load_fixture("guardian.json"))
    adapter = make("guardian", keyed(settings, guardian_api_key="gk"), sources)

    results = await adapter.search(NEWS.model_copy(update={"time_window": WINDOW}))

    params = route.calls.last.request.url.params
    assert params["q"] == "(fed rates) OR (central bank)"
    assert params["api-key"] == "gk"
    assert (params["from-date"], params["to-date"]) == ("2026-09-01", "2026-10-15")
    story, live = results
    assert story.title == "Bank of England holds interest rates at 4%"
    assert story.snippet.startswith("<p>Policymakers vote 6-3")  # normalize strips HTML
    assert story.published_at == date(2026, 10, 1)
    assert story.authors == ["Richard Partington"]
    assert story.venue == "The Guardian"
    assert live.snippet == ""


# --- NYT ---


@respx.mock
async def test_nyt(settings: Settings, sources: SourcesConfig) -> None:
    route = respx.get(nyt.API_URL).respond(json=load_fixture("nyt.json"))
    adapter = make("nyt", keyed(settings, nyt_api_key="nk"), sources)

    results = await adapter.search(NEWS.model_copy(update={"time_window": WINDOW}))

    params = route.calls.last.request.url.params
    assert params["q"] == "fed rates"  # best query only: 5 requests/minute
    assert (params["begin_date"], params["end_date"]) == ("20260901", "20261015")
    story, opinion = results
    assert story.title == "Fed Holds Rates Steady"
    assert story.authors == ["Jeanna Smialek"]
    assert story.published_at == date(2026, 10, 1)
    assert opinion.snippet == "An argument about rates."
    assert opinion.authors == []


# --- GDELT ---


@respx.mock
async def test_gdelt_filters_to_whitelist(settings: Settings, sources: SourcesConfig) -> None:
    route = respx.get(gdelt.API_URL).respond(json=load_fixture("gdelt.json"))

    results = await make("gdelt", settings, sources).search(NEWS)

    query = route.calls.last.request.url.params["query"]
    assert query.startswith("fed rates (domain:reuters.com OR domain:apnews.com")
    assert [r.url for r in results] == [
        "https://www.reuters.com/markets/rates/fed-2026-10-01/",
        "https://apnews.com/article/fed-rates-abc123",
    ]
    reuters = results[0]
    assert reuters.published_at == date(2026, 10, 1)
    assert reuters.trust == 0.8  # GDELT's trust caps the outlet's 0.95
    assert reuters.snippet == ""


@respx.mock
async def test_gdelt_plain_text_error(settings: Settings, sources: SourcesConfig) -> None:
    respx.get(gdelt.API_URL).respond(text="Your search contained a phrase that was too short.")
    outcome = await run_adapter(make("gdelt", settings, sources), NEWS, 1)
    assert outcome.error == "GDELT rejected the query"


# --- Errors ---


@respx.mock
async def test_rate_limit_reported_plainly(settings: Settings, sources: SourcesConfig) -> None:
    respx.get(nyt.API_URL).respond(status_code=429, json={"fault": "slow down"})
    adapter = make("nyt", keyed(settings, nyt_api_key="n"), sources)
    outcome = await run_adapter(adapter, NEWS, 1)
    assert outcome.error == "rate limited"


# --- Wiring ---


def test_build_adapters_skips_keyed_sources_without_keys(
    settings: Settings, sources: SourcesConfig
) -> None:
    ids = {a.id for a in build_adapters(settings, sources, httpx.AsyncClient())}
    assert ids == {"news_search"}  # SEARCH_API_KEY set in fixture; GDELT is disabled

    with_keys = keyed(settings, guardian_api_key="g", nyt_api_key="n")
    assert {"guardian", "nyt"} <= {
        a.id for a in build_adapters(with_keys, sources, httpx.AsyncClient())
    }


@pytest.mark.parametrize("source_id", ["guardian", "nyt", "gdelt"])
def test_every_shipped_native_source_has_an_adapter(source_id: str, sources: SourcesConfig) -> None:
    assert native_adapter_class(source_id) is not None
    assert source_id in {s.id for s in sources.sources if s.kind == "native"}
