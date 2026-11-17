from __future__ import annotations

from dataverse_views_mcp.cache import TTLCache
from tests.fakes import Clock


def test_returns_value_until_it_expires() -> None:
    clock = Clock()
    cache: TTLCache[str, int] = TTLCache(60, clock=clock)
    cache.put("a", 1)

    clock.now += 59
    assert cache.get("a") == 1
    clock.now += 1
    assert cache.get("a") is None
    assert len(cache) == 0


def test_zero_ttl_disables_caching() -> None:
    cache: TTLCache[str, int] = TTLCache(0)
    cache.put("a", 1)

    assert cache.get("a") is None


def test_is_bounded_and_evicts_least_recently_used() -> None:
    cache: TTLCache[str, int] = TTLCache(60, max_entries=2)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.get("a")
    cache.put("c", 3)

    assert cache.get("b") is None
    assert cache.get("a") == 1
    assert cache.get("c") == 3
    assert len(cache) == 2
