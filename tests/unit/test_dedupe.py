from footnote.graph.nodes.dedupe import dedupe_results
from footnote.models import Family
from tests.fakes import make_result

LONG_TITLE = "Blood pressure response to chronic intake of coffee and caffeine: a meta-analysis"


def test_distinct_results_untouched() -> None:
    results = [make_result(1), make_result(2)]
    assert dedupe_results(results) == results


def test_merge_by_doi_keeps_most_trusted_and_fills_gaps() -> None:
    merged = dedupe_results(
        [
            make_result(1, doi="10.1/x", trust=0.85, citation_count=10, venue=None),
            make_result(2, doi="10.1/x", trust=0.9, citation_count=12, venue="J Hypertens"),
        ]
    )
    assert len(merged) == 1
    (result,) = merged
    assert result.title == "Result 2"  # higher trust wins
    assert result.citation_count == 12
    assert result.venue == "J Hypertens"
    assert result.alt_urls == ["https://example.org/academic/1"]


def test_merge_by_arxiv_and_pmid() -> None:
    assert (
        len(dedupe_results([make_result(1, arxiv_id="2301.1"), make_result(2, arxiv_id="2301.1")]))
        == 1
    )
    assert len(dedupe_results([make_result(1, pmid="123"), make_result(2, pmid="123")])) == 1


def test_merge_by_url_variants() -> None:
    merged = dedupe_results(
        [
            make_result(1, url="https://www.reuters.com/a/"),
            make_result(2, url="http://reuters.com/a"),
        ]
    )
    assert len(merged) == 1


def test_preprint_merges_into_published_version_by_title() -> None:
    preprint = make_result(
        1,
        title=LONG_TITLE,
        url="https://arxiv.org/abs/2301.1",
        doi="10.48550/arxiv.2301.1",
        arxiv_id="2301.1",
        is_preprint=True,
        trust=0.75,
    )
    published = make_result(2, title=LONG_TITLE + ".", doi="10.1097/hjh.1", trust=0.9)
    (result,) = dedupe_results([preprint, published])
    assert result.doi == "10.1097/hjh.1"
    assert result.arxiv_id == "2301.1"
    assert not result.is_preprint
    assert "https://arxiv.org/abs/2301.1" in result.alt_urls


def test_messy_duplicate_record_merges_by_title() -> None:
    """The live OpenAlex case: same paper twice, one record with no DOI and a wrong venue."""
    good = make_result(1, title=LONG_TITLE, doi="10.1097/01.hjh.1", venue="Journal of Hypertension")
    messy = make_result(2, title=LONG_TITLE, venue="Socio-Environmental Systems Modeling")
    (result,) = dedupe_results([good, messy])
    assert result.venue == "Journal of Hypertension"


def test_different_published_dois_never_merge_on_title() -> None:
    a = make_result(1, title=LONG_TITLE, doi="10.1/a")
    b = make_result(2, title=LONG_TITLE, doi="10.1/b")
    assert len(dedupe_results([a, b])) == 2


def test_short_generic_titles_do_not_merge() -> None:
    assert (
        len(dedupe_results([make_result(1, title="Editorial"), make_result(2, title="Editorial")]))
        == 2
    )


def test_families_do_not_merge_on_title() -> None:
    a = make_result(1, title=LONG_TITLE)
    b = make_result(2, Family.NEWS, title=LONG_TITLE)
    assert len(dedupe_results([a, b])) == 2


def test_syndicated_wire_story_collapses_with_all_outlet_urls() -> None:
    title = "Federal Reserve holds interest rates steady as inflation cools"
    ap = make_result(
        1, Family.NEWS, title=f"{title} | AP News", url="https://apnews.com/a", trust=0.95
    )
    npr = make_result(2, Family.NEWS, title=f"{title} - NPR", url="https://npr.org/b", trust=0.9)
    other = make_result(
        3, Family.NEWS, title="Stocks rally after the decision", url="https://bbc.com/c"
    )
    merged = dedupe_results([npr, ap, other])
    assert len(merged) == 2
    story = merged[0]
    assert story.url == "https://apnews.com/a"
    assert story.alt_urls == ["https://npr.org/b"]
