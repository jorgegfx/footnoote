"""OpenAlex works search. Free; `mailto` puts us in the polite pool."""

from datetime import date
from typing import Any

import httpx

from footnote.config import Settings, SourceConfig
from footnote.models import Family, QueryPlan, SourceResult
from footnote.sources.base import register

API_URL = "https://api.openalex.org/works"
PER_QUERY = 8
SELECT = ",".join(
    [
        "id",
        "doi",
        "display_name",
        "publication_date",
        "authorships",
        "primary_location",
        "abstract_inverted_index",
        "cited_by_count",
        "ids",
        "type",
    ]
)


@register("openalex")
class OpenAlexAdapter:
    family = Family.ACADEMIC

    def __init__(self, config: SourceConfig, settings: Settings, client: httpx.AsyncClient):
        self.id = config.id
        self.trust = config.trust
        self.settings = settings
        self.client = client

    async def search(self, plan: QueryPlan) -> list[SourceResult]:
        results: dict[str, SourceResult] = {}
        for query in plan.queries.get(Family.ACADEMIC, []):
            for work in await self._fetch(query, plan.time_window):
                result = self._to_result(work)
                if result is not None:
                    results.setdefault(result.id, result)
        return list(results.values())

    async def _fetch(self, query: str, window: tuple[date, date] | None) -> list[dict[str, Any]]:
        params: dict[str, str | int] = {"search": query, "per_page": PER_QUERY, "select": SELECT}
        if window:
            params["filter"] = (
                f"from_publication_date:{window[0].isoformat()},"
                f"to_publication_date:{window[1].isoformat()}"
            )
        if self.settings.contact_email:
            params["mailto"] = self.settings.contact_email
        if self.settings.openalex_api_key:
            params["api_key"] = self.settings.openalex_api_key.get_secret_value()
        response = await self.client.get(API_URL, params=params)
        response.raise_for_status()
        works: list[dict[str, Any]] = response.json().get("results", [])
        return works

    def _to_result(self, work: dict[str, Any]) -> SourceResult | None:
        title = work.get("display_name")
        location = work.get("primary_location") or {}
        doi_url: str | None = work.get("doi")
        url = location.get("landing_page_url") or doi_url or work.get("id")
        if not title or not url:
            return None
        source = location.get("source") or {}
        pmid_url: str | None = (work.get("ids") or {}).get("pmid")
        return SourceResult(
            source_id=self.id,
            family=Family.ACADEMIC,
            title=title,
            url=url,
            snippet=abstract_from_inverted_index(work.get("abstract_inverted_index")),
            published_at=_parse_date(work.get("publication_date")),
            authors=[
                a["author"]["display_name"]
                for a in work.get("authorships") or []
                if (a.get("author") or {}).get("display_name")
            ],
            venue=source.get("display_name"),
            doi=doi_url.removeprefix("https://doi.org/") if doi_url else None,
            pmid=pmid_url.rstrip("/").rsplit("/", 1)[-1] if pmid_url else None,
            citation_count=work.get("cited_by_count"),
            is_preprint=work.get("type") == "preprint",
            trust=self.trust,
        )


def abstract_from_inverted_index(index: dict[str, list[int]] | None) -> str:
    """OpenAlex ships abstracts as {word: [positions]}; rebuild the text."""
    if not index:
        return ""
    positioned = [(pos, word) for word, positions in index.items() for pos in positions]
    return " ".join(word for _, word in sorted(positioned))


def _parse_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None
