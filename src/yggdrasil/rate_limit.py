"""In-memory token bucket rate limiting for Yggdrasil HTTP endpoints."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from math import ceil
from threading import Lock
from time import monotonic

HEALTH_RATE_LIMIT_CAPACITY = 60
HEALTH_RATE_LIMIT_REFILL_RATE = 1.0
HEALTH_RATE_LIMIT_BUCKET_IDLE_TTL = 60.0
HEALTH_RATE_LIMIT_MAX_BUCKETS = 10_000


@dataclass
class _TokenBucket:
    """Mutable state for one endpoint and client IP pair."""

    tokens: float
    updated_at: float


class TokenBucketRateLimiter:
    """Limit each client IP independently for each endpoint."""

    def __init__(
        self,
        *,
        capacity: int = HEALTH_RATE_LIMIT_CAPACITY,
        refill_rate: float = HEALTH_RATE_LIMIT_REFILL_RATE,
        bucket_idle_ttl: float = HEALTH_RATE_LIMIT_BUCKET_IDLE_TTL,
        max_buckets: int = HEALTH_RATE_LIMIT_MAX_BUCKETS,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._capacity = capacity
        self._refill_rate = refill_rate
        self._bucket_idle_ttl = bucket_idle_ttl
        self._max_buckets = max_buckets
        self._clock = clock
        self._buckets: OrderedDict[tuple[str, str], _TokenBucket] = OrderedDict()
        self._lock = Lock()

    def retry_after(self, *, endpoint: str, client_ip: str) -> int | None:
        """Consume a token and return a retry delay when the bucket is empty."""
        with self._lock:
            now = self._clock()
            self._discard_inactive_buckets(now)
            bucket_key = (endpoint, client_ip)
            bucket = self._buckets.get(bucket_key)
            if bucket is None:
                self._discard_oldest_buckets()
                bucket = _TokenBucket(tokens=float(self._capacity), updated_at=now)
                self._buckets[bucket_key] = bucket
            else:
                self._buckets.move_to_end(bucket_key)

            elapsed = max(0.0, now - bucket.updated_at)
            bucket.tokens = min(self._capacity, bucket.tokens + elapsed * self._refill_rate)
            bucket.updated_at = now
            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return None

            return max(1, ceil((1.0 - bucket.tokens) / self._refill_rate))

    def _discard_inactive_buckets(self, now: float) -> None:
        while self._buckets:
            bucket_key = next(iter(self._buckets))
            bucket = self._buckets[bucket_key]
            elapsed = now - bucket.updated_at
            refilled_tokens = min(self._capacity, bucket.tokens + elapsed * self._refill_rate)
            if elapsed < self._bucket_idle_ttl or refilled_tokens < self._capacity:
                return
            self._buckets.popitem(last=False)

    def _discard_oldest_buckets(self) -> None:
        while len(self._buckets) >= self._max_buckets:
            self._buckets.popitem(last=False)
