"""synthesize: write a cited answer from the ranked results only (`strong` model)."""

from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from footnote.citations import clean_citations
from footnote.graph.state import GraphState, Node, timed
from footnote.llm import ModelFactory
from footnote.models import Answer, Citation
from footnote.prompts import load_prompt, source_blocks

NO_RESULTS = (
    "I couldn't find anything from the trusted sources for this question, so I can't answer it. "
    "Try rephrasing, or widening the time range or source families."
)


def make_synthesize_node(models: ModelFactory) -> Node:
    @timed("synthesize")
    async def synthesize(state: GraphState) -> dict[str, Any]:
        sources = state.get("ranked", [])
        if not sources:
            return {"answer": Answer(text=NO_RESULTS)}

        system = load_prompt("synthesize").substitute()
        message = await models("strong").ainvoke(
            [SystemMessage(system), HumanMessage(source_blocks(state["question"], sources))]
        )
        text, used = clean_citations(message.text.strip(), len(sources))
        return {
            "answer": Answer(
                text=text,
                citations=[Citation(index=i, result_id=sources[i - 1].id) for i in used],
                sources=sources,
            )
        }

    return synthesize
