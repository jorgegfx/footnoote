"""NYT Article Search. Requires NYT_API_KEY; abstracts and snippets only (no full text)."""

from typing import Any

from footnote.models import Family, QueryPlan, SourceResult
from footnote.sources.base import NativeAdapter, register
from footnote.sources.parsing import first_text, parse_iso_date

API_URL = "https://api.nytimes.com/svc/search/v2/articlesearch.json"


@register("nyt")
class NYTAdapter(NativeAdapter):
    family = Family.NEWS
    requires_key = "nyt_api_key"

    async def search(self, plan: QueryPlan) -> list[SourceResult]:
        assert self.settings.nyt_api_key is not None
        queries = plan.queries.get(Family.NEWS, [])
        if not queries:
            return []
        # The API allows 5 requests a minute, so only the planner's best query is sent.
        params: dict[str, str] = {
            "q": queries[0],
            "api-key": self.settings.nyt_api_key.get_secret_value(),
            "sort": "relevance",
        }
        if plan.time_window:
            params["begin_date"] = plan.time_window[0].strftime("%Y%m%d")
            params["end_date"] = plan.time_window[1].strftime("%Y%m%d")
        response = await self.client.get(API_URL, params=params)
        response.raise_for_status()
        docs = (response.json().get("response") or {}).get("docs") or []
        return [r for r in (self._to_result(d) for d in docs) if r is not None]

    def _to_result(self, doc: dict[str, Any]) -> SourceResult | None:
        title = (doc.get("headline") or {}).get("main")
        url = doc.get("web_url")
        if not title or not url:
            return None
        byline = ((doc.get("byline") or {}).get("original") or "").removeprefix("By ").strip()
        return SourceResult(
            source_id=self.id,
            family=Family.NEWS,
            title=title,
            url=url,
            snippet=first_text(doc.get("abstract"), doc.get("snippet"), doc.get("lead_paragraph")),
            published_at=parse_iso_date(doc.get("pub_date")),
            authors=[byline] if byline else [],
            venue="The New York Times",
            trust=self.trust,
        )
