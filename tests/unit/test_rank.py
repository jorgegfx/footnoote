from datetime import date

import pytest

from footnote.graph.nodes.rank import lexical_relevance, quality
from footnote.models import Family, QueryPlan
from tests.fakes import make_result

TODAY = date(2026, 10, 28)
NEWS_PLAN = QueryPlan(queries={Family.NEWS: ["q"]})


def test_news_recency_decays_with_half_life() -> None:
    fresh = make_result(1, published_at=TODAY)
    month_old = make_result(2, published_at=date(2026, 9, 28))
    year_old = make_result(3, published_at=date(2025, 10, 28))
    assert quality(fresh, NEWS_PLAN, TODAY) == pytest.approx(1.0)
    assert quality(month_old, NEWS_PLAN, TODAY) == pytest.approx(0.7)
    assert quality(year_old, NEWS_PLAN, TODAY) == pytest.approx(0.4, abs=0.01)


def test_news_recency_ignored_inside_requested_window() -> None:
    plan = NEWS_PLAN.model_copy(update={"time_window": (date(2020, 1, 1), date(2020, 12, 31))})
    old = make_result(1, published_at=date(2020, 3, 1))
    assert quality(old, plan, TODAY) == 1.0


def test_undated_news_gets_middling_quality() -> None:
    assert quality(make_result(1), NEWS_PLAN, TODAY) == 0.7


def test_lexical_relevance() -> None:
    hit = make_result(1, title="Coffee and blood pressure", snippet="")
    miss = make_result(2, title="Tea markets", snippet="")
    question = "Does coffee raise blood pressure?"
    assert lexical_relevance(question, hit) == pytest.approx(0.75)
    assert lexical_relevance(question, miss) == 0.0
