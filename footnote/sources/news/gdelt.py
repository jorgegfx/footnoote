"""GDELT DOC 2.0 article search, used to discover coverage on whitelisted outlets.

GDELT returns titles only (no snippets), allows one request every 5 seconds, and reports
errors as plain text, sometimes with a 200 status.
"""

from datetime import date, datetime
from typing import Any

from footnote.models import Family, QueryPlan, SourceResult
from footnote.sources.base import AdapterError, NativeAdapter, register

API_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
MAX_RECORDS = 20


@register("gdelt")
class GDELTAdapter(NativeAdapter):
    family = Family.NEWS

    async def search(self, plan: QueryPlan) -> list[SourceResult]:
        queries = plan.queries.get(Family.NEWS, [])
        domains = self.sources.search_domains(Family.NEWS)
        if not queries or not domains:
            return []
        domain_filter = " OR ".join(f"domain:{d}" for d in domains)
        params: dict[str, str | int] = {
            "query": f"{queries[0]} ({domain_filter})",
            "mode": "ArtList",
            "format": "json",
            "maxrecords": MAX_RECORDS,
            "sort": "HybridRel",
        }
        if plan.time_window:
            params["startdatetime"] = plan.time_window[0].strftime("%Y%m%d000000")
            params["enddatetime"] = plan.time_window[1].strftime("%Y%m%d235959")
        response = await self.client.get(API_URL, params=params)
        response.raise_for_status()
        try:
            payload: dict[str, Any] = response.json()
        except ValueError as exc:
            raise AdapterError("GDELT rejected the query") from exc
        articles = payload.get("articles") or []
        return [r for r in (self._to_result(a) for a in articles) if r is not None]

    def _to_result(self, article: dict[str, Any]) -> SourceResult | None:
        title, url = article.get("title"), article.get("url")
        if not title or not url:
            return None
        # GDELT's domain filter is a hint; the whitelist decides, and sets the outlet's trust.
        outlet = self.sources.search_source_for_url(url, Family.NEWS)
        if outlet is None:
            return None
        return SourceResult(
            source_id=self.id,
            family=Family.NEWS,
            title=title,
            url=url,
            published_at=_parse_seendate(article.get("seendate")),
            venue=article.get("domain") or outlet.id,
            trust=min(self.trust, outlet.trust),
        )


def _parse_seendate(value: str | None) -> date | None:
    try:
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").date() if value else None
    except ValueError:
        return None
