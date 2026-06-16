"""Shared fixtures for Sleipnir broker integration tests.

These tests require real broker services (NATS, RabbitMQ, Redis) and are
excluded from the default test run.  Enable with ``-m broker``.

Connection URLs default to localhost and can be overridden via env vars
to match CI service containers.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime

import pytest

from sleipnir.domain.events import SleipnirEvent

# ---------------------------------------------------------------------------
# Env-var connection URLs (same pattern as TEST_DATABASE_*)
# ---------------------------------------------------------------------------

NATS_URL = os.environ.get("TEST_NATS_URL", "nats://localhost:4222")
RABBITMQ_URL = os.environ.get("TEST_RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379")
TEST_NATS_MAX_BYTES = int(os.environ.get("TEST_NATS_MAX_BYTES", str(8 * 1024 * 1024)))
TEST_NATS_MAX_AGE_SECONDS = int(os.environ.get("TEST_NATS_MAX_AGE_SECONDS", "300"))

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DEFAULT_TIMESTAMP = datetime(2026, 4, 6, 12, 0, 0, tzinfo=UTC)


def make_event(**kwargs: object) -> SleipnirEvent:
    """Return a SleipnirEvent with sensible defaults; all fields overridable."""
    defaults: dict = {
        "event_type": "ravn.tool.complete",
        "source": "test:broker-integration",
        "payload": {"tool": "bash", "exit_code": 0},
        "summary": "Test event",
        "urgency": 0.5,
        "domain": "code",
        "timestamp": DEFAULT_TIMESTAMP,
    }
    defaults.update(kwargs)
    if "event_id" not in defaults:
        defaults["event_id"] = str(uuid.uuid4())
    return SleipnirEvent(**defaults)


# ---------------------------------------------------------------------------
# Async helper: collect N events with timeout
# ---------------------------------------------------------------------------


async def collect_events(
    count: int,
    received: list[SleipnirEvent],
    timeout: float = 5.0,
) -> list[SleipnirEvent]:
    """Wait until *received* has at least *count* items, or *timeout* expires."""
    deadline = asyncio.get_event_loop().time() + timeout
    while len(received) < count:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            break
        await asyncio.sleep(0.05)
    return list(received)


# ---------------------------------------------------------------------------
# Transport fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def nats_transport():
    """Yield a started NatsTransport connected to the test NATS server."""
    from sleipnir.adapters.nats_transport import NatsTransport

    stream_name = f"sleipnir_test_{uuid.uuid4().hex[:8]}"
    transport = NatsTransport(
        servers=[NATS_URL],
        stream_name=stream_name,
        subject_prefix=stream_name,
        max_reconnect_attempts=3,
        connect_timeout_s=5.0,
        max_bytes=TEST_NATS_MAX_BYTES,
        max_age_seconds=TEST_NATS_MAX_AGE_SECONDS,
    )
    async with transport:
        yield transport


@pytest.fixture
async def rabbitmq_transport():
    """Yield a started RabbitMQTransport connected to the test RabbitMQ."""
    from sleipnir.adapters.rabbitmq import RabbitMQTransport

    exchange = f"sleipnir_test_{uuid.uuid4().hex[:8]}"
    transport = RabbitMQTransport(
        url=RABBITMQ_URL,
        exchange_name=exchange,
        dead_letter_exchange=f"{exchange}_dlx",
    )
    async with transport:
        yield transport


@pytest.fixture
async def redis_transport():
    """Yield a started RedisStreamsTransport connected to the test Redis."""
    from sleipnir.adapters.redis_streams import RedisStreamsTransport

    prefix = f"sleipnir_test_{uuid.uuid4().hex[:8]}"
    transport = RedisStreamsTransport(
        url=REDIS_URL,
        stream_prefix=prefix,
    )
    async with transport:
        yield transport
