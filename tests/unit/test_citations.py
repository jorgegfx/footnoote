import pytest

from footnote.citations import (
    cited_indices,
    clean_citations,
    keep_citations,
    split_sentences,
    tidy,
)


@pytest.mark.parametrize(
    ("text", "expected_text", "expected_used"),
    [
        ("A [1]. B [2].", "A [1]. B [2].", [1, 2]),
        ("A [1, 3].", "A [1][3].", [1, 3]),
        ("A [1][1].", "A [1].", [1]),
        ("A [9][2].", "A [2].", [2]),
        ("A [1] [2, 1].", "A [1][2].", [1, 2]),
        ("A [9].", "A.", []),
        ("A [2, 9].", "A [2].", [2]),
        ("A [0].", "A.", []),
        ("See [the docs](https://x.org) [1].", "See [the docs](https://x.org) [1].", [1]),
        ("No citations here.", "No citations here.", []),
    ],
)
def test_clean_citations(text: str, expected_text: str, expected_used: list[int]) -> None:
    assert clean_citations(text, source_count=3) == (expected_text, expected_used)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("One [1]. Two [2].", ["One [1]. ", "Two [2]."]),
        ("One. [1] Two? [2][3] Three!", ["One. [1] ", "Two? [2][3] ", "Three!"]),
        ("Intro:\n- item [1]\n- other [2]\n", ["Intro:\n", "- item [1]\n", "- other [2]\n"]),
        ("Version 2.5 is out [1].", ["Version 2.5 is out [1]."]),
        ("No terminal punctuation [1]", ["No terminal punctuation [1]"]),
        ("", []),
    ],
)
def test_split_sentences(text: str, expected: list[str]) -> None:
    pieces = split_sentences(text)
    assert pieces == expected
    assert "".join(pieces) == text


def test_cited_indices_are_unique_and_ordered() -> None:
    assert cited_indices("A [3][1, 3] and [2].") == [3, 1, 2]
    assert cited_indices("See [docs](https://x.org).") == []


@pytest.mark.parametrize(
    ("sentence", "keep", "expected"),
    [
        ("A [1][2].", {2}, "A [2]."),
        ("A [1, 2, 3].", {1, 3}, "A [1][3]."),
        ("A [1].", set(), "A."),
        ("See [docs](https://x.org) [1].", {1}, "See [docs](https://x.org) [1]."),
    ],
)
def test_keep_citations(sentence: str, keep: set[int], expected: str) -> None:
    assert keep_citations(sentence, keep) == expected


def test_tidy_removes_bare_bullets_and_blank_runs() -> None:
    text = "Intro [1].\n\n- \n- kept [2]\n1. \n\n\n\nEnd [3].  \n"
    assert tidy(text) == "Intro [1].\n\n- kept [2]\n\nEnd [3]."
