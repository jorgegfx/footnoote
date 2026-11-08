"""dedupe: merge results that are the same story.

Matching, in order: canonical URL (including alternate URLs), then near-identical titles
within a family. Syndicated news (one wire story on several outlets)
collapses to one result that keeps every outlet URL in `alt_urls`.
"""

import re
from collections.abc import Sequence
from difflib import SequenceMatcher
from typing import Any

from footnote.graph.state import GraphState, timed
from footnote.identifiers import url_key
from footnote.models import Family, SourceResult, result_id

TITLE_SIMILARITY = 0.92
MIN_TITLE_CHARS = 25  # shorter titles ("Editorial", "Correction") are too generic to match on
_NON_WORD = re.compile(r"[^a-z0-9]+")
# "Fed holds rates | Reuters", "Fed holds rates - BBC News"
_OUTLET_SUFFIX = re.compile(r"\s+[|\-–—]\s+[^|\-–—]{2,30}$")


def dedupe_results(results: Sequence[SourceResult]) -> list[SourceResult]:
    parent = list(range(len(results)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        root_i, root_j = find(i), find(j)
        if root_i != root_j:
            parent[max(root_i, root_j)] = min(root_i, root_j)  # keep the earliest as root

    first_with_key: dict[str, int] = {}
    for i, result in enumerate(results):
        for key in _identity_keys(result):
            if key in first_with_key:
                union(i, first_with_key[key])
            else:
                first_with_key[key] = i

    titles = [_comparable_title(r) for r in results]
    for i in range(len(results)):
        for j in range(i + 1, len(results)):
            if find(i) != find(j) and _same_title(results[i], results[j], titles[i], titles[j]):
                union(i, j)

    groups: dict[int, list[SourceResult]] = {}
    for i, result in enumerate(results):
        groups.setdefault(find(i), []).append(result)
    return [merge_group(group) for group in groups.values()]


def merge_group(group: list[SourceResult]) -> SourceResult:
    if len(group) == 1:
        return group[0]
    # Prefer the most trusted source, then the richest snippet.
    primary = max(group, key=lambda r: (r.trust, len(r.snippet)))

    seen = {url_key(primary.url)}
    alt_urls = []
    for url in (u for r in group for u in [r.url, *r.alt_urls]):
        if url_key(url) not in seen:
            seen.add(url_key(url))
            alt_urls.append(url)

    return primary.model_copy(
        update={
            "id": result_id(primary.url),
            "alt_urls": alt_urls,
            "snippet": max((r.snippet for r in group), key=len),
            "venue": primary.venue or next((r.venue for r in group if r.venue), None),
            "published_at": primary.published_at
            or next((r.published_at for r in group if r.published_at), None),
            "authors": primary.authors or max((r.authors for r in group), key=len),
            "trust": max(r.trust for r in group),
        }
    )


def _identity_keys(result: SourceResult) -> list[str]:
    return [f"url:{url_key(u)}" for u in [result.url, *result.alt_urls]]


def _comparable_title(result: SourceResult) -> str:
    title = result.title.lower()
    if result.family == Family.NEWS:
        title = _OUTLET_SUFFIX.sub("", title)
    return _NON_WORD.sub(" ", title).strip()


def _same_title(a: SourceResult, b: SourceResult, title_a: str, title_b: str) -> bool:
    if a.family != b.family or min(len(title_a), len(title_b)) < MIN_TITLE_CHARS:
        return False
    if title_a == title_b:
        return True
    matcher = SequenceMatcher(None, title_a, title_b, autojunk=False)
    return (
        matcher.real_quick_ratio() >= TITLE_SIMILARITY
        and matcher.quick_ratio() >= TITLE_SIMILARITY
        and matcher.ratio() >= TITLE_SIMILARITY
    )


@timed("dedupe")
async def dedupe(state: GraphState) -> dict[str, Any]:
    return {"candidates": dedupe_results(state.get("candidates", []))}
