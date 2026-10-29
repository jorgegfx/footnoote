"""Adapter protocol, native-adapter base class and registry, and the never-raise wrapper."""

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import ClassVar, Protocol

import httpx

from footnote.config import Settings, SourceConfig, SourcesConfig
from footnote.http import LimiterBusyError, RateLimiter, ThrottledClient
from footnote.models import AdapterResult, Family, QueryPlan, SourceResult

logger = logging.getLogger(__name__)


class SourceAdapter(Protocol):
    @property
    def id(self) -> str: ...

    @property
    def family(self) -> Family: ...

    def accepts(self, plan: QueryPlan) -> bool: ...

    async def search(self, plan: QueryPlan) -> list[SourceResult]: ...


class AdapterError(Exception):
    """Raised by adapters with a message that is safe to show users as-is."""


class NativeAdapter(ABC):
    """Base for adapters backed by a source's own API."""

    family: ClassVar[Family]
    # Settings attribute holding a required API key; the adapter is skipped when it's unset.
    requires_key: ClassVar[str | None] = None

    def __init__(
        self,
        config: SourceConfig,
        settings: Settings,
        client: httpx.AsyncClient,
        sources: SourcesConfig,
    ):
        self.id = config.id
        self.trust = config.trust
        self.settings = settings
        limiter = None
        if config.rate_limit_per_sec:
            limiter = RateLimiter(config.rate_limit_per_sec, settings.adapter_timeout_seconds)
        self.client = ThrottledClient(client, limiter)
        self.sources = sources

    @classmethod
    def is_configured(cls, settings: Settings) -> bool:
        return cls.requires_key is None or getattr(settings, cls.requires_key) is not None

    def accepts(self, plan: QueryPlan) -> bool:
        return bool(plan.queries.get(self.family))

    @abstractmethod
    async def search(self, plan: QueryPlan) -> list[SourceResult]: ...


_REGISTRY: dict[str, type[NativeAdapter]] = {}


def register[A: type[NativeAdapter]](source_id: str) -> Callable[[A], A]:
    def decorator(cls: A) -> A:
        _REGISTRY[source_id] = cls
        return cls

    return decorator


def native_adapter_class(source_id: str) -> type[NativeAdapter] | None:
    return _REGISTRY.get(source_id)


def describe_error(exc: BaseException) -> str:
    """A short, user-safe error. Never includes URLs, which can carry API keys."""
    match exc:
        case AdapterError():
            return str(exc)
        case LimiterBusyError():
            return "busy; try again shortly"
        case httpx.HTTPStatusError() if exc.response.status_code == 429:
            return "rate limited"
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
