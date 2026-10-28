"""Source adapter protocol, the native-adapter registry, and the never-raise wrapper."""

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Protocol

import httpx

from footnote.config import Settings, SourceConfig
from footnote.models import AdapterResult, Family, QueryPlan, SourceResult

logger = logging.getLogger(__name__)


class SourceAdapter(Protocol):
    id: str
    family: Family

    async def search(self, plan: QueryPlan) -> list[SourceResult]: ...


NativeAdapterFactory = Callable[[SourceConfig, Settings, httpx.AsyncClient], SourceAdapter]

_REGISTRY: dict[str, NativeAdapterFactory] = {}


def register(source_id: str) -> Callable[[NativeAdapterFactory], NativeAdapterFactory]:
    def decorator(factory: NativeAdapterFactory) -> NativeAdapterFactory:
        _REGISTRY[source_id] = factory
        return factory

    return decorator


def native_adapter_factory(source_id: str) -> NativeAdapterFactory | None:
    return _REGISTRY.get(source_id)


def describe_error(exc: BaseException) -> str:
    """A short, user-safe error. Never includes URLs, which can carry API keys."""
    match exc:
        case httpx.HTTPStatusError():
            return f"HTTP {exc.response.status_code}"
        case httpx.TimeoutException():
            return "timed out"
        case httpx.TransportError():
            return "network error"
        case _:
            return f"unexpected error ({type(exc).__name__})"


async def run_adapter(
    adapter: SourceAdapter, plan: QueryPlan, limit_seconds: float
) -> AdapterResult:
    """Run one adapter with a time limit. Never raises: failures come back as `ok=False`."""
    start = time.perf_counter()

    def elapsed() -> int:
        return int((time.perf_counter() - start) * 1000)

    try:
        async with asyncio.timeout(limit_seconds):
            results = await adapter.search(plan)
    except TimeoutError:
        error = f"timed out after {limit_seconds:g}s"
    except Exception as exc:
        error = describe_error(exc)
        if error.startswith("unexpected"):
            logger.exception("source %s failed", adapter.id)
    else:
        return AdapterResult(source_id=adapter.id, ok=True, results=results, elapsed_ms=elapsed())

    logger.warning("source %s failed: %s", adapter.id, error)
    return AdapterResult(source_id=adapter.id, ok=False, error=error, elapsed_ms=elapsed())
