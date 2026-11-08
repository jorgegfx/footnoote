"""Domain-restricted web search: provider interface and the adapter that enforces the whitelist."""

import asyncio
from datetime import date
from typing import Protocol

from pydantic import BaseModel

from footnote.config import SourcesConfig
from footnote.models import Family, QueryPlan, SourceResult

PER_QUERY = 8


class WebHit(BaseModel):
    title: str
    url: str
    content: str = ""
    published_at: date | None = None


class WebSearchProvider(Protocol):
    name: str

    async def search(
        self,
        query: str,
        *,
        domains: list[str],
        family: Family,
        time_window: tuple[date, date] | None,
        max_results: int,
    ) -> list[WebHit]: ...


class WebSearchAdapter:
    """One per family. Passes the whitelist to the provider, then re-checks every URL itself."""

    def __init__(self, family: Family, provider: WebSearchProvider, sources: SourcesConfig):
        self.id = f"{family.value}_search"
        self.family = family
        self.provider = provider
        self.sources = sources

    def accepts(self, plan: QueryPlan) -> bool:
        return bool(plan.queries.get(self.family))

    async def search(self, plan: QueryPlan) -> list[SourceResult]:
        domains = self.sources.search_domains(self.family)
        if not domains:
            return []
        # Queries run concurrently: one at a time, three of them overrun the adapter time limit.
        outcomes = await asyncio.gather(
            *(
                self.provider.search(
                    query,
                    domains=domains,
                    family=self.family,
                    time_window=plan.time_window,
                    max_results=PER_QUERY,
                )
                for query in plan.queries.get(self.family, [])
            ),
            return_exceptions=True,
        )
        hit_lists = [o for o in outcomes if not isinstance(o, BaseException)]
        if outcomes and not hit_lists:  # one failed query is tolerated; all failing is an error
            failure = outcomes[0]
            assert isinstance(failure, BaseException)
            raise failure
        results: dict[str, SourceResult] = {}
        for hits in hit_lists:
            for hit in hits:
                result = self._to_result(hit)
                if result is not None:
                    results.setdefault(result.id, result)
        return list(results.values())

    def _to_result(self, hit: WebHit) -> SourceResult | None:
        # Never trust the provider's domain filter alone.
        source = self.sources.search_source_for_url(hit.url, self.family)
        if source is None:
            return None
        return SourceResult(
            source_id=source.id,
            family=self.family,
            title=hit.title,
            url=hit.url,
            snippet=hit.content,
            published_at=hit.published_at,
            trust=source.trust,
        )
