"""Cache interface with an in-process TTL implementation.

Values must be JSON-serializable (dicts, lists, strings, numbers) so a Redis-backed
implementation can replace `TTLCache` without changing callers.
"""

import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any, Protocol


class Cache(Protocol):
    async def get(self, key: str) -> Any | None: ...

    async def set(self, key: str, value: Any, ttl_seconds: float) -> None: ...


class TTLCache:
    """Expiring entries with least-recently-used eviction once `max_entries` is reached."""

    def __init__(self, max_entries: int = 1024, clock: Callable[[], float] = time.monotonic):
        self.max_entries = max_entries
        self.clock = clock
        self._entries: OrderedDict[str, tuple[float, Any]] = OrderedDict()

    async def get(self, key: str) -> Any | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if self.clock() >= expires_at:
            del self._entries[key]
            return None
        self._entries.move_to_end(key)
        return value

    async def set(self, key: str, value: Any, ttl_seconds: float) -> None:
        if ttl_seconds <= 0:
            return
        self._entries[key] = (self.clock() + ttl_seconds, value)
        self._entries.move_to_end(key)
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)

    def __len__(self) -> int:
        return len(self._entries)
