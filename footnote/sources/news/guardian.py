"""Guardian Open Platform content search. Requires GUARDIAN_API_KEY."""

from typing import Any

from footnote.models import Family, QueryPlan, SourceResult
from footnote.sources.base import NativeAdapter, register
from footnote.sources.parsing import first_text, parse_iso_date

API_URL = "https://content.guardianapis.com/search"
PAGE_SIZE = 8


@register("guardian")
class GuardianAdapter(NativeAdapter):
    family = Family.NEWS
    requires_key = "guardian_api_key"

    async def search(self, plan: QueryPlan) -> list[SourceResult]:
        assert self.settings.guardian_api_key is not None
        queries = plan.queries.get(Family.NEWS, [])
        params: dict[str, str | int] = {
            "q": " OR ".join(f"({q})" for q in queries),
            "api-key": self.settings.guardian_api_key.get_secret_value(),
            "page-size": PAGE_SIZE,
            "order-by": "relevance",
            "show-fields": "trailText,standfirst,byline",
        }
        if plan.time_window:
            params["from-date"] = plan.time_window[0].isoformat()
            params["to-date"] = plan.time_window[1].isoformat()
        response = await self.client.get(API_URL, params=params)
        response.raise_for_status()
        items = response.json().get("response", {}).get("results", [])
        return [r for r in (self._to_result(i) for i in items) if r is not None]

    def _to_result(self, item: dict[str, Any]) -> SourceResult | None:
        title, url = item.get("webTitle"), item.get("webUrl")
        if not title or not url:
            return None
        fields = item.get("fields") or {}
        return SourceResult(
            source_id=self.id,
            family=Family.NEWS,
            title=title,
            url=url,
            snippet=first_text(fields.get("standfirst"), fields.get("trailText")),
            published_at=parse_iso_date(item.get("webPublicationDate")),
            authors=[fields["byline"]] if fields.get("byline") else [],
            venue="The Guardian",
            trust=self.trust,
        )
