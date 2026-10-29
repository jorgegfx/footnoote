import time
from collections.abc import Callable

import httpx
import pytest

from footnote.http import (
    LimiterBusyError,
    RateLimiter,
    RetryTransport,
    ThrottledClient,
    parse_retry_after,
)


def scripted(
    *steps: int | Exception, headers: dict[int, dict[str, str]] | None = None
) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    """A transport that answers each request with the next status, or raises the next error.
    `headers` maps a 1-based request number to that response's headers."""
    queue = list(steps)
    headers = headers or {}
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        step = queue.pop(0)
        if isinstance(step, Exception):
            raise step
        return httpx.Response(step, headers=headers.get(len(seen), {}))

    return httpx.MockTransport(handle), seen


def client(inner: httpx.MockTransport, retries: int = 2) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=RetryTransport(inner, max_retries=retries, backoff=0.001))


async def test_retries_transient_statuses_then_succeeds() -> None:
    inner, seen = scripted(503, 429, 200)
    async with client(inner) as c:
        response = await c.get("https://api.example/x")
    assert response.status_code == 200
    assert len(seen) == 3


async def test_gives_up_after_max_retries() -> None:
    inner, seen = scripted(502, 502, 502)
    async with client(inner) as c:
        response = await c.get("https://api.example/x")
    assert response.status_code == 502
    assert len(seen) == 3


async def test_client_errors_are_not_retried() -> None:
    inner, seen = scripted(404)
    async with client(inner) as c:
        assert (await c.get("https://api.example/x")).status_code == 404
    assert len(seen) == 1


async def test_connection_errors_are_retried_then_raised() -> None:
    inner, seen = scripted(httpx.ConnectError("down"), 200)
    async with client(inner) as c:
        assert (await c.get("https://api.example/x")).status_code == 200

    inner, seen = scripted(*[httpx.ConnectError("down")] * 3)
    async with client(inner) as c:
        with pytest.raises(httpx.ConnectError):
            await c.get("https://api.example/x")
    assert len(seen) == 3


async def test_long_retry_after_is_not_waited_for() -> None:
    inner, seen = scripted(429, 200, headers={1: {"Retry-After": "120"}})
    async with client(inner) as c:
        response = await c.get("https://api.example/x")
    assert response.status_code == 429
    assert len(seen) == 1


@pytest.mark.parametrize(
    ("value", "check"),
    [
        (None, lambda v: v is None),
        ("", lambda v: v is None),
        ("2", lambda v: v == 2.0),
        ("-5", lambda v: v == 0.0),
        ("soon", lambda v: v is None),
        ("Wed, 21 Oct 2015 07:28:00 GMT", lambda v: v == 0.0),  # in the past
    ],
)
def test_parse_retry_after(value: str | None, check: Callable[[float | None], bool]) -> None:
    assert check(parse_retry_after(value))


async def test_rate_limiter_spaces_calls() -> None:
    limiter = RateLimiter(per_second=20)  # 50ms apart
    start = time.monotonic()
    for _ in range(3):
        await limiter.acquire()
    assert time.monotonic() - start >= 0.09


async def test_rate_limiter_fails_fast_when_queue_is_too_long() -> None:
    limiter = RateLimiter(per_second=1, max_wait=0.5)
    await limiter.acquire()  # immediate; reserves the next slot 1s out
    with pytest.raises(LimiterBusyError):
        await limiter.acquire()


async def test_throttled_client_passes_through() -> None:
    inner, seen = scripted(200, 201)
    async with httpx.AsyncClient(transport=inner) as raw:
        throttled = ThrottledClient(raw, RateLimiter(per_second=100))
        assert (await throttled.get("https://api.example/a", params={"q": "x"})).status_code == 200
        assert (await throttled.post("https://api.example/b", json={})).status_code == 201
    assert [r.method for r in seen] == ["GET", "POST"]
    assert seen[0].url.params["q"] == "x"
