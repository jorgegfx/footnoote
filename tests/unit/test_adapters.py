"""Field mapping and request shape for each native adapter, against recorded fixtures."""

from datetime import date

import httpx
import pytest
import respx
from pydantic import SecretStr

from footnote.config import Settings, SourcesConfig
from footnote.models import Family, QueryPlan
from footnote.sources import build_adapters
from footnote.sources.academic import arxiv, crossref, pubmed, semantic_scholar
from footnote.sources.base import NativeAdapter, native_adapter_class, run_adapter
from footnote.sources.news import gdelt, guardian, nyt
from tests.conftest import FIXTURES, load_fixture
from tests.fakes import make_result

ACADEMIC = QueryPlan(intent="academic", queries={Family.ACADEMIC: ["caffeine blood pressure"]})
BIOMEDICAL = ACADEMIC.model_copy(update={"biomedical": True})
NEWS = QueryPlan(intent="news", queries={Family.NEWS: ["fed rates", "central bank"]})
WINDOW = (date(2026, 9, 1), date(2026, 10, 15))


def make(source_id: str, settings: Settings, sources: SourcesConfig) -> NativeAdapter:
    cls = native_adapter_class(source_id)
    config = sources.get(source_id)
    assert cls is not None
    assert config is not None
    return cls(config, settings, httpx.AsyncClient(), sources)


def keyed(settings: Settings, **keys: str) -> Settings:
    return settings.model_copy(update={k: SecretStr(v) for k, v in keys.items()})


# --- Semantic Scholar ---


@respx.mock
async def test_semantic_scholar(settings: Settings, sources: SourcesConfig) -> None:
    route = respx.get(semantic_scholar.API_URL).respond(json=load_fixture("semantic_scholar.json"))
    adapter = make("semantic_scholar", settings, sources)

    results = await adapter.search(ACADEMIC.model_copy(update={"time_window": WINDOW}))

    request = route.calls.last.request
    assert request.url.params["publicationDateOrYear"] == "2026-09-01:2026-10-15"
    assert "x-api-key" not in request.headers
    paper, preprint, no_doi = results
    assert paper.url == "https://doi.org/10.1016/j.clnesp.2023.09.923"
    assert (paper.doi, paper.pmid, paper.citation_count) == (
        "10.1016/j.clnesp.2023.09.923",
        "38057002",
        12,
    )
    assert paper.published_at == date(2023, 9, 30)
    assert preprint.is_preprint
    assert preprint.arxiv_id == "2301.00001"
    assert no_doi.url == "https://www.semanticscholar.org/paper/nodoi"
    assert no_doi.published_at == date(2019, 1, 1)  # year only
    assert no_doi.venue is None


@respx.mock
async def test_semantic_scholar_uses_one_query_without_key(
    settings: Settings, sources: SourcesConfig
) -> None:
    route = respx.get(semantic_scholar.API_URL).respond(json={"data": []})
    plan = QueryPlan(intent="academic", queries={Family.ACADEMIC: ["a", "b", "c"]})

    await make("semantic_scholar", settings, sources).search(plan)
    assert route.call_count == 1

    await make("semantic_scholar", keyed(settings, semantic_scholar_api_key="k"), sources).search(
        plan
    )
    assert route.call_count == 4
    assert route.calls.last.request.headers["x-api-key"] == "k"


@respx.mock
async def test_rate_limit_reported_plainly(settings: Settings, sources: SourcesConfig) -> None:
    respx.get(semantic_scholar.API_URL).respond(status_code=429, json={"message": "slow down"})
    outcome = await run_adapter(make("semantic_scholar", settings, sources), ACADEMIC, 1)
    assert outcome.error == "rate limited"


# --- PubMed ---


@respx.mock
async def test_pubmed(settings: Settings, sources: SourcesConfig) -> None:
    search = respx.get(pubmed.ESEARCH_URL).respond(json=load_fixture("pubmed_esearch.json"))
    fetch = respx.get(pubmed.EFETCH_URL).respond(
        content=(FIXTURES / "pubmed_efetch.xml").read_bytes()
    )
    plan = QueryPlan(
        intent="academic",
        queries={Family.ACADEMIC: ["caffeine blood pressure", "coffee hypertension"]},
        biomedical=True,
        time_window=WINDOW,
    )

    results = await make("pubmed", settings, sources).search(plan)

    params = search.calls.last.request.url.params
    assert params["term"] == "(caffeine blood pressure) OR (coffee hypertension)"
    assert (params["mindate"], params["maxdate"]) == ("2026/09/01", "2026/10/15")
    assert params["email"] == "dev@example.org"
    assert fetch.calls.last.request.url.params["id"] == "38057002,30947761"

    first = results[0]
    assert first.pmid == "38057002"
    assert first.url == "https://pubmed.ncbi.nlm.nih.gov/38057002/"
    assert first.doi == "10.1016/j.clnesp.2023.09.923"
    assert first.title.startswith("The effects of caffeine supplementation on blood pressure")
    assert first.venue == "Clinical nutrition ESPEN"
    assert first.published_at == date(2023, 9, 30)  # electronic date preferred
    assert first.snippet.startswith("Background and aims: Hypertension")
    assert first.authors[0] == "Seyed Ali Abbas-Hashemi"
    assert len(results) == 2


def test_pubmed_only_accepts_biomedical_plans(settings: Settings, sources: SourcesConfig) -> None:
    adapter = make("pubmed", settings, sources)
    assert not adapter.accepts(ACADEMIC)
    assert adapter.accepts(BIOMEDICAL)


@respx.mock
async def test_pubmed_no_hits_skips_efetch(settings: Settings, sources: SourcesConfig) -> None:
    respx.get(pubmed.ESEARCH_URL).respond(json={"esearchresult": {"idlist": []}})
    fetch = respx.get(pubmed.EFETCH_URL)
    assert await make("pubmed", settings, sources).search(BIOMEDICAL) == []
    assert not fetch.called


# --- arXiv ---


@respx.mock
async def test_arxiv(settings: Settings, sources: SourcesConfig) -> None:
    route = respx.get(arxiv.API_URL).respond(content=(FIXTURES / "arxiv.xml").read_bytes())

    results = await make("arxiv", settings, sources).search(ACADEMIC)

    assert route.call_count == 1
    assert len(results) == 2
    first = results[0]
    assert first.arxiv_id == "2209.15001"
    assert first.url == "https://arxiv.org/abs/2209.15001"
    assert first.title == "Dilated Neighborhood Attention Transformer"
    assert first.published_at == date(2022, 9, 29)
    assert first.authors == ["Ali Hassani", "Humphrey Shi"]
    assert first.is_preprint
    assert first.snippet.startswith("Transformers are quickly becoming")


def test_arxiv_query_builder() -> None:
    assert (
        arxiv.build_search_query(["CRISPR off-target effects", "Cas9 specificity!"], None)
        == "(all:CRISPR AND all:off-target AND all:effects) OR (all:Cas9 AND all:specificity)"
    )
    assert arxiv.build_search_query(["q"], WINDOW) == (
        "((all:q)) AND submittedDate:[202609010000 TO 202610152359]"
    )
    assert arxiv.build_search_query(["!!!"], None) == ""


# --- Guardian ---


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


# --- Crossref enrichment ---


@respx.mock
async def test_crossref_fills_only_missing_fields(settings: Settings) -> None:
    doi = "10.1038/s41586-020-2649-2"
    route = respx.get(f"{crossref.API_URL}{doi}").respond(json=load_fixture("crossref_work.json"))
    sparse = make_result(1, doi=doi, venue=None, citation_count=None, published_at=None)
    complete = make_result(
        2,
        doi="10.1/complete",
        venue="V",
        citation_count=1,
        published_at=date(2020, 1, 1),
        authors=["A"],
    )
    no_doi = make_result(3, venue=None)
    enricher = crossref.CrossrefEnricher(settings, httpx.AsyncClient())

    enriched = await enricher.enrich([sparse, complete, no_doi])

    assert route.call_count == 1
    assert route.calls.last.request.url.params["mailto"] == "dev@example.org"
    first = enriched[0]
    assert first.venue == "Nature"
    assert first.published_at == date(2020, 9, 16)
    assert first.citation_count == 25618
    assert first.authors[0] == "Charles R. Harris"
    assert first.snippet == sparse.snippet  # existing snippet kept
    assert enriched[1:] == [complete, no_doi]


@respx.mock
async def test_crossref_failure_leaves_result_unchanged(settings: Settings) -> None:
    respx.get(url__startswith=crossref.API_URL).respond(status_code=404)
    result = make_result(1, doi="10.1/missing", venue=None)
    enricher = crossref.CrossrefEnricher(settings, httpx.AsyncClient())
    assert await enricher.enrich([result]) == [result]


# --- Wiring ---


def test_build_adapters_skips_keyed_sources_without_keys(
    settings: Settings, sources: SourcesConfig
) -> None:
    ids = {a.id for a in build_adapters(settings, sources, httpx.AsyncClient())}
    assert {"openalex", "semantic_scholar", "pubmed", "arxiv", "gdelt"} <= ids
    assert {"news_search", "academic_search"} <= ids  # SEARCH_API_KEY set in fixture
    assert not {"guardian", "nyt", "crossref"} & ids

    with_keys = keyed(settings, guardian_api_key="g", nyt_api_key="n")
    assert {"guardian", "nyt"} <= {
        a.id for a in build_adapters(with_keys, sources, httpx.AsyncClient())
    }


@pytest.mark.parametrize("source_id", ["openalex", "semantic_scholar", "pubmed", "arxiv", "gdelt"])
def test_every_shipped_native_source_has_an_adapter(source_id: str, sources: SourcesConfig) -> None:
    assert native_adapter_class(source_id) is not None
    assert sources.get(source_id) is not None


@respx.mock
async def test_crossref_looks_up_ranking_gaps_first(settings: Settings) -> None:
    """Abstract-only gaps (common for OpenAlex) must not starve citation lookups."""
    route = respx.get(url__startswith=crossref.API_URL).respond(
        json=load_fixture("crossref_work.json")
    )
    abstract_only = [
        make_result(i, doi=f"10.1/{i}", snippet="", citation_count=5, published_at=date(2020, 1, 1))
        for i in range(crossref.MAX_LOOKUPS)
    ]
    needs_cites = make_result(99, doi="10.1/cites", citation_count=None)
    enricher = crossref.CrossrefEnricher(settings, httpx.AsyncClient())

    enriched = await enricher.enrich([*abstract_only, needs_cites])

    assert route.call_count == crossref.MAX_LOOKUPS
    assert enriched[-1].citation_count == 25618
    assert enriched[-2] == abstract_only[-1]  # lowest priority fell outside the budget
