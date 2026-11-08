"""URL normalization shared by adapters, normalize and dedupe."""

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING_PARAMS = frozenset(
    {"fbclid", "gclid", "mc_cid", "mc_eid", "cmpid", "smid", "ref", "taid"}
)


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
