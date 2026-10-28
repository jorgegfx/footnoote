import pytest

from footnote.citations import clean_citations


@pytest.mark.parametrize(
    ("text", "expected_text", "expected_used"),
    [
        ("A [1]. B [2].", "A [1]. B [2].", [1, 2]),
        ("A [1, 3].", "A [1][3].", [1, 3]),
        ("A [1][1].", "A [1][1].", [1]),
        ("A [9].", "A.", []),
        ("A [2, 9].", "A [2].", [2]),
        ("A [0].", "A.", []),
        ("See [the docs](https://x.org) [1].", "See [the docs](https://x.org) [1].", [1]),
        ("No citations here.", "No citations here.", []),
    ],
)
def test_clean_citations(text: str, expected_text: str, expected_used: list[int]) -> None:
    assert clean_citations(text, source_count=3) == (expected_text, expected_used)
