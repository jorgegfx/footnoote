from footnote.config import SourcesConfig
from footnote.graph.nodes.synthesize import NO_RESULTS
from footnote.graph.nodes.verify import ClaimVerdict, Verification
from footnote.models import Answer, Citation
from tests.evals.run import check_citations
from tests.evals.scoring import (
    QuestionScore,
    distinct_outlets,
    load_questions,
    percentile,
    says_insufficient,
    summarize,
    whitelist_violations,
)
from tests.fakes import FakeChatModel, fake_models, make_result


def answer(text: str, cited: list[int], **overrides: object) -> Answer:
    sources = [make_result(1), make_result(2)]
    fields: dict[str, object] = {
        "text": text,
        "sources": sources,
        "citations": [Citation(index=i, result_id=sources[i - 1].id) for i in cited],
    }
    return Answer.model_validate(fields | overrides)


def test_golden_questions_load_and_cover_every_category() -> None:
    questions = load_questions()
    assert len(questions) >= 30
    assert len({q.id for q in questions}) == len(questions)
    assert {q.category for q in questions} == {
        "recent_news",
        "established_science",
        "contested",
        "no_good_answer",
    }
    assert all(q.insufficient for q in questions if q.category == "no_good_answer")


def test_says_insufficient() -> None:
    assert says_insufficient(answer(NO_RESULTS, []))
    assert says_insufficient(answer("Some prose without citations.", []))
    assert says_insufficient(answer("The sources don't contain enough evidence [1].", [1]))
    assert not says_insufficient(answer("Coffee raises blood pressure briefly [1].", [1]))


def test_whitelist_violations_only_checks_discovery_sources(sources: SourcesConfig) -> None:
    ok = make_result(1, source_id="news_search", url="https://www.reuters.com/a")
    bad = make_result(
        2,
        source_id="gdelt",
        url="https://apnews.com/b",
        alt_urls=["https://blog.example/copy"],
    )
    native = make_result(3, source_id="guardian", url="https://guardian.example/x")
    result = answer("x", [], sources=[ok, bad, native])
    assert whitelist_violations(result, sources) == ["https://blog.example/copy"]


def test_distinct_outlets_prefers_venue_then_host() -> None:
    sources = [
        make_result(1, venue="Nature"),
        make_result(2, venue="nature"),
        make_result(3, url="https://www.reuters.com/x"),
    ]
    assert distinct_outlets(answer("x", [], sources=sources)) == 2


def test_percentile_and_summary() -> None:
    assert percentile([], 50) == 0.0
    assert percentile([100, 200, 300, 400], 50) == 200
    assert percentile([100, 200, 300, 400], 95) == 400

    scores = [
        QuestionScore(
            id="a",
            category="contested",
            latency_ms=100,
            insufficient_correct=True,
            citations_checked=4,
            citations_supported=3,
        ),
        QuestionScore(
            id="b",
            category="no_good_answer",
            latency_ms=300,
            whitelist_violations=["https://x"],
            error="RuntimeError",
        ),
    ]
    summary = summarize(scores)
    assert summary["citation_precision"] == 0.75
    assert summary["whitelist_violations"] == 1
    assert summary["insufficient_accuracy"] == 0.5
    assert summary["errors"] == 1
    assert summary["latency_ms_p95"] == 300


async def test_check_citations_counts_supported_citations() -> None:
    strong = FakeChatModel(
        structured=[
            Verification(verdicts=[ClaimVerdict(claim=1, supported_by=[1]), ClaimVerdict(claim=2)])
        ]
    )
    models = fake_models(FakeChatModel(), strong)
    result = answer("Coffee helps [1][2]. Tea too [2]. Uncited summary.", [1, 2])
    assert await check_citations(models, "q", result) == (3, 1)
