import json
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import respx
from fastapi.testclient import TestClient
from pydantic import SecretStr

from footnote.config import Settings
from footnote.graph.nodes.plan import PlanOutput
from footnote.graph.nodes.rank import RelevanceScore, RelevanceScores
from footnote.graph.nodes.verify import ClaimVerdict, Verification
from footnote.main import create_app
from footnote.models import Family
from footnote.sources.news.guardian import API_URL as GUARDIAN_URL
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
        return [
            WebHit(title="Reuters story", url="https://www.reuters.com/a", content="Story."),
            WebHit(title="Off-list", url="https://blog.example/b", content="Untrusted."),
        ]


def all_relevant(count: int) -> RelevanceScores:
    return RelevanceScores(scores=[RelevanceScore(index=i, score=8) for i in range(1, count + 1)])


def sse_events(body: str) -> list[tuple[str, Any]]:
    events = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


@pytest.fixture
def api_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "sources_path": Path(FIXTURES / "sources.yaml"),
            "guardian_api_key": SecretStr("test-guardian-key"),
        }
    )


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
        mock.get(GUARDIAN_URL).respond(json=load_fixture("guardian.json"))
        with TestClient(app) as test_client:
            yield test_client


def test_search_returns_cited_answer(
    client: TestClient, fast: FakeChatModel, strong: FakeChatModel
) -> None:
    fast.structured = [PlanOutput(news_queries=["bank of england rates"]), all_relevant(3)]
    strong.texts = ["Rates were held [1][3]."]

    response = client.post("/api/search", json={"question": "Did the BoE hold rates?"})

    assert response.status_code == 200
    body = response.json()
    urls = [s["url"] for s in body["sources"]]
    assert "https://www.reuters.com/a" in urls
    assert "https://blog.example/b" not in urls
    assert "https://www.theguardian.com/business/2026/oct/01/bank-of-england-holds-rates" in urls
    assert {s["family"] for s in body["sources"]} == {"news"}
    assert body["text"] == "Rates were held [1][3]."
    assert [c["index"] for c in body["citations"]] == [1, 3]
    assert body["source_errors"] == {}
    assert {"plan", "rank", "synthesize", "total", "source:guardian"} <= set(body["timings_ms"])


@pytest.mark.parametrize(
    "payload",
    [
        {"question": "hi"},
        {"question": "x" * 501},
        {"question": "   valid?   ", "max_results": 0},
        {
            "question": "valid question",
            "time_window": {"start": "2024-02-01", "end": "2024-01-01"},
        },
    ],
)
def test_request_validation(client: TestClient, payload: dict[str, object]) -> None:
    assert client.post("/api/search", json=payload).status_code == 422


def test_empty_plan_is_422_with_message(client: TestClient, fast: FakeChatModel) -> None:
    fast.structured = [PlanOutput()]
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
    assert {"id": "guardian", "family": "news", "kind": "native", "trust": 0.85} | {
        "domains": []
    } in sources

    health = client.get("/health").json()
    assert health["status"] == "ok"
    assert health["llm_configured"] is False
    assert health["sources"] == {"guardian": True, "news_search": True}


def test_health_reports_missing_keys(settings: Settings) -> None:
    """The shipped trust list: keyless sources are ready, keyed ones report False."""
    with TestClient(create_app(settings, search_provider=FakeSearchProvider())) as test_client:
        status = test_client.get("/health").json()["sources"]
    assert "gdelt" not in status  # disabled in the shipped list
    assert status["news_search"]
    assert status["guardian"] is False
    assert status["nyt"] is False


def test_stream_reports_progress_then_verified_answer(
    client: TestClient, fast: FakeChatModel, strong: FakeChatModel
) -> None:
    fast.structured = [PlanOutput(news_queries=["bank of england"]), all_relevant(3)]
    strong.texts = ["Rates were held [1]. The vote was unanimous [2]."]
    strong.structured = [
        Verification(verdicts=[ClaimVerdict(claim=1, supported_by=[1]), ClaimVerdict(claim=2)])
    ]

    response = client.get("/api/search/stream", params={"q": "Did the BoE hold rates?"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = sse_events(response.text)
    names = [name for name, _ in events]
    assert names[0] == "plan"
    assert names[-2:] == ["answer", "done"]
    assert names.index("source_started") < names.index("source_done") < names.index("ranked")
    assert names.index("ranked") < names.index("token") < names.index("answer")
    done = {data["source"]: data for name, data in events if name == "source_done"}
    assert done.keys() == {"guardian", "news_search"}
    assert done["guardian"]["count"] == 2
    draft = "".join(data["text"] for name, data in events if name == "token")
    assert draft == "Rates were held [1]. The vote was unanimous [2]."
    answer = events[-2][1]
    assert answer["text"] == "Rates were held [1]."
    assert answer["unsupported_claims_removed"] == 1
    assert "verify" in answer["timings_ms"]


def test_stream_reports_empty_plan_as_error_event(client: TestClient, fast: FakeChatModel) -> None:
    fast.structured = [PlanOutput()]
    events = sse_events(client.get("/api/search/stream", params={"q": "asdf qwer"}).text)
    assert [name for name, _ in events] == ["error", "done"]
    assert events[0][1]["status"] == 422


@pytest.mark.parametrize(
    "params",
    [{"q": "hi"}, {"q": "x" * 501}, {"q": "valid", "max_results": 0}],
)
def test_stream_validation(client: TestClient, params: dict[str, object]) -> None:
    assert client.get("/api/search/stream", params=params).status_code == 422


def test_searches_are_rate_limited_per_client(api_settings: Settings, fast: FakeChatModel) -> None:
    limited = api_settings.model_copy(update={"search_rate_limit_per_minute": 1})
    fast.structured = [PlanOutput(), PlanOutput()]
    app = create_app(limited, fake_models(fast, FakeChatModel()), FakeSearchProvider())
    with TestClient(app) as test_client:
        assert test_client.post("/api/search", json={"question": "asdf qwer"}).status_code == 422
        response = test_client.get("/api/search/stream", params={"q": "asdf qwer"})
    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) > 0


def test_index_page_and_request_id(client: TestClient) -> None:
    response = client.get("/", headers={"X-Request-ID": "abc123"})
    assert response.status_code == 200
    assert 'id="search"' in response.text
    assert "/static/app.js" in response.text
    assert response.headers["X-Request-ID"] == "abc123"
    assert client.get("/static/style.css").status_code == 200
