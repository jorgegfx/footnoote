from datetime import date

import pytest

from footnote.graph.nodes.plan import EmptyPlanError, PlanOutput, build_plan
from footnote.models import Family


def test_news_queries_become_the_plan() -> None:
    plan = build_plan(PlanOutput(news_queries=["IPCC report"], entities=["IPCC"]), time_window=None)
    assert plan.queries == {Family.NEWS: ["IPCC report"]}
    assert plan.entities == ["IPCC"]


def test_queries_are_cleaned_deduped_and_capped() -> None:
    plan = build_plan(
        PlanOutput(news_queries=["  sea  level ", "Sea Level", "", "b", "c", "d"]),
        time_window=None,
    )
    assert plan.queries[Family.NEWS] == ["sea level", "b", "c"]


def test_empty_plan_raises() -> None:
    with pytest.raises(EmptyPlanError):
        build_plan(PlanOutput(news_queries=["  ", ""]), time_window=None)


def test_time_window_from_model_and_request_override() -> None:
    output = PlanOutput(
        news_queries=["a"], time_start=date(2023, 1, 1), time_end=date(2023, 12, 31)
    )
    plan = build_plan(output, time_window=None)
    assert plan.time_window == (date(2023, 1, 1), date(2023, 12, 31))

    requested = (date(2024, 1, 1), date(2024, 2, 1))
    assert build_plan(output, time_window=requested).time_window == requested


def test_inverted_model_window_is_ignored() -> None:
    output = PlanOutput(news_queries=["a"], time_start=date(2024, 1, 1), time_end=date(2023, 1, 1))
    assert build_plan(output, time_window=None).time_window is None


def test_blank_dates_from_model_are_none() -> None:
    output = PlanOutput.model_validate({"time_start": "", "time_end": " "})
    assert output.time_start is None
    assert output.time_end is None
