"""arXiv API (Atom). Everything here is a preprint; arXiv asks for one request every 3 seconds."""

import re
import xml.etree.ElementTree as ET
from datetime import date

from footnote.identifiers import normalize_arxiv_id, normalize_doi
from footnote.models import Family, QueryPlan, SourceResult
from footnote.sources.base import NativeAdapter, register
from footnote.sources.parsing import parse_iso_date

API_URL = "https://export.arxiv.org/api/query"
MAX_RESULTS = 8
MAX_TERMS = 6
NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-]*")


@register("arxiv")
class ArxivAdapter(NativeAdapter):
    family = Family.ACADEMIC

    async def search(self, plan: QueryPlan) -> list[SourceResult]:
        search_query = build_search_query(plan.queries.get(Family.ACADEMIC, []), plan.time_window)
        if not search_query:
            return []
        response = await self.client.get(
            API_URL,
            params={
                "search_query": search_query,
                "max_results": MAX_RESULTS,
                "sortBy": "relevance",
            },
        )
        response.raise_for_status()
        root = ET.fromstring(response.content)
        results = [self._to_result(e) for e in root.findall("atom:entry", NS)]
        return [r for r in results if r is not None]

    def _to_result(self, entry: ET.Element) -> SourceResult | None:
        arxiv_id = normalize_arxiv_id(_find(entry, "atom:id"))
        title = _find(entry, "atom:title")
        if not arxiv_id or not title:
            return None
        return SourceResult(
            source_id=self.id,
            family=Family.ACADEMIC,
            title=title,
            url=f"https://arxiv.org/abs/{arxiv_id}",
            snippet=_find(entry, "atom:summary"),
            published_at=parse_iso_date(_find(entry, "atom:published")),
            authors=[
                name for a in entry.findall("atom:author", NS) if (name := _find(a, "atom:name"))
            ],
            venue="arXiv",
            # Set when the paper was later published; dedupe then merges it with that record.
            doi=normalize_doi(_find(entry, "arxiv:doi")),
            arxiv_id=arxiv_id,
            is_preprint=True,
            trust=self.trust,
        )


def build_search_query(queries: list[str], window: tuple[date, date] | None) -> str:
    """One request for all rewrites: `(all:a AND all:b) OR (all:c AND all:d)`."""
    clauses = []
    for query in queries:
        terms = _WORD.findall(query)[:MAX_TERMS]
        if terms:
            clauses.append("(" + " AND ".join(f"all:{t}" for t in terms) + ")")
    if not clauses:
        return ""
    search = " OR ".join(clauses)
    if window:
        start, end = (d.strftime("%Y%m%d") for d in window)
        search = f"({search}) AND submittedDate:[{start}0000 TO {end}2359]"
    return search


def _find(element: ET.Element, path: str) -> str:
    found = element.find(path, NS)
    return " ".join((found.text or "").split()) if found is not None else ""
