"""Semantic Scholar paper search. Works without a key, but the shared pool is often rate limited."""

from typing import Any

from footnote.identifiers import is_preprint_doi, normalize_arxiv_id, normalize_doi
from footnote.models import Family, QueryPlan, SourceResult
from footnote.sources.base import NativeAdapter, register
from footnote.sources.parsing import parse_iso_date

API_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
PER_QUERY = 8
FIELDS = "title,abstract,venue,year,publicationDate,authors,externalIds,citationCount,url,journal"
PREPRINT_VENUES = frozenset({"arxiv", "arxiv.org", "biorxiv", "medrxiv", "ssrn"})


@register("semantic_scholar")
class SemanticScholarAdapter(NativeAdapter):
    family = Family.ACADEMIC

    async def search(self, plan: QueryPlan) -> list[SourceResult]:
        key = self.settings.semantic_scholar_api_key
        # Without a key we share a tight global rate limit, so spend one request per search.
        queries = plan.queries.get(Family.ACADEMIC, [])[: 3 if key else 1]
        headers = {"x-api-key": key.get_secret_value()} if key else {}
        results: dict[str, SourceResult] = {}
        for query in queries:
            params: dict[str, str | int] = {"query": query, "limit": PER_QUERY, "fields": FIELDS}
            if plan.time_window:
                start, end = plan.time_window
                params["publicationDateOrYear"] = f"{start.isoformat()}:{end.isoformat()}"
            response = await self.client.get(API_URL, params=params, headers=headers)
            response.raise_for_status()
            for paper in response.json().get("data") or []:
                result = self._to_result(paper)
                if result is not None:
                    results.setdefault(result.id, result)
        return list(results.values())

    def _to_result(self, paper: dict[str, Any]) -> SourceResult | None:
        title = paper.get("title")
        ids = paper.get("externalIds") or {}
        doi = normalize_doi(ids.get("DOI"))
        url = f"https://doi.org/{doi}" if doi else paper.get("url")
        if not title or not url:
            return None
        venue = paper.get("venue") or (paper.get("journal") or {}).get("name") or None
        year = paper.get("year")
        published = parse_iso_date(paper.get("publicationDate")) or (
            parse_iso_date(f"{year}-01-01") if year else None
        )
        return SourceResult(
            source_id=self.id,
            family=Family.ACADEMIC,
            title=title,
            url=url,
            snippet=paper.get("abstract") or "",
            published_at=published,
            authors=[a["name"] for a in paper.get("authors") or [] if a.get("name")],
            venue=venue,
            doi=doi,
            arxiv_id=normalize_arxiv_id(ids.get("ArXiv")),
            pmid=ids.get("PubMed"),
            citation_count=paper.get("citationCount"),
            is_preprint=is_preprint_doi(doi) or (venue or "").lower() in PREPRINT_VENUES,
            trust=self.trust,
        )
