from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
import respx
from fastapi.testclient import TestClient

from footnote.config import Settings
from footnote.graph.nodes.plan import PlanOutput
from footnote.graph.nodes.rank import RelevanceScore, RelevanceScores
from footnote.main import create_app
from footnote.models import Family
from footnote.sources.academic.openalex import API_URL as OPENALEX_URL
from footnote.sources.search.base import WebHit
from tests.conftest import FIXTURES, load_fixture
from tests.fakes import FakeChatModel, fake_models


class FakeSearchProvider:
    name = "fake"

    def __init__(self) -> None:
        self.calls: list[tuple[str, Family]] = []

    async def search(
        self,
        query: str,
        *,
        domains: list[str],
        family: Family,
        time_window: tuple[date, date] | None,
        max_results: int,
    ) -> list[WebHit]:
        self.calls.append((query, family))
        if family != Family.NEWS:
            return []
        return [
            WebHit(title="Reuters story", url="https://www.reuters.com/a", content="Story."),
            WebHit(title="Off-list", url="https://blog.example/b", content="Untrusted."),
        ]


def all_relevant(count: int) -> RelevanceScores:
    return RelevanceScores(scores=[RelevanceScore(index=i, score=8) for i in range(1, count + 1)])


@pytest.fixture
def api_settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"sources_path": Path(FIXTURES / "sources.yaml")})


@pytest.fixture
def fast() -> FakeChatModel:
    return FakeChatModel()


@pytest.fixture
def strong() -> FakeChatModel:
    return FakeChatModel()


@pytest.fixture
def client(
    api_settings: Settings, fast: FakeChatModel, strong: FakeChatModel
) -> Iterator[TestClient]:
    app = create_app(api_settings, fake_models(fast, strong), FakeSearchProvider())
    with respx.mock(assert_all_called=False) as mock:
        mock.get(OPENALEX_URL).respond(json=load_fixture("openalex_works.json"))
        with TestClient(app) as test_client:
            yield test_client


def test_search_returns_cited_answer(
    client: TestClient, fast: FakeChatModel, strong: FakeChatModel
) -> None:
    fast.structured = [
        PlanOutput(intent="both", news_queries=["numpy news"], academic_queries=["numpy"]),
        all_relevant(3),
    ]
    strong.texts = ["NumPy is widely used [1][3]."]

    response = client.post("/api/search", json={"question": "How popular is NumPy?"})

    assert response.status_code == 200
    body = response.json()
    urls = [s["url"] for s in body["sources"]]
    assert "https://www.reuters.com/a" in urls
    assert "https://blog.example/b" not in urls
    # Equal relevance, so trust × quality decides: the highly cited paper leads.
    assert body["sources"][0]["title"] == "Array programming with NumPy"  # HTML stripped
    assert body["text"] == "NumPy is widely used [1][3]."
    assert [c["index"] for c in body["citations"]] == [1, 3]
    assert body["source_errors"] == {}
    assert {"plan", "rank", "synthesize", "total", "source:openalex"} <= set(body["timings_ms"])


def test_families_override_restricts_sources(
    client: TestClient, fast: FakeChatModel, strong: FakeChatModel
) -> None:
    fast.structured = [
        PlanOutput(intent="both", news_queries=["n"], academic_queries=["a"]),
        all_relevant(1),
    ]
    strong.texts = ["A story [1]."]

    response = client.post(
        "/api/search", json={"question": "anything at all", "families": ["news"], "max_results": 1}
    )

    assert response.status_code == 200
    assert [s["family"] for s in response.json()["sources"]] == ["news"]


@pytest.mark.parametrize(
    "payload",
    [
        {"question": "hi"},
        {"question": "x" * 501},
        {"question": "   valid?   ", "max_results": 0},
        {"question": "valid question", "families": []},
        {"question": "valid question", "families": ["blogs"]},
        {
            "question": "valid question",
            "time_window": {"start": "2024-02-01", "end": "2024-01-01"},
        },
    ],
)
def test_request_validation(client: TestClient, payload: dict[str, object]) -> None:
    assert client.post("/api/search", json=payload).status_code == 422


def test_empty_plan_is_422_with_message(client: TestClient, fast: FakeChatModel) -> None:
    fast.structured = [PlanOutput(intent="news")]
    response = client.post("/api/search", json={"question": "asdf qwer"})
    assert response.status_code == 422
    assert "rephrasing" in response.json()["detail"]


def test_unconfigured_llm_is_503(api_settings: Settings) -> None:
    app = create_app(api_settings, search_provider=FakeSearchProvider())
    with TestClient(app) as test_client:
        response = test_client.post("/api/search", json={"question": "valid question"})
    assert response.status_code == 503
    assert "LLM_PROVIDER" in response.json()["detail"]


def test_sources_and_health(client: TestClient) -> None:
    sources = client.get("/api/sources").json()
    assert {"id": "openalex", "family": "academic", "kind": "native", "trust": 0.9} | {
        "domains": []
    } in sources

    health = client.get("/health").json()
    assert health["status"] == "ok"
    assert health["llm_configured"] is False
    assert health["sources"] == {
        "openalex": True,
        "crossref": True,
        "news_search": True,
        "academic_search": True,
    }


def test_health_reports_missing_keys(settings: Settings) -> None:
    """The shipped trust list: keyless sources are ready, keyed ones report False."""
    with TestClient(create_app(settings, search_provider=FakeSearchProvider())) as test_client:
        status = test_client.get("/health").json()["sources"]
    assert status["openalex"]
    assert status["pubmed"]
    assert status["arxiv"]
    assert status["guardian"] is False
    assert status["nyt"] is False
