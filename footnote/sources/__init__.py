"""Builds the adapter set and enricher for the enabled sources."""

import logging

import httpx

from footnote.cache import Cache
from footnote.config import Settings, SourcesConfig
from footnote.models import Family

# Importing the adapter modules registers them.
from footnote.sources.academic import arxiv, openalex, pubmed, semantic_scholar  # noqa: F401
from footnote.sources.academic.crossref import CrossrefEnricher
from footnote.sources.base import SourceAdapter, native_adapter_class
from footnote.sources.cached import CachedAdapter
from footnote.sources.news import gdelt, guardian, nyt  # noqa: F401
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


def source_status(settings: Settings, sources: SourcesConfig) -> dict[str, bool]:
    """Whether each enabled native/enrichment source can run (adapter exists, keys present)."""
    status: dict[str, bool] = {}
    for config in sources.enabled():
        if config.kind == "native":
            cls = native_adapter_class(config.id)
            status[config.id] = cls is not None and cls.is_configured(settings)
        elif config.kind == "enrichment":
            status[config.id] = config.id == "crossref"
    return status


def build_adapters(
    settings: Settings,
    sources: SourcesConfig,
    client: httpx.AsyncClient,
    search_provider: WebSearchProvider | None = None,
    cache: Cache | None = None,
) -> list[SourceAdapter]:
    adapters: list[SourceAdapter] = []
    for config in sources.native():
        cls = native_adapter_class(config.id)
        if cls is None:
            logger.warning("no adapter registered for enabled source %r; skipping", config.id)
        elif not cls.is_configured(settings):
            logger.warning("%s needs %s; skipping", config.id, (cls.requires_key or "").upper())
        else:
            adapters.append(cls(config, settings, client, sources))

    if search_provider is None and settings.search_api_key:
        search_provider = build_search_provider(settings, client)
    if search_provider is None:
        logger.warning("SEARCH_API_KEY not set; domain-restricted search is disabled")
    else:
        for family in Family:
            if sources.search_domains(family):
                adapters.append(WebSearchAdapter(family, search_provider, sources))

    if cache is not None:
        ttl = settings.cache_ttl_seconds
        adapters = [CachedAdapter(a, cache, ttl) for a in adapters]
    return adapters


def build_enricher(
    settings: Settings,
    sources: SourcesConfig,
    client: httpx.AsyncClient,
    cache: Cache | None = None,
) -> CrossrefEnricher | None:
    config = sources.get("crossref")
    if config is None:
        return None
    return CrossrefEnricher(
        settings, client, cache, settings.cache_ttl_seconds, config.rate_limit_per_sec
    )
