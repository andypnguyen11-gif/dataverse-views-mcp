"""A small, bounded, time-limited cache. Safe to share between concurrent requests."""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable


class TTLCache[K, V]:
    """Keeps at most `max_entries` values, each for at most `ttl_seconds`.

    A `ttl_seconds` of 0 disables caching. There are no awaits inside any method, so
    operations are atomic under asyncio.
    """

    def __init__(
        self,
        ttl_seconds: float,
        max_entries: int = 256,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = ttl_seconds
        self._max = max_entries
        self._clock = clock
        self._items: OrderedDict[K, tuple[float, V]] = OrderedDict()

    def get(self, key: K) -> V | None:
        entry = self._items.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if self._clock() >= expires_at:
            del self._items[key]
            return None
        self._items.move_to_end(key)
        return value

    def put(self, key: K, value: V) -> None:
        if self._ttl <= 0:
            return
        self._items[key] = (self._clock() + self._ttl, value)
        self._items.move_to_end(key)
        while len(self._items) > self._max:
            self._items.popitem(last=False)

    def __len__(self) -> int:
        return len(self._items)
