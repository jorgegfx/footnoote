"""enrich: fill metadata gaps on academic results via Crossref before ranking uses them."""

from typing import Any

from footnote.graph.state import GraphState, Node, timed
from footnote.sources.academic.crossref import CrossrefEnricher


def make_enrich_node(enricher: CrossrefEnricher | None) -> Node:
    @timed("enrich")
    async def enrich(state: GraphState) -> dict[str, Any]:
        candidates = state.get("candidates", [])
        if enricher is None or not candidates:
            return {"candidates": candidates}
        return {"candidates": await enricher.enrich(candidates)}

    return enrich
