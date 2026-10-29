"""FastAPI app, routes and SSE streaming."""

import json
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date
from importlib.metadata import version
from pathlib import Path
from typing import Annotated, Any, Self, cast

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, StringConstraints, ValidationError, model_validator

from footnote.cache import TTLCache
from footnote.config import Settings, SourcesConfig, get_settings, load_sources
from footnote.graph.build import GraphDeps, SearchGraph, build_graph
from footnote.graph.nodes.plan import EmptyPlanError
from footnote.graph.state import GraphState
from footnote.llm import LLMNotConfiguredError, ModelFactory, model_factory
from footnote.models import Answer, Family
from footnote.sources import build_adapters, build_enricher, source_status
from footnote.sources.base import SourceAdapter
from footnote.sources.search.base import WebSearchProvider

__version__ = version("footnote")

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).parent / "web"
INTERNAL_ERROR = "Something went wrong while answering. Please try again."


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


def initial_state(body: SearchRequest) -> GraphState:
    return {
        "question": body.question,
        "max_results": body.max_results,
        "families": body.families,
        "time_window": (body.time_window.start, body.time_window.end) if body.time_window else None,
    }


def finish(final: dict[str, Any], start: float) -> Answer:
    answer: Answer = final["answer"]
    timings = {**final.get("timings", {}), "total": int((time.perf_counter() - start) * 1000)}
    answer = answer.model_copy(
        update={"source_errors": final.get("errors", {}), "timings_ms": timings}
    )
    return answer


def sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def stream_request(
    q: Annotated[str, Query(description="The question.")],
    families: Annotated[list[Family] | None, Query()] = None,
    max_results: int = 12,
    start: date | None = None,
    end: date | None = None,
) -> SearchRequest:
    window = {"start": start, "end": end} if start and end else None
    try:
        return SearchRequest.model_validate(
            {"question": q, "families": families, "max_results": max_results, "time_window": window}
        )
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc


def runtime_of(request: Request) -> Runtime:
    return cast(Runtime, request.app.state.runtime)


async def stream_answer(graph: SearchGraph, initial: GraphState) -> AsyncIterator[str]:
    """SSE events: plan, source_started, source_done, source_error, ranked, token, answer,
    error, done. Token events are the unverified draft; `answer` carries the final text."""
    start = time.perf_counter()
    final: dict[str, Any] = {}
    try:
        async for mode, item in graph.astream(initial, stream_mode=["updates", "custom", "values"]):
            chunk: Any = item  # shape depends on the mode
            if mode == "custom":
                yield sse(chunk["event"], chunk["data"])
            elif mode == "values":
                final = chunk
            else:
                for node, update in chunk.items():
                    if node == "plan" and update:
                        yield sse("plan", update["plan"].model_dump(mode="json"))
                    elif node == "rank" and update:
                        ranked = [r.model_dump(mode="json") for r in update["ranked"]]
                        yield sse("ranked", {"sources": ranked})
        yield sse("answer", finish(final, start).model_dump(mode="json"))
    except EmptyPlanError as exc:
        yield sse("error", {"status": 422, "message": str(exc)})
    except LLMNotConfiguredError as exc:
        yield sse("error", {"status": 503, "message": str(exc)})
    except Exception:
        logger.exception("streamed search failed")
        yield sse("error", {"status": 500, "message": INTERNAL_ERROR})
    yield sse("done", {})


@asynccontextmanager
async def open_runtime(
    settings: Settings,
    models: ModelFactory | None = None,
    search_provider: WebSearchProvider | None = None,
) -> AsyncIterator[Runtime]:
    """Shared HTTP client, adapters and compiled graph; also used by the eval suite."""
    sources = load_sources(settings.sources_path)
    async with httpx.AsyncClient(
        headers={"User-Agent": user_agent(settings)},
        timeout=httpx.Timeout(settings.adapter_timeout_seconds),
        follow_redirects=True,
    ) as client:
        cache = TTLCache()
        adapters = build_adapters(settings, sources, client, search_provider, cache)
        graph = build_graph(
            GraphDeps(
                models=models or model_factory(settings),
                adapters=adapters,
                adapter_timeout=settings.adapter_timeout_seconds,
                enricher=build_enricher(settings, sources, client, cache),
            )
        )
        yield Runtime(settings, sources, adapters, graph)


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
        async with open_runtime(resolved, models, search_provider) as runtime:
            app.state.runtime = runtime
            yield

    app = FastAPI(title="Footnote", version=__version__, lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
    templates = Jinja2Templates(directory=WEB_DIR / "templates")

    @app.post("/api/search")
    async def search(body: SearchRequest, request: Request) -> Answer:
        start = time.perf_counter()
        try:
            final = await runtime_of(request).graph.ainvoke(initial_state(body))
        except EmptyPlanError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except LLMNotConfiguredError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return finish(cast(dict[str, Any], final), start)

    @app.get("/api/search/stream")
    async def search_stream(
        body: Annotated[SearchRequest, Depends(stream_request)], request: Request
    ) -> StreamingResponse:
        return StreamingResponse(
            stream_answer(runtime_of(request).graph, initial_state(body)),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/sources")
    async def list_sources(request: Request) -> list[SourceInfo]:
        return [
            SourceInfo(id=s.id, family=s.family, kind=s.kind, trust=s.trust, domains=s.domains)
            for s in runtime_of(request).sources.enabled()
        ]

    @app.get("/health")
    async def health(request: Request) -> dict[str, Any]:
        runtime = runtime_of(request)
        s = runtime.settings
        active = {a.id for a in runtime.adapters}
        search = {f"{f.value}_search": f"{f.value}_search" in active for f in Family}
        return {
            "status": "ok",
            "version": __version__,
            "llm_configured": bool(s.llm_provider and s.llm_model_fast and s.llm_model_strong),
            "sources": {**source_status(s, runtime.sources), **search},
        }

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def index(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "index.html", {"version": __version__})

    return app


app = create_app()
