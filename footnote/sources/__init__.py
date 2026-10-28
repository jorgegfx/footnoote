"""Builds the adapter set for the enabled sources."""

import logging

import httpx

from footnote.config import Settings, SourcesConfig
from footnote.models import Family
from footnote.sources.academic import openalex  # noqa: F401  (registers the adapter)
from footnote.sources.base import SourceAdapter, native_adapter_factory
from footnote.sources.search.base import WebSearchAdapter, WebSearchProvider
from footnote.sources.search.tavily import TavilyProvider

logger = logging.getLogger(__name__)


class SearchNotConfiguredError(RuntimeError):
    pass


def build_search_provider(settings: Settings, client: httpx.AsyncClient) -> WebSearchProvider:
    if not settings.search_api_key:
        raise SearchNotConfiguredError("Set SEARCH_API_KEY to enable domain-restricted search.")
    if settings.search_provider == "tavily":
        return TavilyProvider(settings.search_api_key.get_secret_value(), client)
    raise SearchNotConfiguredError(f"Unknown SEARCH_PROVIDER {settings.search_provider!r}.")


def build_adapters(
    settings: Settings,
    sources: SourcesConfig,
    client: httpx.AsyncClient,
    search_provider: WebSearchProvider | None = None,
) -> list[SourceAdapter]:
    adapters: list[SourceAdapter] = []
    for config in sources.native():
        factory = native_adapter_factory(config.id)
        if factory is None:
            logger.warning("no adapter registered for enabled source %r; skipping", config.id)
            continue
        adapters.append(factory(config, settings, client))

    if search_provider is None and settings.search_api_key:
        search_provider = build_search_provider(settings, client)
    if search_provider is None:
        logger.warning("SEARCH_API_KEY not set; domain-restricted search is disabled")
    else:
        for family in Family:
            if sources.search_domains(family):
                adapters.append(WebSearchAdapter(family, search_provider, sources))
    return adapters
