"""Graph construction. Milestone 1: plan → retrieve → normalize → synthesize."""

from collections.abc import Sequence
from dataclasses import dataclass

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from footnote.graph.nodes.normalize import normalize
from footnote.graph.nodes.plan import make_plan_node
from footnote.graph.nodes.retrieve import make_retrieve_node
from footnote.graph.nodes.synthesize import make_synthesize_node
from footnote.graph.state import GraphState
from footnote.llm import ModelFactory
from footnote.sources.base import SourceAdapter

SearchGraph = CompiledStateGraph[GraphState, None, GraphState, GraphState]


@dataclass(frozen=True)
class GraphDeps:
    models: ModelFactory
    adapters: Sequence[SourceAdapter]
    adapter_timeout: float


def build_graph(deps: GraphDeps) -> SearchGraph:
    graph = StateGraph(GraphState)
    graph.add_node("plan", make_plan_node(deps.models))
    graph.add_node("retrieve", make_retrieve_node(deps.adapters, deps.adapter_timeout))
    graph.add_node("normalize", normalize)
    graph.add_node("synthesize", make_synthesize_node(deps.models))

    graph.add_edge(START, "plan")
    graph.add_edge("plan", "retrieve")
    graph.add_edge("retrieve", "normalize")
    graph.add_edge("normalize", "synthesize")
    graph.add_edge("synthesize", END)
    return graph.compile()
