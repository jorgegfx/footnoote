"""synthesize: write a cited answer from the ranked results only (`strong` model)."""

import html
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from footnote.citations import clean_citations
from footnote.graph.state import GraphState, Node, timed
from footnote.llm import ModelFactory
from footnote.models import Answer, Citation, SourceResult
from footnote.prompts import load_prompt

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
            [SystemMessage(system), HumanMessage(format_sources(state["question"], sources))]
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


def format_sources(question: str, sources: list[SourceResult]) -> str:
    """Each source in its own delimited block. Content is escaped so retrieved text can't
    close the block or forge another source."""
    blocks = [f"Question: {html.escape(question, quote=False)}", ""]
    for index, source in enumerate(sources, start=1):
        attrs = {
            "index": str(index),
            "outlet": source.venue or source.source_id,
            "date": source.published_at.isoformat() if source.published_at else "unknown",
            "preprint": str(source.is_preprint).lower(),
        }
        attr_text = " ".join(f'{k}="{html.escape(v)}"' for k, v in attrs.items())
        blocks += [
            f"<source {attr_text}>",
            f"Title: {html.escape(source.title, quote=False)}",
            html.escape(source.snippet, quote=False) or "(no snippet available)",
            "</source>",
            "",
        ]
    return "\n".join(blocks).rstrip()
