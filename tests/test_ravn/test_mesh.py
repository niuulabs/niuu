"""Tests for MeshPort and SleipnirMeshAdapter.

All tests run without real broker connections — Sleipnir transports are mocked.

Coverage targets:
- MeshPort protocol conformance
- PeerNotFoundError
- SleipnirMeshAdapter: publish, subscribe, unsubscribe, send, rpc handler,
  timeout, peer-not-found, start/stop
- RavnEvent <-> SleipnirEvent conversion
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from ravn.adapters.mesh.sleipnir_mesh import SleipnirMeshAdapter
from ravn.config import MeshConfig
from ravn.domain.events import RavnEvent, RavnEventType
from ravn.ports.mesh import MeshPort, PeerNotFoundError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(source: str = "test-ravn") -> RavnEvent:
    return RavnEvent.thought(
        source=source,
        text="hello mesh",
        correlation_id="cid-1",
        session_id="sess-1",
    )


class _FakeDiscovery:
    """Minimal DiscoveryPort stub."""

    def __init__(self, peers: dict | None = None) -> None:
        self._peers: dict = peers or {}

    def peers(self) -> dict:
        return self._peers


@dataclass
class _FakeSubscription:
    """Fake Sleipnir subscription."""

    topic: str
    handler: Callable
    active: bool = True

    async def unsubscribe(self) -> None:
        self.active = False


class _FakeSleipnirTransport:
    """Fake Sleipnir transport implementing both publisher and subscriber."""

    def __init__(self) -> None:
        self.published: list[Any] = []
        self.subscriptions: list[_FakeSubscription] = []

    async def publish(self, event: Any) -> None:
        self.published.append(event)
        # Deliver to matching subscriptions
        for sub in self.subscriptions:
            if sub.active and self._matches(sub.topic, event.event_type):
                await sub.handler(event)

    async def publish_batch(self, events: list) -> None:
        for event in events:
            await self.publish(event)

    async def subscribe(
        self,
        event_types: list[str],
        handler: Callable,
    ) -> _FakeSubscription:
        # For simplicity, use first pattern
        topic = event_types[0] if event_types else "*"
        sub = _FakeSubscription(topic=topic, handler=handler)
        self.subscriptions.append(sub)
        return sub

    def _matches(self, pattern: str, event_type: str) -> bool:
        """Simple pattern matching."""
        if pattern == "*":
            return True
        if pattern.endswith("*"):
            return event_type.startswith(pattern[:-1])
        return pattern == event_type


# ---------------------------------------------------------------------------
# MeshPort Protocol
# ---------------------------------------------------------------------------


class TestMeshPortProtocol:
    """Verify adapters satisfy the MeshPort protocol."""

    def test_sleipnir_adapter_satisfies_protocol(self) -> None:
        transport = _FakeSleipnirTransport()
        adapter = SleipnirMeshAdapter(
            publisher=transport,
            subscriber=transport,
            own_peer_id="test-peer",
        )
        assert isinstance(adapter, MeshPort)

    def test_peer_not_found_error_carries_peer_id(self) -> None:
        err = PeerNotFoundError("missing-peer")
        assert err.peer_id == "missing-peer"
        assert "missing-peer" in str(err)


# ---------------------------------------------------------------------------
# SleipnirMeshAdapter Unit Tests
# ---------------------------------------------------------------------------


class TestSleipnirMeshAdapterUnit:
    """Unit tests for SleipnirMeshAdapter."""

    @pytest.fixture
    def transport(self) -> _FakeSleipnirTransport:
        return _FakeSleipnirTransport()

    @pytest.fixture
    def adapter(self, transport: _FakeSleipnirTransport) -> SleipnirMeshAdapter:
        return SleipnirMeshAdapter(
            publisher=transport,
            subscriber=transport,
            own_peer_id="test-peer",
        )

    @pytest.mark.asyncio
    async def test_publish_sends_to_transport(
        self, adapter: SleipnirMeshAdapter, transport: _FakeSleipnirTransport
    ) -> None:
        event = _make_event()
        await adapter.publish(event, "test.topic")

        assert len(transport.published) == 1
        sleipnir_event = transport.published[0]
        assert sleipnir_event.event_type == "ravn.mesh.test.topic"
        assert sleipnir_event.payload["ravn_type"] == "thought"

    @pytest.mark.asyncio
    async def test_publish_sanitizes_invalid_topic_segments(
        self, adapter: SleipnirMeshAdapter, transport: _FakeSleipnirTransport
    ) -> None:
        event = _make_event(source="flock-coder")

        await adapter.publish(event, "activity.flock-coder")

        assert len(transport.published) == 1
        sleipnir_event = transport.published[0]
        assert sleipnir_event.event_type == "ravn.mesh.activity.flock_coder"
        assert sleipnir_event.payload["ravn_source"] == "flock-coder"

    @pytest.mark.asyncio
    async def test_subscribe_registers_handler(
        self, adapter: SleipnirMeshAdapter, transport: _FakeSleipnirTransport
    ) -> None:
        handler = AsyncMock()
        await adapter.subscribe("test.topic", handler)

        assert len(transport.subscriptions) == 1
        assert transport.subscriptions[0].topic == "ravn.mesh.test.topic"

    @pytest.mark.asyncio
    async def test_subscribe_sanitizes_invalid_topic_segments(
        self, adapter: SleipnirMeshAdapter, transport: _FakeSleipnirTransport
    ) -> None:
        handler = AsyncMock()

        await adapter.subscribe("activity.flock-coder", handler)

        assert len(transport.subscriptions) == 1
        assert transport.subscriptions[0].topic == "ravn.mesh.activity.flock_coder"

    @pytest.mark.asyncio
    async def test_subscribe_handler_receives_converted_event(
        self, adapter: SleipnirMeshAdapter, transport: _FakeSleipnirTransport
    ) -> None:
        received_events: list[RavnEvent] = []

        async def handler(event: RavnEvent) -> None:
            received_events.append(event)

        await adapter.subscribe("test.topic", handler)

        # Publish an event
        event = _make_event()
        await adapter.publish(event, "test.topic")

        assert len(received_events) == 1
        assert received_events[0].type == RavnEventType.THOUGHT

    @pytest.mark.asyncio
    async def test_unsubscribe_removes_handler(
        self, adapter: SleipnirMeshAdapter, transport: _FakeSleipnirTransport
    ) -> None:
        handler = AsyncMock()
        await adapter.subscribe("test.topic", handler)
        await adapter.unsubscribe("test.topic")

        # Subscription should be inactive
        assert not transport.subscriptions[0].active

    @pytest.mark.asyncio
    async def test_send_raises_peer_not_found(self, transport: _FakeSleipnirTransport) -> None:
        discovery = _FakeDiscovery(peers={})  # No peers
        adapter = SleipnirMeshAdapter(
            publisher=transport,
            subscriber=transport,
            own_peer_id="test-peer",
            discovery=discovery,
        )

        with pytest.raises(PeerNotFoundError):
            await adapter.send("unknown-peer", {"msg": "hello"})

    @pytest.mark.asyncio
    async def test_send_without_discovery_allows_any_peer(
        self, adapter: SleipnirMeshAdapter, transport: _FakeSleipnirTransport
    ) -> None:
        # No discovery = trust all peers
        # Set up response handler that uses the reply_topic from the request
        async def respond(event: Any) -> None:
            if "rpc_request" in event.payload:
                # The reply_topic is already sanitized by the adapter
                reply_topic = event.payload["reply_topic"]
                from sleipnir.domain.events import SleipnirEvent

                reply = SleipnirEvent(
                    event_type=reply_topic,
                    source="target_peer",
                    payload={"rpc_response": {"status": "ok"}},
                    summary="reply",
                    urgency=0.5,
                    domain="code",
                    timestamp=datetime.now(UTC),
                    correlation_id=event.correlation_id,
                )
                await transport.publish(reply)

        # Subscribe to handle RPC requests
        await transport.subscribe(["ravn.mesh.rpc.*"], respond)

        result = await adapter.send("any-peer", {"msg": "hello"}, timeout_s=1.0)
        assert result == {"status": "ok"}

    @pytest.mark.asyncio
    async def test_set_rpc_handler(
        self, adapter: SleipnirMeshAdapter, transport: _FakeSleipnirTransport
    ) -> None:
        handler = AsyncMock(return_value={"result": "handled"})
        adapter.set_rpc_handler(handler)

        # Start to register RPC subscription
        await adapter.start()

        # Simulate incoming RPC request (use sanitized peer IDs)
        from sleipnir.domain.events import SleipnirEvent

        request = SleipnirEvent(
            event_type="ravn.mesh.rpc.test_peer",  # sanitized: test-peer -> test_peer
            source="other_peer",
            payload={
                "rpc_request": {"action": "test"},
                "reply_topic": "ravn.mesh.rpc.reply.other_peer.nabc1234",  # prefixed nonce
            },
            summary="rpc request",
            urgency=0.5,
            domain="code",
            timestamp=datetime.now(UTC),
            correlation_id="corr-123",
        )
        await transport.publish(request)

        # Handler should have been called
        handler.assert_called_once_with({"action": "test"})

        await adapter.stop()

    @pytest.mark.asyncio
    async def test_start_and_stop(
        self, adapter: SleipnirMeshAdapter, transport: _FakeSleipnirTransport
    ) -> None:
        await adapter.start()
        # Should have RPC subscription
        rpc_subs = [s for s in transport.subscriptions if "rpc" in s.topic]
        assert len(rpc_subs) == 1

        await adapter.stop()
        # Subscription should be inactive
        assert not rpc_subs[0].active


# ---------------------------------------------------------------------------
# RPC Integration Test
# ---------------------------------------------------------------------------


class TestRpcRoundtrip:
    """Test RPC request/response flow."""

    @pytest.mark.asyncio
    async def test_rpc_roundtrip(self) -> None:
        transport = _FakeSleipnirTransport()

        # Create two adapters sharing transport (simulates two peers)
        peer_a = SleipnirMeshAdapter(
            publisher=transport,
            subscriber=transport,
            own_peer_id="peer-a",
        )
        peer_b = SleipnirMeshAdapter(
            publisher=transport,
            subscriber=transport,
            own_peer_id="peer-b",
        )

        # Peer B handles RPC
        peer_b.set_rpc_handler(AsyncMock(return_value={"echo": "hello from B"}))
        await peer_b.start()

        # Peer A sends RPC to peer B
        result = await peer_a.send("peer-b", {"msg": "hello"}, timeout_s=2.0)
        assert result == {"echo": "hello from B"}

        await peer_b.stop()


# ---------------------------------------------------------------------------
# Config Tests
# ---------------------------------------------------------------------------


class TestMeshConfig:
    def test_defaults(self) -> None:
        cfg = MeshConfig()
        assert cfg.enabled is False
        assert cfg.adapter == ""
        assert cfg.rpc_timeout_s == 10.0

    def test_custom_values(self) -> None:
        cfg = MeshConfig(enabled=True, adapter="rabbitmq", rpc_timeout_s=30.0)
        assert cfg.enabled is True
        assert cfg.adapter == "rabbitmq"
        assert cfg.rpc_timeout_s == 30.0
