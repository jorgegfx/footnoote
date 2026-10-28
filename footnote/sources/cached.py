"""Adapter wrapper that caches successful searches per (adapter, queries, time window)."""

import hashlib
import json

from footnote.cache import Cache
from footnote.models import QueryPlan, SourceResult
from footnote.sources.base import SourceAdapter


class CachedAdapter:
    def __init__(self, inner: SourceAdapter, cache: Cache, ttl_seconds: float):
        self.inner = inner
        self.id = inner.id
        self.family = inner.family
        self.cache = cache
        self.ttl_seconds = ttl_seconds

    def accepts(self, plan: QueryPlan) -> bool:
        return self.inner.accepts(plan)

    async def search(self, plan: QueryPlan) -> list[SourceResult]:
        key = self.cache_key(plan)
        cached = await self.cache.get(key)
        if cached is not None:
            return [SourceResult.model_validate(item) for item in cached]
        results = await self.inner.search(plan)  # failures raise, so they're never cached
        await self.cache.set(key, [r.model_dump(mode="json") for r in results], self.ttl_seconds)
        return results

    def cache_key(self, plan: QueryPlan) -> str:
        """Hashed, so cache keys never contain query text."""
        window = [d.isoformat() for d in plan.time_window] if plan.time_window else None
        payload = json.dumps([plan.queries.get(self.family, []), window], sort_keys=True)
        return f"source:{self.id}:{hashlib.sha256(payload.encode()).hexdigest()[:24]}"
