"""Normalization of scholarly identifiers and URLs, shared by adapters, normalize and dedupe."""

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_DOI_PREFIXES = ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "http://dx.doi.org/")
_ARXIV_VERSION = re.compile(r"v\d+$")
# bioRxiv/medRxiv DOIs are date-shaped (10.1101/2020.01.01.123456); CSHL journals share the prefix.
_BIORXIV_DOI = re.compile(r"^10\.1101/\d{4}\.\d{2}\.\d{2}\.")
_TRACKING_PARAMS = frozenset(
    {"fbclid", "gclid", "mc_cid", "mc_eid", "cmpid", "smid", "ref", "taid"}
)


def normalize_doi(value: str | None) -> str | None:
    if not value:
        return None
    doi = value.strip()
    for prefix in _DOI_PREFIXES:
        if doi.lower().startswith(prefix):
            doi = doi[len(prefix) :]
            break
    doi = doi.removeprefix("doi:").removeprefix("DOI:").strip().lower()
    return doi if doi.startswith("10.") else None


def normalize_arxiv_id(value: str | None) -> str | None:
    if not value:
        return None
    arxiv_id = value.strip()
    for prefix in ("http://arxiv.org/abs/", "https://arxiv.org/abs/", "arXiv:", "arxiv:"):
        arxiv_id = arxiv_id.removeprefix(prefix)
    return _ARXIV_VERSION.sub("", arxiv_id) or None


def is_preprint_doi(doi: str | None) -> bool:
    if not doi:
        return False
    return doi.startswith("10.48550/") or bool(_BIORXIV_DOI.match(doi))


def clean_url(url: str) -> str:
    """Display form: tracking parameters and fragment removed, host lower-cased."""
    parts = urlsplit(url.strip())
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in _TRACKING_PARAMS
    ]
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path, urlencode(query), "")
    )


def url_key(url: str) -> str:
    """Comparison form for dedupe: ignores scheme, `www.`, trailing slash and query order."""
    parts = urlsplit(clean_url(url))
    host = (parts.hostname or "").removeprefix("www.")
    path = parts.path.rstrip("/") or "/"
    query = urlencode(sorted(parse_qsl(parts.query, keep_blank_values=True)))
    return f"{host}{path}?{query}" if query else f"{host}{path}"
