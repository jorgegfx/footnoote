"""rank: score = relevance × trust × quality, keep the top N.

Relevance comes from one batched `fast`-model call (0–10 per candidate). If that call fails,
word overlap with the question stands in so the request still succeeds. Quality is recency.
"""

import logging
import math
from collections.abc import Callable, Sequence
from datetime import date
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from footnote.graph.state import GraphState, Node, timed
from footnote.llm import ModelFactory, structured
from footnote.models import QueryPlan, SourceResult
from footnote.prompts import load_prompt, source_blocks
from footnote.text import content_words

logger = logging.getLogger(__name__)

MAX_CANDIDATES = 40  # cap on what's sent for LLM scoring, chosen by trust × quality
SCORING_SNIPPET_CHARS = 500
MIN_RELEVANCE = 0.3  # LLM score 3/10; below this a result isn't evidence for the question
NEWS_HALF_LIFE_DAYS = 30


class RelevanceScore(BaseModel):
    index: int = Field(description="The candidate's source index.")
    score: int = Field(ge=0, le=10, description="Relevance to the question, 0-10.")


class RelevanceScores(BaseModel):
    scores: list[RelevanceScore]


def quality(result: SourceResult, plan: QueryPlan, today: date) -> float:
    """0–1 recency multiplier, ignored when the user fixed a time window."""
    if plan.time_window is not None:
        return 1.0
    if result.published_at is None:
        return 0.7
    age_days = max((today - result.published_at).days, 0)
    return 0.4 + 0.6 * math.pow(0.5, age_days / NEWS_HALF_LIFE_DAYS)


def lexical_relevance(question: str, result: SourceResult) -> float:
    words = content_words(question)
    if not words:
        return 0.5
    return len(words & content_words(f"{result.title} {result.snippet}")) / len(words)


async def llm_relevance(
    models: ModelFactory, question: str, candidates: Sequence[SourceResult]
) -> list[float]:
    scorer = structured(models("fast"), RelevanceScores)
    output = await scorer.ainvoke(
        [
            SystemMessage(load_prompt("rank").substitute()),
            HumanMessage(source_blocks(question, candidates, SCORING_SNIPPET_CHARS)),
        ]
    )
    if not isinstance(output, RelevanceScores):
        output = RelevanceScores.model_validate(output)
    by_index = {s.index: s.score / 10 for s in output.scores}
    # A candidate the model skipped falls back to word overlap rather than being dropped.
    return [
        by_index.get(i, lexical_relevance(question, c)) for i, c in enumerate(candidates, start=1)
    ]


def make_rank_node(models: ModelFactory, today: Callable[[], date] = date.today) -> Node:
    @timed("rank")
    async def rank(state: GraphState) -> dict[str, Any]:
        candidates = state.get("candidates", [])
        if not candidates:
            return {"ranked": []}
        plan, question, now = state["plan"], state["question"], today()
        prior = sorted(candidates, key=lambda r: r.trust * quality(r, plan, now), reverse=True)
        pool = prior[:MAX_CANDIDATES]

        threshold = MIN_RELEVANCE
        try:
            relevance = await llm_relevance(models, question, pool)
        except Exception as exc:
            logger.warning("relevance scoring failed (%s); using word overlap", type(exc).__name__)
            relevance = [lexical_relevance(question, c) for c in pool]
            threshold = 0.0  # overlap is too crude to drop results on

        scored = [
            r.model_copy(update={"score": round(rel * r.trust * quality(r, plan, now), 4)})
            for r, rel in zip(pool, relevance, strict=True)
            if rel >= threshold
        ]
        scored.sort(key=lambda r: r.score, reverse=True)
        return {"ranked": scored[: state["max_results"]]}

    return rank
