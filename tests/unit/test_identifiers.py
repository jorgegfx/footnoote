import pytest

from footnote.identifiers import (
    clean_url,
    is_preprint_doi,
    normalize_arxiv_id,
    normalize_doi,
    url_key,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("10.1038/S41586", "10.1038/s41586"),
        ("https://doi.org/10.1038/s41586", "10.1038/s41586"),
        ("http://dx.doi.org/10.1/x", "10.1/x"),
        ("doi:10.1/x", "10.1/x"),
        ("  10.1/x ", "10.1/x"),
        ("not-a-doi", None),
        (None, None),
    ],
)
def test_normalize_doi(value: str | None, expected: str | None) -> None:
    assert normalize_doi(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("http://arxiv.org/abs/2209.15001v3", "2209.15001"),
        ("arXiv:2301.00001", "2301.00001"),
        ("hep-th/9901001v2", "hep-th/9901001"),
        (None, None),
    ],
)
def test_normalize_arxiv_id(value: str | None, expected: str | None) -> None:
    assert normalize_arxiv_id(value) == expected


def test_is_preprint_doi() -> None:
    assert is_preprint_doi("10.48550/arxiv.2301.00001")
    assert is_preprint_doi("10.1101/2020.03.01.123456")
    assert not is_preprint_doi("10.1101/gr.123.2020")  # Genome Research, same prefix
    assert not is_preprint_doi("10.1038/x")
    assert not is_preprint_doi(None)


def test_clean_url_strips_tracking_and_fragment() -> None:
    assert (
        clean_url("https://Example.ORG/a?utm_source=x&b=1&fbclid=z#frag")
        == "https://example.org/a?b=1"
    )


def test_url_key_ignores_cosmetic_differences() -> None:
    assert url_key("https://www.reuters.com/a/?x=1&y=2") == url_key("http://reuters.com/a?y=2&x=1")
    assert url_key("https://reuters.com/a") != url_key("https://reuters.com/b")
