"""Tavily search provider."""

from datetime import date, datetime
from typing import Any

import httpx

from footnote.models import Family
from footnote.sources.search.base import WebHit

API_URL = "https://api.tavily.com/search"


class TavilyProvider:
    name = "tavily"

    def __init__(self, api_key: str, client: httpx.AsyncClient):
        self.api_key = api_key
        self.client = client

    async def search(
        self,
        query: str,
        *,
        domains: list[str],
        family: Family,
        time_window: tuple[date, date] | None,
        max_results: int,
    ) -> list[WebHit]:
        body: dict[str, Any] = {
            "query": query,
            "topic": "news" if family == Family.NEWS else "general",
            "search_depth": "basic",
            "max_results": max_results,
            "include_domains": domains,
        }
        if time_window:
            body["start_date"] = time_window[0].isoformat()
            body["end_date"] = time_window[1].isoformat()
        response = await self.client.post(
            API_URL, json=body, headers={"Authorization": f"Bearer {self.api_key}"}
        )
        response.raise_for_status()
        return [
            WebHit(
                title=item["title"],
                url=item["url"],
                content=item.get("content") or "",
                published_at=_parse_date(item.get("published_date")),
            )
            for item in response.json().get("results", [])
            if item.get("title") and item.get("url")
        ]


def _parse_date(value: str | None) -> date | None:
    """Tavily returns ISO dates or RFC 2822 strings depending on the topic."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        pass
    try:
        return datetime.strptime(value, "%a, %d %b %Y %H:%M:%S %Z").date()
    except ValueError:
        return None
