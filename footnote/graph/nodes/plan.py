"""plan: classify the question and write per-family search queries (`fast` model)."""

from datetime import date
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from footnote.graph.state import GraphState, Node, timed
from footnote.llm import ModelFactory
from footnote.models import Family, Intent, QueryPlan
from footnote.prompts import load_prompt

MAX_QUERIES = 3
MAX_QUERY_CHARS = 300


class EmptyPlanError(ValueError):
    pass


class PlanOutput(BaseModel):
    """Flat schema for structured output; nested dicts and tuples trip up some providers."""

    intent: Intent = Field(description="Which source families can answer the question.")
    news_queries: list[str] = Field(default_factory=list, description="1-3 news searches.")
    academic_queries: list[str] = Field(default_factory=list, description="1-3 paper searches.")
    entities: list[str] = Field(default_factory=list, description="Key named entities.")
    time_start: date | None = Field(default=None, description="Start of an implied date range.")
    time_end: date | None = Field(default=None, description="End of an implied date range.")
    biomedical: bool = Field(
        default=False, description="True for medicine, health, biology or life-science questions."
    )


def make_plan_node(models: ModelFactory) -> Node:
    @timed("plan")
    async def plan(state: GraphState) -> dict[str, Any]:
        system = load_prompt("plan").substitute(today=date.today().isoformat())
        planner = models("fast").with_structured_output(PlanOutput)
        output = await planner.ainvoke([SystemMessage(system), HumanMessage(state["question"])])
        if not isinstance(output, PlanOutput):
            output = PlanOutput.model_validate(output)
        return {
            "plan": build_plan(
                output,
                question=state["question"],
                families=state.get("families"),
                time_window=state.get("time_window"),
            )
        }

    return plan


def build_plan(
    output: PlanOutput,
    *,
    question: str,
    families: list[Family] | None,
    time_window: tuple[date, date] | None,
) -> QueryPlan:
    """Apply the request's overrides to the model's plan. Families the user asked for always
    get searched (falling back to the question itself); otherwise the model's intent decides."""
    proposed = {Family.NEWS: output.news_queries, Family.ACADEMIC: output.academic_queries}
    active = families or _intent_families(output.intent)

    queries: dict[Family, list[str]] = {}
    for family in active:
        cleaned = _clean_queries(proposed[family])
        if not cleaned and families:
            cleaned = [question[:MAX_QUERY_CHARS]]
        if cleaned:
            queries[family] = cleaned
    if not queries:
        raise EmptyPlanError(
            "Couldn't work out what to search for. Try rephrasing with specific names, "
            "topics, or terms."
        )

    intent: Intent = (
        "both" if len(queries) == 2 else "news" if Family.NEWS in queries else "academic"
    )
    return QueryPlan(
        intent=intent,
        queries=queries,
        time_window=time_window or _window(output.time_start, output.time_end),
        entities=_clean_queries(output.entities, limit=10),
        biomedical=output.biomedical,
    )


def _intent_families(intent: Intent) -> list[Family]:
    return [Family.NEWS, Family.ACADEMIC] if intent == "both" else [Family(intent)]


def _clean_queries(queries: list[str], limit: int = MAX_QUERIES) -> list[str]:
    seen: dict[str, str] = {}
    for query in queries:
        stripped = " ".join(query.split())[:MAX_QUERY_CHARS]
        if stripped:
            seen.setdefault(stripped.lower(), stripped)
    return list(seen.values())[:limit]


def _window(start: date | None, end: date | None) -> tuple[date, date] | None:
    if start is None:
        return None
    end = end or date.today()
    return (start, end) if start <= end else None
