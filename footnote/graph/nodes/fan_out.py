"""fan_out: one `Send` per adapter that accepts the plan; each runs in the `source` node."""

from collections.abc import Callable, Sequence
from typing import Any

from langgraph.types import Send

from footnote.graph.state import GraphState, SourceNode, SourceTask
from footnote.sources.base import SourceAdapter, run_adapter

SOURCE_NODE = "source"
AFTER_SOURCES = "normalize"


def make_fan_out(adapters: Sequence[SourceAdapter]) -> Callable[[GraphState], list[Send] | str]:
    def fan_out(state: GraphState) -> list[Send] | str:
        plan = state["plan"]
        sends = [
            Send(SOURCE_NODE, SourceTask(adapter_id=a.id, plan=plan))
            for a in adapters
            if a.accepts(plan)
        ]
        return sends or AFTER_SOURCES  # nothing to query: go straight on with no results

    return fan_out


def make_source_node(adapters: Sequence[SourceAdapter], timeout: float) -> SourceNode:
    by_id = {a.id: a for a in adapters}

    async def source(state: SourceTask) -> dict[str, Any]:
        outcome = await run_adapter(by_id[state["adapter_id"]], state["plan"], timeout)
        update: dict[str, Any] = {
            "results": outcome.results,
            "timings": {f"source:{outcome.source_id}": outcome.elapsed_ms},
        }
        if not outcome.ok:
            update["errors"] = {outcome.source_id: outcome.error or "failed"}
        return update

    return source
