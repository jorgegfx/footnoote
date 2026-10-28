"""Domain whitelist checks. Every URL from web search passes through here."""

from collections.abc import Iterable
from urllib.parse import urlsplit


def normalize_domain(domain: str) -> str:
    return domain.strip().lower().rstrip(".").removeprefix("www.")


def host_of(url: str) -> str | None:
    """Lower-cased host of an http(s) URL, or None if the URL isn't one we accept."""
    try:
        parts = urlsplit(url.strip())
        host = parts.hostname
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not host:
        return None
    return normalize_domain(host)


def host_matches(host: str, domain: str) -> bool:
    """Exact domain or a subdomain of it; `evilreuters.com` does not match `reuters.com`."""
    return host == domain or host.endswith("." + domain)


def matching_domain(url: str, domains: Iterable[str]) -> str | None:
    host = host_of(url)
    if host is None:
        return None
    for domain in domains:
        normalized = normalize_domain(domain)
        if normalized and host_matches(host, normalized):
            return normalized
    return None


def is_allowed(url: str, domains: Iterable[str]) -> bool:
    return matching_domain(url, domains) is not None
