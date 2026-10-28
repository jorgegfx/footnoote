from datetime import date

import pytest

from footnote.graph.nodes.plan import EmptyPlanError, PlanOutput, build_plan
from footnote.models import Family

Q = "What did the latest IPCC report say about sea level rise?"


def test_both_intent_keeps_both_families() -> None:
    plan = build_plan(
        PlanOutput(intent="both", news_queries=["IPCC report"], academic_queries=["sea level"]),
        question=Q,
        families=None,
        time_window=None,
    )
    assert plan.intent == "both"
    assert plan.queries == {Family.NEWS: ["IPCC report"], Family.ACADEMIC: ["sea level"]}


def test_intent_wins_over_stray_queries() -> None:
    plan = build_plan(
        PlanOutput(intent="news", news_queries=["a"], academic_queries=["stray"]),
        question=Q,
        families=None,
        time_window=None,
    )
    assert plan.queries == {Family.NEWS: ["a"]}
    assert plan.intent == "news"


def test_requested_family_falls_back_to_question() -> None:
    plan = build_plan(
        PlanOutput(intent="news", news_queries=["a"]),
        question=Q,
        families=[Family.ACADEMIC],
        time_window=None,
    )
    assert plan.queries == {Family.ACADEMIC: [Q]}
    assert plan.intent == "academic"


def test_queries_are_cleaned_deduped_and_capped() -> None:
    plan = build_plan(
        PlanOutput(intent="news", news_queries=["  sea  level ", "Sea Level", "", "b", "c", "d"]),
        question=Q,
        families=None,
        time_window=None,
    )
    assert plan.queries[Family.NEWS] == ["sea level", "b", "c"]


def test_empty_plan_raises() -> None:
    with pytest.raises(EmptyPlanError):
        build_plan(PlanOutput(intent="news"), question=Q, families=None, time_window=None)


def test_time_window_from_model_and_request_override() -> None:
    output = PlanOutput(
        intent="news", news_queries=["a"], time_start=date(2023, 1, 1), time_end=date(2023, 12, 31)
    )
    plan = build_plan(output, question=Q, families=None, time_window=None)
    assert plan.time_window == (date(2023, 1, 1), date(2023, 12, 31))

    requested = (date(2024, 1, 1), date(2024, 2, 1))
    assert build_plan(output, question=Q, families=None, time_window=requested).time_window == (
        requested
    )


def test_inverted_model_window_is_ignored() -> None:
    output = PlanOutput(
        intent="news", news_queries=["a"], time_start=date(2024, 1, 1), time_end=date(2023, 1, 1)
    )
    assert build_plan(output, question=Q, families=None, time_window=None).time_window is None
