"""Tests for ting.adapters.bifrost_publisher.BifrostPublisher."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from sleipnir.adapters.in_process import InProcessBus
from sleipnir.domain.registry import (
    BIFROST_PROVIDER_DOWN,
    BIFROST_PROVIDER_RECOVERED,
    BIFROST_QUOTA_EXCEEDED,
    BIFROST_QUOTA_WARNING,
    BIFROST_REQUEST_COMPLETE,
)
from sleipnir.testing import EventCapture
from ting.adapters.bifrost_publisher import BifrostPublisher

# ---------------------------------------------------------------------------
# bifrost.request.complete
# ---------------------------------------------------------------------------


class TestRequestComplete:
    @pytest.mark.asyncio
    async def test_publishes_correct_event_type(self) -> None:
        bus = InProcessBus()
        pub = BifrostPublisher(bus)

        async with EventCapture(bus, [BIFROST_REQUEST_COMPLETE]) as capture:
            await pub.request_complete(
                model="claude-sonnet-4-6",
                input_tokens=100,
                output_tokens=200,
                latency_ms=1234.5,
            )
            await bus.flush()

        assert len(capture.events) == 1

    @pytest.mark.asyncio
    async def test_urgency_is_zero(self) -> None:
        bus = InProcessBus()
        pub = BifrostPublisher(bus)

        async with EventCapture(bus, [BIFROST_REQUEST_COMPLETE]) as capture:
            await pub.request_complete(
                model="m",
                input_tokens=10,
                output_tokens=20,
                latency_ms=100.0,
            )
            await bus.flush()

        assert capture.events[0].urgency == 0.0

    @pytest.mark.asyncio
    async def test_payload_contains_token_fields(self) -> None:
        bus = InProcessBus()
        pub = BifrostPublisher(bus)

        async with EventCapture(bus, [BIFROST_REQUEST_COMPLETE]) as capture:
            await pub.request_complete(
                model="claude-opus-4-6",
                input_tokens=300,
                output_tokens=700,
                latency_ms=500.0,
            )
            await bus.flush()

        evt = capture.events[0]
        assert evt.payload["model"] == "claude-opus-4-6"
        assert evt.payload["input_tokens"] == 300
        assert evt.payload["output_tokens"] == 700
        assert evt.payload["total_tokens"] == 1000
        assert evt.payload["latency_ms"] == 500.0

    @pytest.mark.asyncio
    async def test_agent_id_in_payload_and_correlation(self) -> None:
        bus = InProcessBus()
        pub = BifrostPublisher(bus, agent_id="saga-abc")

        async with EventCapture(bus, [BIFROST_REQUEST_COMPLETE]) as capture:
            await pub.request_complete(model="m", input_tokens=1, output_tokens=1, latency_ms=1.0)
            await bus.flush()

        evt = capture.events[0]
        assert evt.payload["agent_id"] == "saga-abc"
        assert evt.correlation_id == "saga-abc"

    @pytest.mark.asyncio
    async def test_tenant_id_forwarded(self) -> None:
        bus = InProcessBus()
        pub = BifrostPublisher(bus, tenant_id="tenant-xyz")

        async with EventCapture(bus, [BIFROST_REQUEST_COMPLETE]) as capture:
            await pub.request_complete(model="m", input_tokens=1, output_tokens=1, latency_ms=1.0)
            await bus.flush()

        assert capture.events[0].tenant_id == "tenant-xyz"

    @pytest.mark.asyncio
    async def test_domain_is_infrastructure(self) -> None:
        bus = InProcessBus()
        pub = BifrostPublisher(bus)

        async with EventCapture(bus, [BIFROST_REQUEST_COMPLETE]) as capture:
            await pub.request_complete(model="m", input_tokens=1, output_tokens=1, latency_ms=1.0)
            await bus.flush()

        assert capture.events[0].domain == "infrastructure"


# ---------------------------------------------------------------------------
# bifrost.quota.warning
# ---------------------------------------------------------------------------


class TestQuotaWarning:
    @pytest.mark.asyncio
    async def test_publishes_correct_event_type(self) -> None:
        bus = InProcessBus()
        pub = BifrostPublisher(bus)

        async with EventCapture(bus, [BIFROST_QUOTA_WARNING]) as capture:
            await pub.quota_warning(tokens_used=8000, budget_tokens=10000)
            await bus.flush()

        assert len(capture.events) == 1

    @pytest.mark.asyncio
    async def test_urgency_is_point_five(self) -> None:
        bus = InProcessBus()
        pub = BifrostPublisher(bus)

        async with EventCapture(bus, [BIFROST_QUOTA_WARNING]) as capture:
            await pub.quota_warning(tokens_used=8000, budget_tokens=10000)
            await bus.flush()

        assert capture.events[0].urgency == 0.5

    @pytest.mark.asyncio
    async def test_payload_contains_quota_fields(self) -> None:
        bus = InProcessBus()
        pub = BifrostPublisher(bus, agent_id="agent-42")

        async with EventCapture(bus, [BIFROST_QUOTA_WARNING]) as capture:
            await pub.quota_warning(tokens_used=8000, budget_tokens=10000)
            await bus.flush()

        p = capture.events[0].payload
        assert p["agent_id"] == "agent-42"
        assert p["tokens_used"] == 8000
        assert p["budget_tokens"] == 10000
        assert p["pct_used"] == pytest.approx(0.8, rel=1e-4)


# ---------------------------------------------------------------------------
# bifrost.quota.exceeded
# ---------------------------------------------------------------------------


class TestQuotaExceeded:
    @pytest.mark.asyncio
    async def test_publishes_correct_event_type(self) -> None:
        bus = InProcessBus()
        pub = BifrostPublisher(bus)

        async with EventCapture(bus, [BIFROST_QUOTA_EXCEEDED]) as capture:
            await pub.quota_exceeded(tokens_used=11000, budget_tokens=10000)
            await bus.flush()

        assert len(capture.events) == 1

    @pytest.mark.asyncio
    async def test_urgency_is_point_seven(self) -> None:
        bus = InProcessBus()
        pub = BifrostPublisher(bus)

        async with EventCapture(bus, [BIFROST_QUOTA_EXCEEDED]) as capture:
            await pub.quota_exceeded(tokens_used=11000, budget_tokens=10000)
            await bus.flush()

        assert capture.events[0].urgency == 0.7

    @pytest.mark.asyncio
    async def test_payload_contains_quota_fields(self) -> None:
        bus = InProcessBus()
        pub = BifrostPublisher(bus, agent_id="saga-1")

        async with EventCapture(bus, [BIFROST_QUOTA_EXCEEDED]) as capture:
            await pub.quota_exceeded(tokens_used=11000, budget_tokens=10000)
            await bus.flush()

        p = capture.events[0].payload
        assert p["agent_id"] == "saga-1"
        assert p["tokens_used"] == 11000
        assert p["budget_tokens"] == 10000


# ---------------------------------------------------------------------------
# bifrost.provider.down
# ---------------------------------------------------------------------------


class TestProviderDown:
    @pytest.mark.asyncio
    async def test_publishes_correct_event_type(self) -> None:
        bus = InProcessBus()
        pub = BifrostPublisher(bus)

        async with EventCapture(bus, [BIFROST_PROVIDER_DOWN]) as capture:
            await pub.provider_down(
                provider="https://api.anthropic.com",
                status_code=500,
                error="Internal Server Error",
            )
            await bus.flush()

        assert len(capture.events) == 1

    @pytest.mark.asyncio
    async def test_urgency_is_point_eight(self) -> None:
        """Sköll receives bifrost.provider.down at urgency 0.8."""
        bus = InProcessBus()
        pub = BifrostPublisher(bus)

        async with EventCapture(bus, [BIFROST_PROVIDER_DOWN]) as capture:
            await pub.provider_down(
                provider="https://api.anthropic.com",
                status_code=503,
                error="Service Unavailable",
            )
            await bus.flush()

        assert capture.events[0].urgency == 0.8

    @pytest.mark.asyncio
    async def test_payload_contains_provider_fields(self) -> None:
        bus = InProcessBus()
        pub = BifrostPublisher(bus)

        async with EventCapture(bus, [BIFROST_PROVIDER_DOWN]) as capture:
            await pub.provider_down(
                provider="https://bifrost.niuu.io",
                status_code=502,
                error="Bad Gateway",
            )
            await bus.flush()

        p = capture.events[0].payload
        assert p["provider"] == "https://bifrost.niuu.io"
        assert p["status_code"] == 502
        assert p["error"] == "Bad Gateway"


# ---------------------------------------------------------------------------
# bifrost.provider.recovered
# ---------------------------------------------------------------------------


class TestProviderRecovered:
    @pytest.mark.asyncio
    async def test_publishes_correct_event_type(self) -> None:
        bus = InProcessBus()
        pub = BifrostPublisher(bus)

        async with EventCapture(bus, [BIFROST_PROVIDER_RECOVERED]) as capture:
            await pub.provider_recovered(provider="https://api.anthropic.com")
            await bus.flush()

        assert len(capture.events) == 1

    @pytest.mark.asyncio
    async def test_urgency_is_point_three(self) -> None:
        bus = InProcessBus()
        pub = BifrostPublisher(bus)

        async with EventCapture(bus, [BIFROST_PROVIDER_RECOVERED]) as capture:
            await pub.provider_recovered(provider="https://api.anthropic.com")
            await bus.flush()

        assert capture.events[0].urgency == 0.3

    @pytest.mark.asyncio
    async def test_payload_contains_provider(self) -> None:
        bus = InProcessBus()
        pub = BifrostPublisher(bus)

        async with EventCapture(bus, [BIFROST_PROVIDER_RECOVERED]) as capture:
            await pub.provider_recovered(provider="https://bifrost.niuu.io")
            await bus.flush()

        assert capture.events[0].payload["provider"] == "https://bifrost.niuu.io"


# ---------------------------------------------------------------------------
# Fault tolerance
# ---------------------------------------------------------------------------


class TestFaultTolerance:
    @pytest.mark.asyncio
    async def test_publish_error_is_swallowed(self) -> None:
        mock_publisher = AsyncMock()
        mock_publisher.publish.side_effect = RuntimeError("sleipnir down")
        pub = BifrostPublisher(mock_publisher)

        # Must not raise
        await pub.request_complete(model="m", input_tokens=1, output_tokens=1, latency_ms=1.0)
        await pub.provider_down(provider="p", status_code=500, error="err")
        await pub.provider_recovered(provider="p")
        await pub.quota_warning(tokens_used=80, budget_tokens=100)
        await pub.quota_exceeded(tokens_used=110, budget_tokens=100)
