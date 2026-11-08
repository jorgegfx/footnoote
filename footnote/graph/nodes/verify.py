"""verify: check each cited sentence against its cited sources (`strong` model, separate call).

Citations a source doesn't back are dropped; a sentence left with no supporting citation is
removed and counted. If the check itself fails, the drafted answer stands (citations already
cleaned) so the request still succeeds.
"""

import html
import logging
from collections.abc import Sequence
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from footnote.citations import (
    cited_indices,
    clean_citations,
    keep_citations,
    split_sentences,
    tidy,
)
from footnote.graph.state import GraphState, Node, timed
from footnote.llm import ModelFactory, structured
from footnote.models import Citation, SourceResult
from footnote.prompts import load_prompt, source_blocks

logger = logging.getLogger(__name__)

NOTHING_SUPPORTED = (
    "The trusted sources found don't contain enough evidence to answer this reliably. "
    "The sources below are the closest matches."
)


class ClaimVerdict(BaseModel):
    claim: int = Field(description="The claim's index.")
    supported_by: list[int] = Field(
        default_factory=list, description="Cited source numbers that support the claim."
    )


class Verification(BaseModel):
    verdicts: list[ClaimVerdict]


def claim_blocks(claims: Sequence[tuple[str, list[int]]]) -> str:
    """One delimited, escaped `<claim>` block per cited sentence, numbered from 1."""
    blocks = []
    for index, (sentence, cites) in enumerate(claims, start=1):
        text = html.escape(sentence.strip(), quote=False)
        blocks.append(f'<claim index="{index}" cites="{",".join(map(str, cites))}">{text}</claim>')
    return "\n".join(blocks)


async def judge_claims(
    models: ModelFactory,
    question: str,
    sources: Sequence[SourceResult],
    claims: Sequence[tuple[str, list[int]]],
) -> dict[int, set[int]]:
    """Map claim index → the cited sources that support it. Claims the model skipped are
    absent. Also used by the eval suite to measure citation precision."""
    checker = structured(models("strong"), Verification)
    output = await checker.ainvoke(
        [
            SystemMessage(load_prompt("verify").substitute()),
            HumanMessage(f"{source_blocks(question, sources)}\n\nClaims:\n{claim_blocks(claims)}"),
        ]
    )
    if not isinstance(output, Verification):
        output = Verification.model_validate(output)
    return {
        v.claim: set(v.supported_by) & set(claims[v.claim - 1][1])
        for v in output.verdicts
        if 1 <= v.claim <= len(claims)
    }


def apply_verdicts(
    sentences: Sequence[str], claim_positions: Sequence[int], verdicts: dict[int, set[int]]
) -> tuple[str, int]:
    """Rebuild the text from verdicts. Returns the text and how many sentences were removed.
    A claim without a verdict is kept as written."""
    revised = list(sentences)
    removed = 0
    for claim, position in enumerate(claim_positions, start=1):
        if claim not in verdicts:
            continue
        support = verdicts[claim]
        if support:
            revised[position] = keep_citations(revised[position], support)
        else:
            revised[position] = ""
            removed += 1
    return tidy("".join(revised)), removed


def make_verify_node(models: ModelFactory) -> Node:
    @timed("verify")
    async def verify(state: GraphState) -> dict[str, Any]:
        answer = state["answer"]
        sources = answer.sources
        sentences = split_sentences(answer.text)
        positions = [i for i, s in enumerate(sentences) if cited_indices(s)]
        if not sources or not positions:
            return {}

        claims = [(sentences[i], cited_indices(sentences[i])) for i in positions]
        try:
            verdicts = await judge_claims(models, state["question"], sources, claims)
        except Exception as exc:
            logger.warning("verification failed (%s); keeping the draft", type(exc).__name__)
            return {}

        text, removed = apply_verdicts(sentences, positions, verdicts)
        text, used = clean_citations(text, len(sources))
        if not used:
            text = NOTHING_SUPPORTED
        return {
            "answer": answer.model_copy(
                update={
                    "text": text,
                    "citations": [Citation(index=i, result_id=sources[i - 1].id) for i in used],
                    "unsupported_claims_removed": removed,
                }
            )
        }

    return verify
