import httpx

from footnote.models import Family, QueryPlan
from footnote.sources.base import run_adapter
from tests.fakes import StaticAdapter, make_result

PLAN = QueryPlan(queries={Family.NEWS: ["q"]})


async def test_success() -> None:
    adapter = StaticAdapter("reuters", Family.NEWS, [make_result(1)])
    outcome = await run_adapter(adapter, PLAN, limit_seconds=1)
    assert outcome.ok
    assert len(outcome.results) == 1


async def test_timeout() -> None:
    adapter = StaticAdapter("slow", Family.NEWS, delay=5)
    outcome = await run_adapter(adapter, PLAN, limit_seconds=0.05)
    assert not outcome.ok
    assert outcome.error == "timed out after 0.05s"


async def test_network_error() -> None:
    request = httpx.Request("GET", "https://api.example.org/?api_key=secret")
    adapter = StaticAdapter("x", Family.NEWS, error=httpx.ConnectError("boom", request=request))
    outcome = await run_adapter(adapter, PLAN, limit_seconds=1)
    assert outcome.error == "network error"


async def test_unexpected_error_never_raises() -> None:
    adapter = StaticAdapter("x", Family.NEWS, error=KeyError("results"))
    outcome = await run_adapter(adapter, PLAN, limit_seconds=1)
    assert not outcome.ok
    assert outcome.error == "unexpected error (KeyError)"
