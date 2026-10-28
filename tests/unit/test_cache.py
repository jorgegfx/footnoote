import contextlib

from footnote.cache import TTLCache
from footnote.models import Family, QueryPlan
from footnote.sources.cached import CachedAdapter
from tests.fakes import StaticAdapter, make_result


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


async def test_ttl_expiry() -> None:
    clock = FakeClock()
    cache = TTLCache(clock=clock)
    await cache.set("k", [1, 2], ttl_seconds=10)
    assert await cache.get("k") == [1, 2]
    clock.now = 10
    assert await cache.get("k") is None
    assert len(cache) == 0


async def test_lru_eviction() -> None:
    cache = TTLCache(max_entries=2)
    await cache.set("a", 1, 60)
    await cache.set("b", 2, 60)
    await cache.get("a")  # a is now most recently used
    await cache.set("c", 3, 60)
    assert await cache.get("b") is None
    assert await cache.get("a") == 1


async def test_zero_ttl_disables_caching() -> None:
    cache = TTLCache()
    await cache.set("k", 1, 0)
    assert await cache.get("k") is None


async def test_cached_adapter_reuses_results_per_query() -> None:
    inner = StaticAdapter("openalex", Family.ACADEMIC, [make_result(1)])
    adapter = CachedAdapter(inner, TTLCache(), ttl_seconds=60)
    plan = QueryPlan(intent="academic", queries={Family.ACADEMIC: ["q"]})

    first = await adapter.search(plan)
    second = await adapter.search(plan)
    other = await adapter.search(plan.model_copy(update={"queries": {Family.ACADEMIC: ["q2"]}}))

    assert first == second == other
    assert len(inner.plans) == 2  # second call was a hit; different query was a miss
    assert "q" not in adapter.cache_key(plan).split(":")[-1]  # hashed, no query text


async def test_cached_adapter_does_not_cache_failures() -> None:
    inner = StaticAdapter("x", Family.ACADEMIC, error=RuntimeError("down"))
    cache = TTLCache()
    adapter = CachedAdapter(inner, cache, ttl_seconds=60)
    plan = QueryPlan(intent="academic", queries={Family.ACADEMIC: ["q"]})
    for _ in range(2):
        with contextlib.suppress(RuntimeError):
            await adapter.search(plan)
    assert len(inner.plans) == 2
    assert len(cache) == 0
