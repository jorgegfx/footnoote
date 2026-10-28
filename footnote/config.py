"""Settings from the environment and the trusted-source list from `config/sources.yaml`."""

from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from footnote.models import Family
from footnote.whitelist import host_matches, host_of, normalize_domain

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_provider: str | None = None
    llm_model_fast: str | None = None
    llm_model_strong: str | None = None

    search_provider: str = "tavily"
    search_api_key: SecretStr | None = None

    openalex_api_key: SecretStr | None = None
    guardian_api_key: SecretStr | None = None
    nyt_api_key: SecretStr | None = None
    ncbi_api_key: SecretStr | None = None
    semantic_scholar_api_key: SecretStr | None = None

    contact_email: str | None = None
    cache_ttl_seconds: int = 900
    adapter_timeout_seconds: float = 8.0
    sources_path: Path = PROJECT_ROOT / "config" / "sources.yaml"


class SourceConfig(BaseModel):
    id: str
    family: Family
    kind: Literal["native", "search", "enrichment"]
    trust: float = Field(ge=0, le=1)
    enabled: bool = True
    rate_limit_per_sec: float | None = None
    domains: list[str] = Field(default_factory=list)

    @field_validator("domains")
    @classmethod
    def _normalize_domains(cls, domains: list[str]) -> list[str]:
        return [normalize_domain(d) for d in domains if normalize_domain(d)]

    @model_validator(mode="after")
    def _search_needs_domains(self) -> Self:
        if self.kind == "search" and not self.domains:
            raise ValueError(f"search source {self.id!r} must list at least one domain")
        return self


class SourcesConfig(BaseModel):
    sources: list[SourceConfig]

    @model_validator(mode="after")
    def _unique_ids(self) -> Self:
        ids = [s.id for s in self.sources]
        duplicates = {i for i in ids if ids.count(i) > 1}
        if duplicates:
            raise ValueError(f"duplicate source ids: {sorted(duplicates)}")
        return self

    def enabled(self) -> list[SourceConfig]:
        return [s for s in self.sources if s.enabled]

    def native(self) -> list[SourceConfig]:
        return [s for s in self.enabled() if s.kind == "native"]

    def get(self, source_id: str) -> SourceConfig | None:
        return next((s for s in self.enabled() if s.id == source_id), None)

    def search_sources(self, family: Family) -> list[SourceConfig]:
        return [s for s in self.enabled() if s.kind == "search" and s.family == family]

    def search_domains(self, family: Family) -> list[str]:
        return [d for s in self.search_sources(family) for d in s.domains]

    def search_source_for_url(self, url: str, family: Family) -> SourceConfig | None:
        """The enabled search source whose domains cover `url`, i.e. the whitelist check."""
        host = host_of(url)
        if host is None:
            return None
        for source in self.search_sources(family):
            if any(host_matches(host, d) for d in source.domains):
                return source
        return None


def load_sources(path: Path) -> SourcesConfig:
    with path.open(encoding="utf-8") as f:
        return SourcesConfig.model_validate(yaml.safe_load(f))


@lru_cache
def get_settings() -> Settings:
    return Settings()
