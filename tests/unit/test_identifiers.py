from footnote.identifiers import clean_url, url_key


def test_clean_url_strips_tracking_and_fragment() -> None:
    assert (
        clean_url("https://Example.ORG/a?utm_source=x&b=1&fbclid=z#frag")
        == "https://example.org/a?b=1"
    )


def test_url_key_ignores_cosmetic_differences() -> None:
    assert url_key("https://www.reuters.com/a/?x=1&y=2") == url_key("http://reuters.com/a?y=2&x=1")
    assert url_key("https://reuters.com/a") != url_key("https://reuters.com/b")
