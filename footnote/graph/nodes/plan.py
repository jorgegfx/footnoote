"""plan: write news search queries for the question (`fast` model)."""

from datetime import date
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, field_validator

from footnote.graph.state import GraphState, Node, timed
from footnote.llm import ModelFactory, structured
from footnote.models import Family, QueryPlan
from footnote.prompts import load_prompt

MAX_QUERIES = 3
MAX_QUERY_CHARS = 300


class EmptyPlanError(ValueError):
    pass


class PlanOutput(BaseModel):
    """Flat schema for structured output; nested dicts and tuples trip up some providers."""

    news_queries: list[str] = Field(default_factory=list, description="1-3 news searches.")
    entities: list[str] = Field(default_factory=list, description="Key named entities.")
    time_start: date | None = Field(default=None, description="Start of an implied date range.")
    time_end: date | None = Field(default=None, description="End of an implied date range.")

    @field_validator("time_start", "time_end", mode="before")
    @classmethod
    def _blank_date_is_none(cls, value: object) -> object:
        """Some models send "" instead of null for a missing date."""
        return None if isinstance(value, str) and not value.strip() else value


def make_plan_node(models: ModelFactory) -> Node:
    @timed("plan")
    async def plan(state: GraphState) -> dict[str, Any]:
        system = load_prompt("plan").substitute(today=date.today().isoformat())
        planner = structured(models("fast"), PlanOutput)
        output = await planner.ainvoke([SystemMessage(system), HumanMessage(state["question"])])
        if not isinstance(output, PlanOutput):
            output = PlanOutput.model_validate(output)
        return {"plan": build_plan(output, time_window=state.get("time_window"))}

    return plan


def build_plan(output: PlanOutput, *, time_window: tuple[date, date] | None) -> QueryPlan:
    """Clean the model's plan and apply the request's time window, which wins over the model's."""
    queries = _clean_queries(output.news_queries)
    if not queries:
        raise EmptyPlanError(
            "Couldn't work out what to search for. Try rephrasing with specific names, "
            "events, or places."
        )
    return QueryPlan(
        queries={Family.NEWS: queries},
        time_window=time_window or _window(output.time_start, output.time_end),
        entities=_clean_queries(output.entities, limit=10),
    )


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
