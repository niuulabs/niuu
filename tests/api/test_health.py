"""Contract tests for the Yggdrasil health endpoint."""

from __future__ import annotations

from collections import OrderedDict

from fastapi.testclient import TestClient

from yggdrasil.main import create_app
from yggdrasil.rate_limit import TokenBucketRateLimiter


def _client(app, client_ip: str) -> TestClient:
    return TestClient(app, client=(client_ip, 50000))


def test_health_passes_through_unchanged_below_the_rate_limit() -> None:
    client = _client(create_app(), "198.51.100.10")

    try:
        response = client.get("/health")
    finally:
        client.close()

    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_health_returns_429_and_retry_after_when_rate_limit_is_exceeded() -> None:
    client = _client(create_app(), "198.51.100.10")

    try:
        for _ in range(60):
            assert client.get("/health").status_code == 200
        response = client.get("/health")
    finally:
        client.close()

    assert response.status_code == 429
    assert response.headers["retry-after"] == "1"


def test_health_rate_limits_each_client_ip_independently() -> None:
    app = create_app()
    limited_client = _client(app, "198.51.100.10")
    isolated_client = _client(app, "198.51.100.11")

    try:
        for _ in range(60):
            assert limited_client.get("/health").status_code == 200
        limited_response = limited_client.get("/health")
        isolated_response = isolated_client.get("/health")
    finally:
        limited_client.close()
        isolated_client.close()

    assert limited_response.status_code == 429
    assert isolated_response.status_code == 200


def test_health_rate_limit_refills_one_token_per_second() -> None:
    now = 0.0
    limiter = TokenBucketRateLimiter(clock=lambda: now)

    for _ in range(60):
        assert limiter.retry_after(endpoint="/health", client_ip="198.51.100.10") is None
    assert limiter.retry_after(endpoint="/health", client_ip="198.51.100.10") == 1

    now = 1.0

    assert limiter.retry_after(endpoint="/health", client_ip="198.51.100.10") is None


def test_health_rate_limit_discards_inactive_full_buckets() -> None:
    now = 0.0
    limiter = TokenBucketRateLimiter(
        capacity=1,
        refill_rate=1.0,
        bucket_idle_ttl=1.0,
        max_buckets=2,
        clock=lambda: now,
    )

    assert limiter.retry_after(endpoint="/health", client_ip="198.51.100.10") is None

    now = 1.0

    assert limiter.retry_after(endpoint="/health", client_ip="198.51.100.11") is None
    assert ("/health", "198.51.100.10") not in limiter._buckets


def test_health_rate_limit_does_not_scan_unexpired_buckets() -> None:
    class CountingOrderedDict(OrderedDict):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.inspected_buckets = 0

        def __iter__(self):
            for bucket_key in super().__iter__():
                self.inspected_buckets += 1
                yield bucket_key

    now = 0.0
    limiter = TokenBucketRateLimiter(
        bucket_idle_ttl=60.0,
        max_buckets=100,
        clock=lambda: now,
    )
    for client_number in range(100):
        assert (
            limiter.retry_after(endpoint="/health", client_ip=f"198.51.100.{client_number}") is None
        )

    limiter._buckets = CountingOrderedDict(limiter._buckets)
    now = 1.0

    assert limiter.retry_after(endpoint="/health", client_ip="198.51.100.0") is None
    assert limiter._buckets.inspected_buckets == 1


def test_health_rate_limit_caps_retained_buckets() -> None:
    limiter = TokenBucketRateLimiter(
        bucket_idle_ttl=60.0,
        max_buckets=2,
        clock=lambda: 0.0,
    )

    for client_ip in ("198.51.100.10", "198.51.100.11", "198.51.100.12"):
        assert limiter.retry_after(endpoint="/health", client_ip=client_ip) is None

    assert len(limiter._buckets) == 2
    assert ("/health", "198.51.100.10") not in limiter._buckets
