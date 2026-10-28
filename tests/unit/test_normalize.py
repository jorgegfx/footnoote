from footnote.graph.nodes.normalize import html_to_text, normalize, normalize_result, truncate
from footnote.graph.state import GraphState
from tests.fakes import make_result


def test_html_to_text() -> None:
    assert html_to_text("Array programming with <i>NumPy</i>") == "Array programming with NumPy"
    assert html_to_text("a<script>evil()</script> b &amp; c") == "a b & c"
    assert html_to_text("  plain \n text ") == "plain text"


def test_truncate_on_word_boundary() -> None:
    assert truncate("one two three", 100) == "one two three"
    assert truncate("one two three", 9) == "one two…"


def test_normalize_result_drops_non_http_urls() -> None:
    assert normalize_result(make_result(1, url="javascript:alert(1)")) is None


async def test_normalize_dedupes_sorts_by_trust_and_caps() -> None:
    state: GraphState = {
        "question": "q",
        "max_results": 2,
        "results": [
            make_result(1, trust=0.5),
            make_result(2, trust=0.9, title="<b>Two</b>"),
            make_result(2, trust=0.9),
            make_result(3, trust=0.7),
        ],
    }
    update = await normalize(state)
    assert [r.title for r in update["ranked"]] == ["Two", "Result 3"]
    assert "normalize" in update["timings"]
