"""normalize: clean HTML to text, truncate snippets, and cap the result list.

Milestone 1 keeps the top results by trust weight; real dedupe and ranking arrive in milestone 2.
"""

import html
from html.parser import HTMLParser
from typing import Any

from footnote.graph.state import GraphState, timed
from footnote.models import SourceResult
from footnote.whitelist import host_of

SNIPPET_CHARS = 1200
TITLE_CHARS = 300


class _TextExtractor(HTMLParser):
    _SKIP = frozenset({"script", "style"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skipping = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP:
            self._skipping += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skipping:
            self._skipping -= 1

    def handle_data(self, data: str) -> None:
        if not self._skipping:
            self.parts.append(data)


def html_to_text(value: str) -> str:
    if "<" not in value:
        return " ".join(html.unescape(value).split())
    parser = _TextExtractor()
    parser.feed(value)
    parser.close()
    return " ".join(" ".join(parser.parts).split())


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:.") + "…"


def normalize_result(result: SourceResult) -> SourceResult | None:
    if host_of(result.url) is None:
        return None
    title = truncate(html_to_text(result.title), TITLE_CHARS)
    if not title:
        return None
    return result.model_copy(
        update={"title": title, "snippet": truncate(html_to_text(result.snippet), SNIPPET_CHARS)}
    )


@timed("normalize")
async def normalize(state: GraphState) -> dict[str, Any]:
    unique: dict[str, SourceResult] = {}
    for raw in state.get("results", []):
        result = normalize_result(raw)
        if result is not None:
            unique.setdefault(result.id, result)
    ranked = sorted(unique.values(), key=lambda r: r.trust, reverse=True)
    return {"ranked": ranked[: state["max_results"]]}
