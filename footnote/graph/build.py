"""Graph construction.

plan ─► fan_out ─► source (one per adapter, in parallel) ─► normalize ─► dedupe ─► enrich
     ─► rank ─► synthesize
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from footnote.graph.nodes.dedupe import dedupe
from footnote.graph.nodes.enrich import make_enrich_node
from footnote.graph.nodes.fan_out import AFTER_SOURCES, SOURCE_NODE, make_fan_out, make_source_node
from footnote.graph.nodes.normalize import normalize
from footnote.graph.nodes.plan import make_plan_node
from footnote.graph.nodes.rank import make_rank_node
from footnote.graph.nodes.synthesize import make_synthesize_node
from footnote.graph.nodes.verify import make_verify_node
from footnote.graph.state import GraphState, SourceTask
from footnote.llm import ModelFactory
from footnote.sources.academic.crossref import CrossrefEnricher
from footnote.sources.base import SourceAdapter

SearchGraph = CompiledStateGraph[GraphState, None, GraphState, GraphState]


@dataclass(frozen=True)
class GraphDeps:
    models: ModelFactory
    adapters: Sequence[SourceAdapter]
    adapter_timeout: float
    enricher: CrossrefEnricher | None = None
    today: Callable[[], date] = date.today


def build_graph(deps: GraphDeps) -> SearchGraph:
    graph = StateGraph(GraphState)
    graph.add_node("plan", make_plan_node(deps.models))
    graph.add_node(
        SOURCE_NODE,
        make_source_node(deps.adapters, deps.adapter_timeout),
        input_schema=SourceTask,
    )
    graph.add_node("normalize", normalize)
    graph.add_node("dedupe", dedupe)
    graph.add_node("enrich", make_enrich_node(deps.enricher))
    graph.add_node("rank", make_rank_node(deps.models, deps.today))
    graph.add_node("synthesize", make_synthesize_node(deps.models))
    graph.add_node("verify", make_verify_node(deps.models))

    graph.add_edge(START, "plan")
    graph.add_conditional_edges("plan", make_fan_out(deps.adapters), [SOURCE_NODE, AFTER_SOURCES])
    graph.add_edge(SOURCE_NODE, "normalize")
    graph.add_edge("normalize", "dedupe")
    graph.add_edge("dedupe", "enrich")
    graph.add_edge("enrich", "rank")
    graph.add_edge("rank", "synthesize")
    graph.add_edge("synthesize", "verify")
    graph.add_edge("verify", END)
    return graph.compile()
