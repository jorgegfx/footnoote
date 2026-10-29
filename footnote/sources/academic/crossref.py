"""Crossref DOI lookups that fill metadata gaps (date, venue, authors, citations, abstract).

Not a search source: it runs after dedupe on academic results that have a DOI but are
missing fields that ranking and display use. Lookups never fail the request.
"""

import asyncio
import logging
from collections.abc import Sequence
from datetime import date
from typing import Any
from urllib.parse import quote

import httpx

from footnote.cache import Cache
from footnote.config import Settings
from footnote.http import RateLimiter, ThrottledClient
from footnote.models import Family, SourceResult
from footnote.text import html_to_text

logger = logging.getLogger(__name__)

API_URL = "https://api.crossref.org/works/"
MAX_LOOKUPS = 20
CONCURRENCY = 5  # Crossref's polite pool asks for modest parallelism


class CrossrefEnricher:
    def __init__(
        self,
        settings: Settings,
        client: httpx.AsyncClient,
        cache: Cache | None = None,
        ttl_seconds: float = 0,
        rate_limit_per_sec: float | None = None,
    ):
        self.settings = settings
        limiter = None
        if rate_limit_per_sec:
            limiter = RateLimiter(rate_limit_per_sec, settings.adapter_timeout_seconds)
        self.client = ThrottledClient(client, limiter)
        self.cache = cache
        self.ttl_seconds = ttl_seconds
        self._slots = asyncio.Semaphore(CONCURRENCY)

    async def enrich(self, results: Sequence[SourceResult]) -> list[SourceResult]:
        """Looks up results missing ranking fields (date, citations) before display-only gaps."""
        needing = [(gap_priority(r), i) for i, r in enumerate(results) if needs_enrichment(r)]
        targets = [i for _, i in sorted(needing)][:MAX_LOOKUPS]
        if not targets:
            return list(results)
        works = await asyncio.gather(*(self._lookup(results[i].doi or "") for i in targets))
        enriched = list(results)
        for index, work in zip(targets, works, strict=True):
            if work is not None:
                enriched[index] = merge_work(enriched[index], work)
        return enriched

    async def _lookup(self, doi: str) -> dict[str, Any] | None:
        key = f"crossref:{doi}"
        if self.cache is not None and (cached := await self.cache.get(key)) is not None:
            work: dict[str, Any] = cached
            return work
        params = {"mailto": self.settings.contact_email} if self.settings.contact_email else {}
        try:
            async with self._slots:
                response = await self.client.get(API_URL + quote(doi, safe="/"), params=params)
            response.raise_for_status()
            work = response.json()["message"]
        except Exception as exc:
            logger.info("crossref lookup failed: %s", type(exc).__name__)
            return None
        if self.cache is not None:
            await self.cache.set(key, work, self.ttl_seconds)
        return work


def needs_enrichment(result: SourceResult) -> bool:
    return (
        result.family == Family.ACADEMIC
        and result.doi is not None
        and (
            result.published_at is None
            or not result.venue
            or not result.authors
            or result.citation_count is None
            or not result.snippet
        )
    )


def gap_priority(result: SourceResult) -> int:
    """0: missing a ranking input; 1: missing display metadata; 2: only missing an abstract,
    which Crossref seldom has."""
    if result.published_at is None or result.citation_count is None:
        return 0
    if not result.venue or not result.authors:
        return 1
    return 2


def merge_work(result: SourceResult, work: dict[str, Any]) -> SourceResult:
    """Fill only missing fields; what the original source said wins."""
    update: dict[str, Any] = {}
    if result.published_at is None:
        update["published_at"] = _work_date(work)
    if not result.venue and (venues := work.get("container-title")):
        update["venue"] = venues[0]
    if not result.authors:
        update["authors"] = [
            " ".join(p for p in (a.get("given"), a.get("family")) if p)
            for a in work.get("author") or []
            if a.get("family")
        ]
    if result.citation_count is None and isinstance(work.get("is-referenced-by-count"), int):
        update["citation_count"] = work["is-referenced-by-count"]
    if not result.snippet and work.get("abstract"):
        update["snippet"] = html_to_text(work["abstract"])
    if work.get("type") == "posted-content":
        update["is_preprint"] = True
    return result.model_copy(update={k: v for k, v in update.items() if v})


def _work_date(work: dict[str, Any]) -> date | None:
    for field in ("published", "issued", "published-online", "published-print"):
        parts = ((work.get(field) or {}).get("date-parts") or [[]])[0]
        if not parts or not parts[0]:
            continue
        month = parts[1] if len(parts) > 1 else 1
        day = parts[2] if len(parts) > 2 else 1
        try:
            return date(parts[0], month, day)
        except (TypeError, ValueError):
            continue
    return None
