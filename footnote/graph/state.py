"""Graph state shared by all nodes."""

import operator
import time
from collections.abc import Awaitable, Callable
from datetime import date
from functools import wraps
from typing import Annotated, Any, NotRequired, Protocol, Required, TypedDict

from footnote.models import Answer, Family, QueryPlan, SourceResult


def merge_dicts[K, V](left: dict[K, V], right: dict[K, V]) -> dict[K, V]:
    return {**left, **right}


class GraphState(TypedDict, total=False):
    # Request
    question: Required[str]
    max_results: Required[int]
    families: NotRequired[list[Family] | None]
    time_window: NotRequired[tuple[date, date] | None]
    # Pipeline
    plan: QueryPlan
    results: Annotated[list[SourceResult], operator.add]  # raw, appended by parallel sources
    candidates: list[SourceResult]  # normalized, deduped, enriched
    ranked: list[SourceResult]  # top results, in citation order
    answer: Answer
    # Observability; adapters write these in parallel, hence the reducers
    errors: Annotated[dict[str, str], merge_dicts]
    timings: Annotated[dict[str, int], merge_dicts]


class SourceTask(TypedDict):
    """Payload of the `Send` that runs one adapter."""

    adapter_id: str
    plan: QueryPlan


class Node(Protocol):
    """Matches LangGraph's node protocol, which passes `state` by name."""

    def __call__(self, state: GraphState) -> Awaitable[dict[str, Any]]: ...


class SourceNode(Protocol):
    def __call__(self, state: SourceTask) -> Awaitable[dict[str, Any]]: ...


def timed(name: str) -> Callable[[Node], Node]:
    """Record the node's wall time in `timings[name]`."""

    def decorator(node: Node) -> Node:
        @wraps(node)
        async def wrapper(state: GraphState) -> dict[str, Any]:
            start = time.perf_counter()
            update = await node(state)
            elapsed = int((time.perf_counter() - start) * 1000)
            return {**update, "timings": {**update.get("timings", {}), name: elapsed}}

        return wrapper

    return decorator
