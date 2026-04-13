"""Small in-memory anti-spam primitives for Telegram runtime."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Callable, Hashable


class FixedWindowRateLimiter:
    """Per-key fixed-window limiter with lightweight in-memory state."""

    def __init__(self, *, now_provider: Callable[[], float]) -> None:
        self._now_provider = now_provider
        self._events: dict[Hashable, deque[float]] = defaultdict(deque)

    def allow(self, *, key: Hashable, limit: int, window_seconds: float) -> bool:
        if limit < 1:
            return False
        if window_seconds <= 0:
            return True

        now_value = self._now_provider()
        window_start = now_value - window_seconds
        bucket = self._events[key]
        while bucket and bucket[0] <= window_start:
            bucket.popleft()

        if len(bucket) >= limit:
            return False

        bucket.append(now_value)
        return True


class CallbackDebounceCache:
    """Short-lived cache that blocks repeated identical callback hits."""

    def __init__(self, *, now_provider: Callable[[], float], ttl_seconds: float) -> None:
        self._now_provider = now_provider
        self._ttl_seconds = ttl_seconds
        self._last_seen: dict[Hashable, float] = {}

    def is_duplicate(self, *, key: Hashable) -> bool:
        if self._ttl_seconds <= 0:
            return False

        now_value = self._now_provider()
        last_seen = self._last_seen.get(key)
        self._last_seen[key] = now_value
        if last_seen is None:
            return False
        return now_value - last_seen <= self._ttl_seconds
