import pytest

from footnote.whitelist import host_of, is_allowed, matching_domain

DOMAINS = ["reuters.com", "bbc.co.uk", "bbc.com"]


@pytest.mark.parametrize(
    "url",
    [
        "https://reuters.com/x",
        "https://www.reuters.com/markets/x",
        "http://uk.reuters.com/x",
        "https://WWW.BBC.CO.UK/news/1",
        "https://reuters.com./x",
    ],
)
def test_allows_domain_and_subdomains(url: str) -> None:
    assert is_allowed(url, DOMAINS)


@pytest.mark.parametrize(
    "url",
    [
        "https://evilreuters.com/x",
        "https://reuters.com.evil.example/x",
        "https://reuters.com@attacker.example/x",
        "https://notbbc.co.uk/x",
        "https://co.uk/x",
        "ftp://reuters.com/x",
        "javascript:alert(1)",
        "reuters.com/x",
        "",
        "https://[::1/x",
    ],
)
def test_rejects_lookalikes_and_bad_urls(url: str) -> None:
    assert not is_allowed(url, DOMAINS)


def test_matching_domain_normalizes_config_entries() -> None:
    assert matching_domain("https://www.bbc.com/a", [" WWW.BBC.com "]) == "bbc.com"


def test_host_of_strips_www_and_case() -> None:
    assert host_of("https://WWW.Nature.com/articles/1") == "nature.com"
