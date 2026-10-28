"""Data models shared across the pipeline and the API."""

import hashlib
from datetime import date
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator


class Family(StrEnum):
    NEWS = "news"
    ACADEMIC = "academic"


Intent = Literal["news", "academic", "both"]


class QueryPlan(BaseModel):
    intent: Intent
    queries: dict[Family, list[str]]
    time_window: tuple[date, date] | None = None
    entities: list[str] = Field(default_factory=list)
    biomedical: bool = False  # gates biomedical-only sources such as PubMed

    @property
    def families(self) -> list[Family]:
        return [family for family, queries in self.queries.items() if queries]


def result_id(*, doi: str | None, url: str) -> str:
    """Stable id: DOI when known (survives URL variants), otherwise the URL."""
    key = f"doi:{doi.lower()}" if doi else f"url:{url}"
    return hashlib.sha1(key.encode()).hexdigest()[:16]


class SourceResult(BaseModel):
    id: str = ""
    source_id: str
    family: Family
    title: str
    url: str
    alt_urls: list[str] = Field(default_factory=list)
    snippet: str = ""
    published_at: date | None = None
    authors: list[str] = Field(default_factory=list)
    venue: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    pmid: str | None = None
    citation_count: int | None = None
    is_preprint: bool = False
    trust: float = Field(ge=0, le=1)
    score: float = 0.0

    @model_validator(mode="after")
    def _fill_id(self) -> Self:
        if not self.id:
            self.id = result_id(doi=self.doi, url=self.url)
        return self


class AdapterResult(BaseModel):
    source_id: str
    ok: bool
    results: list[SourceResult] = Field(default_factory=list)
    error: str | None = None
    elapsed_ms: int = 0


class Citation(BaseModel):
    index: int
    result_id: str


class Answer(BaseModel):
    text: str
    citations: list[Citation] = Field(default_factory=list)
    sources: list[SourceResult] = Field(default_factory=list)
    unsupported_claims_removed: int = 0
    source_errors: dict[str, str] = Field(default_factory=dict)
    timings_ms: dict[str, int] = Field(default_factory=dict)
