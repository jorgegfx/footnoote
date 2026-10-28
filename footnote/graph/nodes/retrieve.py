"""retrieve: run the adapters for the planned families concurrently.

Milestone 1 uses asyncio.gather inside one node; milestone 2 replaces it with a LangGraph
`Send` fan-out so each adapter becomes its own node.
"""

import asyncio
from collections.abc import Sequence
from typing import Any

from footnote.graph.state import GraphState, Node, timed
from footnote.sources.base import SourceAdapter, run_adapter


def make_retrieve_node(adapters: Sequence[SourceAdapter], timeout: float) -> Node:
    @timed("retrieve")
    async def retrieve(state: GraphState) -> dict[str, Any]:
        plan = state["plan"]
        selected = [a for a in adapters if a.family in plan.families]
        outcomes = await asyncio.gather(*(run_adapter(a, plan, timeout) for a in selected))
        return {
            "results": [r for o in outcomes for r in o.results],
            "errors": {o.source_id: o.error or "failed" for o in outcomes if not o.ok},
            "timings": {f"source:{o.source_id}": o.elapsed_ms for o in outcomes},
        }

    return retrieve
