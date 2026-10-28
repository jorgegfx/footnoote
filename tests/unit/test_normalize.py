from footnote.graph.nodes.normalize import normalize, normalize_result
from footnote.graph.state import GraphState
from footnote.models import result_id
from footnote.text import content_words, html_to_text, truncate
from tests.fakes import make_result


def test_html_to_text() -> None:
    assert html_to_text("Array programming with <i>NumPy</i>") == "Array programming with NumPy"
    assert html_to_text("a<script>evil()</script> b &amp; c") == "a b & c"
    assert html_to_text("  plain \n text ") == "plain text"


def test_truncate_on_word_boundary() -> None:
    assert truncate("one two three", 100) == "one two three"
    assert truncate("one two three", 9) == "one two…"


def test_content_words_drops_stopwords() -> None:
    assert content_words("Does coffee raise the blood pressure?") == {
        "coffee",
        "raise",
        "blood",
        "pressure",
    }


def test_normalize_result_drops_non_http_urls() -> None:
    assert normalize_result(make_result(1, url="javascript:alert(1)")) is None


def test_normalize_result_canonicalizes_ids_and_urls() -> None:
    result = normalize_result(
        make_result(
            1,
            url="https://WWW.Nature.com/articles/x?utm_source=feed&id=2#section",
            doi="https://doi.org/10.1038/ABC",
            arxiv_id="2301.00001v3",
            authors=["<b>Ada</b> Lovelace", ""],
            snippet="<p>An &amp; B</p>",
        )
    )
    assert result is not None
    assert result.url == "https://www.nature.com/articles/x?id=2"
    assert result.doi == "10.1038/abc"
    assert result.arxiv_id == "2301.00001"
    assert result.authors == ["Ada Lovelace"]
    assert result.snippet == "An & B"
    assert result.id == result_id(doi="10.1038/abc", url=result.url)


async def test_normalize_keeps_order_and_drops_invalid() -> None:
    state: GraphState = {
        "question": "q",
        "max_results": 2,
        "results": [
            make_result(1),
            make_result(2, url="ftp://example.org/x"),
            make_result(3, title="<b></b>"),
            make_result(4),
        ],
    }
    update = await normalize(state)
    assert [r.title for r in update["candidates"]] == ["Result 1", "Result 4"]
    assert "normalize" in update["timings"]
