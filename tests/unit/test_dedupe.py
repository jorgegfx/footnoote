from footnote.graph.nodes.dedupe import dedupe_results
from tests.fakes import make_result

LONG_TITLE = "Federal Reserve holds interest rates steady as inflation cools"


def test_distinct_results_untouched() -> None:
    results = [make_result(1), make_result(2)]
    assert dedupe_results(results) == results


def test_merge_keeps_most_trusted_and_fills_gaps() -> None:
    merged = dedupe_results(
        [
            make_result(1, url="https://www.reuters.com/a", trust=0.95, venue=None),
            make_result(
                2,
                url="https://news.example.com/x",
                alt_urls=["https://reuters.com/a"],
                trust=0.8,
                venue="Reuters",
                snippet="A much longer snippet with the full story.",
            ),
        ]
    )
    assert len(merged) == 1
    (result,) = merged
    assert result.title == "Result 1"  # higher trust wins
    assert result.venue == "Reuters"
    assert result.snippet == "A much longer snippet with the full story."
    assert result.alt_urls == ["https://news.example.com/x"]


def test_merge_by_url_variants() -> None:
    merged = dedupe_results(
        [
            make_result(1, url="https://www.reuters.com/a/"),
            make_result(2, url="http://reuters.com/a"),
        ]
    )
    assert len(merged) == 1


def test_short_generic_titles_do_not_merge() -> None:
    assert (
        len(
            dedupe_results(
                [make_result(1, title="Live updates"), make_result(2, title="Live updates")]
            )
        )
        == 2
    )


def test_syndicated_wire_story_collapses_with_all_outlet_urls() -> None:
    ap = make_result(1, title=f"{LONG_TITLE} | AP News", url="https://apnews.com/a", trust=0.95)
    npr = make_result(2, title=f"{LONG_TITLE} - NPR", url="https://npr.org/b", trust=0.9)
    other = make_result(3, title="Stocks rally after the decision", url="https://bbc.com/c")
    merged = dedupe_results([npr, ap, other])
    assert len(merged) == 2
    story = merged[0]
    assert story.url == "https://apnews.com/a"
    assert story.alt_urls == ["https://npr.org/b"]
