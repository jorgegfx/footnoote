"""Outbound HTTP hardening: retries with backoff and per-source rate limiting."""

import asyncio
import email.utils
import random
import time
from typing import Any

import httpx

RETRY_STATUSES = frozenset({429, 502, 503, 504})
MAX_RETRY_AFTER = 3.0  # seconds; a longer Retry-After isn't worth waiting for inside a request


class RetryTransport(httpx.AsyncBaseTransport):
    """Retries connection failures and 429/502/503/504 with jittered exponential backoff.

    Honors `Retry-After` up to `MAX_RETRY_AFTER`; beyond that the response is returned as is.
    The caller's own timeout (the adapter time limit) bounds the total time spent.
    """

    def __init__(
        self,
        inner: httpx.AsyncBaseTransport | None = None,
        *,
        max_retries: int = 2,
        backoff: float = 0.5,
    ):
        self.inner = inner or httpx.AsyncHTTPTransport()
        self.max_retries = max_retries
        self.backoff = backoff

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        attempt = 0
        while True:
            try:
                response = await self.inner.handle_async_request(request)
            except (httpx.ConnectError, httpx.RemoteProtocolError):
                if attempt >= self.max_retries:
                    raise
                await asyncio.sleep(self._delay(attempt))
            else:
                if response.status_code not in RETRY_STATUSES or attempt >= self.max_retries:
                    return response
                retry_after = parse_retry_after(response.headers.get("Retry-After"))
                if retry_after is not None and retry_after > MAX_RETRY_AFTER:
                    return response
                await response.aclose()
                await asyncio.sleep(max(retry_after or 0.0, self._delay(attempt)))
            attempt += 1

    def _delay(self, attempt: int) -> float:
        base: float = self.backoff * 2**attempt
        return base * (0.5 + random.random() / 2)

    async def aclose(self) -> None:
        await self.inner.aclose()


def parse_retry_after(value: str | None) -> float | None:
    """Seconds from a `Retry-After` header (delta-seconds or HTTP date)."""
    if not value:
        return None
    try:
        return max(float(value), 0.0)
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max(when.timestamp() - time.time(), 0.0)


class LimiterBusyError(Exception):
    """The local rate limit's queue is longer than the caller is willing to wait."""


class RateLimiter:
    """Spaces calls at least `1 / per_second` apart, shared by all requests in the process.

    A call that would have to wait longer than `max_wait` fails fast instead of queueing.
    """

    def __init__(self, per_second: float, max_wait: float = 8.0):
        self.interval = 1 / per_second
        self.max_wait = max_wait
        self._next = 0.0

    async def acquire(self) -> None:
        now = time.monotonic()
        wait = self._next - now
        if wait > self.max_wait:
            raise LimiterBusyError
        self._next = max(now, self._next) + self.interval  # reserve before awaiting
        if wait > 0:
            await asyncio.sleep(wait)


class ThrottledClient:
    """The subset of `httpx.AsyncClient` adapters use, with an optional rate limit in front."""

    def __init__(self, client: httpx.AsyncClient, limiter: RateLimiter | None = None):
        self.client = client
        self.limiter = limiter

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        if self.limiter is not None:
            await self.limiter.acquire()
        return await self.client.get(url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        if self.limiter is not None:
            await self.limiter.acquire()
        return await self.client.post(url, **kwargs)
