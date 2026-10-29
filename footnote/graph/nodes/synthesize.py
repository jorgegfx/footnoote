"""synthesize: write a cited answer from the ranked results only (`strong` model).

Tokens are streamed to `custom` stream consumers as `token` events while the answer is drafted.
"""

from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.config import get_stream_writer

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
        write = get_stream_writer()
        parts: list[str] = []
        async for chunk in models("strong").astream(
            [SystemMessage(system), HumanMessage(source_blocks(state["question"], sources))]
        ):
            if chunk.text:
                parts.append(chunk.text)
                write({"event": "token", "data": {"text": chunk.text}})
        text, used = clean_citations("".join(parts).strip(), len(sources))
        return {
            "answer": Answer(
                text=text,
                citations=[Citation(index=i, result_id=sources[i - 1].id) for i in used],
                sources=sources,
            )
        }

    return synthesize
