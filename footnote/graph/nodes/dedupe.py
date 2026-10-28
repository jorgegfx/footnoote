"""dedupe: merge results that are the same work or the same story.

Matching, in order: DOI, arXiv id, PMID, canonical URL (including alternate URLs), then
near-identical titles within a family. Syndicated news (one wire story on several outlets)
collapses to one result that keeps every outlet URL in `alt_urls`.
"""

import re
from collections.abc import Sequence
from difflib import SequenceMatcher
from typing import Any

from footnote.graph.state import GraphState, timed
from footnote.identifiers import is_preprint_doi, url_key
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
    # Prefer the published version, then the most trusted source, then the richest snippet.
    primary = max(group, key=lambda r: (not r.is_preprint, r.trust, len(r.snippet)))
    published_doi = next((r.doi for r in group if r.doi and not is_preprint_doi(r.doi)), None)
    doi = primary.doi if primary.doi and not is_preprint_doi(primary.doi) else published_doi
    doi = doi or next((r.doi for r in group if r.doi), None)

    seen = {url_key(primary.url)}
    alt_urls = []
    for url in (u for r in group for u in [r.url, *r.alt_urls]):
        if url_key(url) not in seen:
            seen.add(url_key(url))
            alt_urls.append(url)

    counts = [r.citation_count for r in group if r.citation_count is not None]
    return primary.model_copy(
        update={
            "id": result_id(doi=doi, url=primary.url),
            "doi": doi,
            "alt_urls": alt_urls,
            "snippet": max((r.snippet for r in group), key=len),
            "arxiv_id": primary.arxiv_id or next((r.arxiv_id for r in group if r.arxiv_id), None),
            "pmid": primary.pmid or next((r.pmid for r in group if r.pmid), None),
            "venue": primary.venue or next((r.venue for r in group if r.venue), None),
            "published_at": primary.published_at
            or next((r.published_at for r in group if r.published_at), None),
            "authors": primary.authors or max((r.authors for r in group), key=len),
            "citation_count": max(counts) if counts else None,
            "is_preprint": all(r.is_preprint for r in group),
            "trust": max(r.trust for r in group),
        }
    )


def _identity_keys(result: SourceResult) -> list[str]:
    keys = [f"url:{url_key(u)}" for u in [result.url, *result.alt_urls]]
    if result.doi:
        keys.append(f"doi:{result.doi}")
    if result.arxiv_id:
        keys.append(f"arxiv:{result.arxiv_id}")
    if result.pmid:
        keys.append(f"pmid:{result.pmid}")
    return keys


def _comparable_title(result: SourceResult) -> str:
    title = result.title.lower()
    if result.family == Family.NEWS:
        title = _OUTLET_SUFFIX.sub("", title)
    return _NON_WORD.sub(" ", title).strip()


def _same_title(a: SourceResult, b: SourceResult, title_a: str, title_b: str) -> bool:
    if a.family != b.family or min(len(title_a), len(title_b)) < MIN_TITLE_CHARS:
        return False
    # Two published works with different DOIs are different works, whatever their titles.
    if a.doi and b.doi and a.doi != b.doi and not (a.is_preprint or b.is_preprint):
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
