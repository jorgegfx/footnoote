"""FastAPI app and routes."""

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date
from importlib.metadata import version
from typing import Annotated, Any, Self, cast

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field, StringConstraints, model_validator

from footnote.config import Settings, SourcesConfig, get_settings, load_sources
from footnote.graph.build import GraphDeps, SearchGraph, build_graph
from footnote.graph.nodes.plan import EmptyPlanError
from footnote.graph.state import GraphState
from footnote.llm import LLMNotConfiguredError, ModelFactory, model_factory
from footnote.models import Answer, Family
from footnote.sources import build_adapters
from footnote.sources.base import SourceAdapter, native_adapter_factory
from footnote.sources.search.base import WebSearchProvider

__version__ = version("footnote")


class TimeWindow(BaseModel):
    start: date
    end: date

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.start > self.end:
            raise ValueError("time_window.start must not be after time_window.end")
        return self


class SearchRequest(BaseModel):
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]
    families: list[Family] | None = Field(default=None, min_length=1)
    max_results: int = Field(default=12, ge=1, le=30)
    time_window: TimeWindow | None = None


class SourceInfo(BaseModel):
    id: str
    family: Family
    kind: str
    trust: float
    domains: list[str]


@dataclass
class Runtime:
    settings: Settings
    sources: SourcesConfig
    adapters: list[SourceAdapter]
    graph: SearchGraph


def user_agent(settings: Settings) -> str:
    contact = f" (+mailto:{settings.contact_email})" if settings.contact_email else ""
    return f"Footnote/{__version__}{contact}"


def create_app(
    settings: Settings | None = None,
    models: ModelFactory | None = None,
    search_provider: WebSearchProvider | None = None,
) -> FastAPI:
    """Arguments exist for tests; production uses the environment and `.env`."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if settings is None:
            load_dotenv()  # provider SDKs read their API keys from os.environ
        resolved = settings or get_settings()
        sources = load_sources(resolved.sources_path)
        async with httpx.AsyncClient(
            headers={"User-Agent": user_agent(resolved)},
            timeout=httpx.Timeout(resolved.adapter_timeout_seconds),
            follow_redirects=True,
        ) as client:
            adapters = build_adapters(resolved, sources, client, search_provider)
            graph = build_graph(
                GraphDeps(
                    models=models or model_factory(resolved),
                    adapters=adapters,
                    adapter_timeout=resolved.adapter_timeout_seconds,
                )
            )
            app.state.runtime = Runtime(resolved, sources, adapters, graph)
            yield

    app = FastAPI(title="Footnote", version=__version__, lifespan=lifespan)

    @app.post("/api/search")
    async def search(body: SearchRequest, request: Request) -> Answer:
        runtime: Runtime = request.app.state.runtime
        start = time.perf_counter()
        initial: GraphState = {
            "question": body.question,
            "max_results": body.max_results,
            "families": body.families,
            "time_window": (body.time_window.start, body.time_window.end)
            if body.time_window
            else None,
        }
        try:
            final = cast(dict[str, Any], await runtime.graph.ainvoke(initial))
        except EmptyPlanError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except LLMNotConfiguredError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        answer: Answer = final["answer"]
        timings = {**final.get("timings", {}), "total": int((time.perf_counter() - start) * 1000)}
        return answer.model_copy(
            update={"source_errors": final.get("errors", {}), "timings_ms": timings}
        )

    @app.get("/api/sources")
    async def list_sources(request: Request) -> list[SourceInfo]:
        runtime: Runtime = request.app.state.runtime
        return [
            SourceInfo(id=s.id, family=s.family, kind=s.kind, trust=s.trust, domains=s.domains)
            for s in runtime.sources.enabled()
        ]

    @app.get("/health")
    async def health(request: Request) -> dict[str, Any]:
        runtime: Runtime = request.app.state.runtime
        s = runtime.settings
        native = {
            src.id: native_adapter_factory(src.id) is not None for src in runtime.sources.native()
        }
        search_ok = any(a.id.endswith("_search") for a in runtime.adapters)
        return {
            "status": "ok",
            "version": __version__,
            "llm_configured": bool(s.llm_provider and s.llm_model_fast and s.llm_model_strong),
            "sources": {**native, "news_search": search_ok, "academic_search": search_ok},
        }

    return app


app = create_app()
