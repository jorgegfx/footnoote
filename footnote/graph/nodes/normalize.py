"""normalize: clean HTML to text, truncate snippets, and canonicalize URLs."""

from typing import Any

from footnote.graph.state import GraphState, timed
from footnote.identifiers import clean_url
from footnote.models import SourceResult, result_id
from footnote.text import html_to_text, truncate
from footnote.whitelist import host_of

SNIPPET_CHARS = 1200
TITLE_CHARS = 300


def normalize_result(result: SourceResult) -> SourceResult | None:
    if host_of(result.url) is None:
        return None
    title = truncate(html_to_text(result.title), TITLE_CHARS)
    if not title:
        return None
    url = clean_url(result.url)
    return result.model_copy(
        update={
            "id": result_id(url),
            "title": title,
            "url": url,
            "alt_urls": [clean_url(u) for u in result.alt_urls if host_of(u)],
            "snippet": truncate(html_to_text(result.snippet), SNIPPET_CHARS),
            "authors": [a for a in (html_to_text(a) for a in result.authors) if a],
            "venue": html_to_text(result.venue) if result.venue else None,
        }
    )


@timed("normalize")
async def normalize(state: GraphState) -> dict[str, Any]:
    normalized = (normalize_result(r) for r in state.get("results", []))
    return {"candidates": [r for r in normalized if r is not None]}
