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

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from niuu.mesh import mesh_event_prefix
from niuu.mesh.config import DEFAULT_RPC_REPLY_CACHE_SIZE
from ravn.adapters.mesh.sleipnir_mesh import SleipnirMeshAdapter
from ravn.config import MeshConfig
from ravn.domain.events import RavnEvent, RavnEventType
from ravn.ports.mesh import MeshPort, PeerNotFoundError
from sleipnir.domain.events import SleipnirEvent

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


class _FailingSleipnirTransport(_FakeSleipnirTransport):
    """Fake transport that fails publishes for observability tests."""

    async def publish(self, event: Any) -> None:
        raise RuntimeError("broker unavailable")


class _FlakySleipnirPublisher(_FakeSleipnirTransport):
    """Fake publisher whose first *failures* publishes raise, then succeed."""

    def __init__(self, failures: int) -> None:
        super().__init__()
        self._failures = failures

    async def publish(self, event: Any) -> None:
        if self._failures > 0:
            self._failures -= 1
            raise RuntimeError("broker unavailable")
        await super().publish(event)


_RPC_REPLY_TOPIC = "ravn.mesh.rpc.reply.other_peer.nabc1234"


def _rpc_request(correlation_id: str = "corr-rpc", **fields: Any) -> SleipnirEvent:
    """An RPC request for peer ``test-peer`` as it arrives from the transport."""
    return SleipnirEvent(
        event_type="ravn.mesh.rpc.test_peer",
        source="other_peer",
        payload={"rpc_request": {"action": "test"}, "reply_topic": _RPC_REPLY_TOPIC},
        summary="rpc request",
        urgency=0.5,
        domain="code",
        timestamp=datetime.now(UTC),
        correlation_id=correlation_id,
        **fields,
    )


def _redelivered(event: SleipnirEvent) -> SleipnirEvent:
    """The same message delivered again: decoded afresh from its wire form."""
    return SleipnirEvent.from_dict(json.loads(json.dumps(event.to_dict())))


def _replies(transport: _FakeSleipnirTransport) -> list[SleipnirEvent]:
    return [event for event in transport.published if event.event_type == _RPC_REPLY_TOPIC]


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
    async def test_environment_scopes_pubsub_to_one_flock(
        self, transport: _FakeSleipnirTransport
    ) -> None:
        flock_a = SleipnirMeshAdapter(
            publisher=transport,
            subscriber=transport,
            own_peer_id="peer-a",
            environment_id="flock-a",
        )
        flock_b = SleipnirMeshAdapter(
            publisher=transport,
            subscriber=transport,
            own_peer_id="peer-b",
            environment_id="flock-b",
        )
        handler_a = AsyncMock()
        handler_b = AsyncMock()
        await flock_a.subscribe("review.completed", handler_a)
        await flock_b.subscribe("review.completed", handler_b)

        await flock_a.publish(_make_event(), "review.completed")

        handler_a.assert_awaited_once()
        handler_b.assert_not_awaited()
        published = transport.published[0]
        assert published.event_type == "ravn.mesh.realm_flock_a.review.completed"
        assert published.payload["ravn_environment_id"] == "flock-a"

    def test_environment_prefix_sanitizes_for_transport(self) -> None:
        assert mesh_event_prefix(" Flock/A:B ") == "ravn.mesh.realm_flock_a_b"

    @pytest.mark.asyncio
    async def test_publish_failure_logs_environment_context_and_propagates(self, caplog) -> None:
        """An event that never left the peer must not read as published."""
        transport = _FailingSleipnirTransport()
        adapter = SleipnirMeshAdapter(
            publisher=transport,
            subscriber=transport,
            own_peer_id="valkyrie-a",
            environment_id="cluster-a",
        )
        event = RavnEvent(
            type=RavnEventType.DECISION,
            source="valkyrie-a",
            payload={"signal": "pod/restarted"},
            timestamp=datetime.now(UTC),
            urgency=0.8,
            correlation_id="corr-env",
            session_id="session-env",
            root_correlation_id="root-env",
        )

        with (
            caplog.at_level(logging.WARNING, logger="ravn.adapters.mesh.sleipnir_mesh"),
            pytest.raises(RuntimeError, match="broker unavailable"),
        ):
            await adapter.publish(event, "signal.kubernetes.pod")

        assert "publish failed" in caplog.text
        assert "peer=valkyrie-a" in caplog.text
        assert "environment=cluster-a" in caplog.text
        assert "topic=signal.kubernetes.pod" in caplog.text
        assert "correlation_id=corr-env" in caplog.text
        assert "root_correlation_id=root-env" in caplog.text

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
        object.__setattr__(
            event,
            "trace_context",
            {"traceparent": "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01"},
        )
        await adapter.publish(event, "test.topic")

        assert len(received_events) == 1
        assert received_events[0].type == RavnEventType.THOUGHT
        assert received_events[0].trace_context == event.trace_context

    @pytest.mark.asyncio
    async def test_handler_failure_logs_environment_context_and_propagates(
        self, transport: _FakeSleipnirTransport, caplog
    ) -> None:
        """The failure reaches the Sleipnir transport so it can nak and redeliver."""
        adapter = SleipnirMeshAdapter(
            publisher=transport,
            subscriber=transport,
            own_peer_id="valkyrie-b",
            environment_id="cluster-b",
        )

        async def failing_handler(event: RavnEvent) -> None:
            raise ValueError("bad handler")

        await adapter.subscribe("signal.kubernetes.pod", failing_handler)
        event = RavnEvent(
            type=RavnEventType.DECISION,
            source="valkyrie-a",
            payload={"signal": "pod/restarted"},
            timestamp=datetime.now(UTC),
            urgency=0.8,
            correlation_id="corr-handler",
            session_id="session-env",
            root_correlation_id="root-handler",
        )

        # Capture the wire event without the fake's inline dispatch, then hand it
        # to the subscribed handler the way a Sleipnir transport would.
        subscription = transport.subscriptions[0]
        subscription.active = False
        await adapter.publish(event, "signal.kubernetes.pod")

        with (
            caplog.at_level(logging.WARNING, logger="ravn.adapters.mesh.sleipnir_mesh"),
            pytest.raises(ValueError, match="bad handler"),
        ):
            await subscription.handler(transport.published[0])

        assert "handler failed" in caplog.text
        assert "peer=valkyrie-b" in caplog.text
        assert "environment=cluster-b" in caplog.text
        assert "topic=signal.kubernetes.pod" in caplog.text
        assert "correlation_id=corr-handler" in caplog.text
        assert "root_correlation_id=root-handler" in caplog.text

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
    async def test_rpc_reply_failure_propagates_to_transport(self, caplog) -> None:
        """An unsent reply leaves the request unhandled, so the transport can redeliver it."""
        subscriber = _FakeSleipnirTransport()
        adapter = SleipnirMeshAdapter(
            publisher=_FailingSleipnirTransport(),
            subscriber=subscriber,
            own_peer_id="test-peer",
        )
        handler = AsyncMock(return_value={"result": "handled"})
        adapter.set_rpc_handler(handler)
        await adapter.start()

        from sleipnir.domain.events import SleipnirEvent

        request = SleipnirEvent(
            event_type="ravn.mesh.rpc.test_peer",
            source="other_peer",
            payload={
                "rpc_request": {"action": "test"},
                "reply_topic": "ravn.mesh.rpc.reply.other_peer.nabc1234",
            },
            summary="rpc request",
            urgency=0.5,
            domain="code",
            timestamp=datetime.now(UTC),
            correlation_id="corr-reply",
        )
        rpc_subscription = subscriber.subscriptions[0]

        with (
            caplog.at_level(logging.WARNING, logger="ravn.adapters.mesh.sleipnir_mesh"),
            pytest.raises(RuntimeError, match="broker unavailable"),
        ):
            await rpc_subscription.handler(request)

        handler.assert_awaited_once_with({"action": "test"})
        assert "failed to send RPC reply" in caplog.text
        assert "reply_topic=ravn.mesh.rpc.reply.other_peer.nabc1234" in caplog.text
        assert "correlation_id=corr-reply" in caplog.text

        await adapter.stop()

    @pytest.mark.asyncio
    async def test_send_propagates_request_publish_failure(self) -> None:
        subscriber = _FakeSleipnirTransport()
        adapter = SleipnirMeshAdapter(
            publisher=_FailingSleipnirTransport(),
            subscriber=subscriber,
            own_peer_id="test-peer",
        )

        with pytest.raises(RuntimeError, match="broker unavailable"):
            await adapter.send("other-peer", {"msg": "hello"}, timeout_s=1.0)

        reply_subscription = subscriber.subscriptions[0]
        assert "rpc.reply" in reply_subscription.topic
        assert not reply_subscription.active
        assert adapter._pending_rpc == {}

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

    @pytest.mark.asyncio
    async def test_rpc_is_scoped_to_one_flock(self) -> None:
        transport = _FakeSleipnirTransport()
        requester = SleipnirMeshAdapter(
            publisher=transport,
            subscriber=transport,
            own_peer_id="peer-a",
            environment_id="flock-a",
        )
        target_a = SleipnirMeshAdapter(
            publisher=transport,
            subscriber=transport,
            own_peer_id="peer-b",
            environment_id="flock-a",
        )
        target_b = SleipnirMeshAdapter(
            publisher=transport,
            subscriber=transport,
            own_peer_id="peer-b",
            environment_id="flock-b",
        )
        handler_a = AsyncMock(return_value={"flock": "a"})
        handler_b = AsyncMock(return_value={"flock": "b"})
        target_a.set_rpc_handler(handler_a)
        target_b.set_rpc_handler(handler_b)
        await target_a.start()
        await target_b.start()

        result = await requester.send("peer-b", {"msg": "hello"}, timeout_s=1.0)

        assert result == {"flock": "a"}
        handler_a.assert_awaited_once_with({"msg": "hello"})
        handler_b.assert_not_awaited()
        await target_a.stop()
        await target_b.stop()


# ---------------------------------------------------------------------------
# RPC redelivery (at-least-once Sleipnir delivery)
# ---------------------------------------------------------------------------


class TestRpcRedelivery:
    """A redelivered RPC request re-publishes its stored reply; the handler runs once.

    The fake transport dispatches inline, so each test hands a request straight
    to the RPC subscription's handler, the way a Sleipnir transport delivers it
    again after a nak or a lost ack.
    """

    @staticmethod
    async def _started_peer(
        publisher: _FakeSleipnirTransport,
        subscriber: _FakeSleipnirTransport,
        handler: AsyncMock | None,
        **kwargs: Any,
    ) -> SleipnirMeshAdapter:
        adapter = SleipnirMeshAdapter(
            publisher=publisher,
            subscriber=subscriber,
            own_peer_id="test-peer",
            **kwargs,
        )
        if handler is not None:
            adapter.set_rpc_handler(handler)
        await adapter.start()
        return adapter

    @pytest.mark.asyncio
    async def test_redelivery_after_answered_roundtrip_does_not_rerun_handler(self) -> None:
        """A lost ack redelivers a request whose reply already went out."""
        transport = _FakeSleipnirTransport()
        requester = SleipnirMeshAdapter(
            publisher=transport, subscriber=transport, own_peer_id="peer-a"
        )
        handler = AsyncMock(return_value={"status": "complete", "output": "done"})
        target = SleipnirMeshAdapter(
            publisher=transport, subscriber=transport, own_peer_id="peer-b"
        )
        target.set_rpc_handler(handler)
        await target.start()

        result = await requester.send("peer-b", {"type": "work_request"}, timeout_s=1.0)
        assert result == {"status": "complete", "output": "done"}
        request = next(e for e in transport.published if "rpc_request" in e.payload)
        target_rpc_subscription = next(
            s for s in transport.subscriptions if s.topic == "ravn.mesh.rpc.peer_b"
        )

        await target_rpc_subscription.handler(_redelivered(request))

        handler.assert_awaited_once_with({"type": "work_request"})
        replies = [e for e in transport.published if e.event_type == request.payload["reply_topic"]]
        assert [reply.payload for reply in replies] == [
            {"rpc_response": {"status": "complete", "output": "done"}}
        ] * 2
        assert {reply.correlation_id for reply in replies} == {request.correlation_id}
        await target.stop()

    @pytest.mark.asyncio
    async def test_redelivery_after_failed_reply_publishes_stored_reply(self, caplog) -> None:
        """The reply publish fails, the request is nak'd, and its redelivery only replies."""
        subscriber = _FakeSleipnirTransport()
        publisher = _FlakySleipnirPublisher(failures=1)
        handler = AsyncMock(return_value={"status": "complete", "output": "turn ran once"})
        adapter = await self._started_peer(publisher, subscriber, handler)
        request = _rpc_request()
        rpc_subscription = subscriber.subscriptions[0]

        with pytest.raises(RuntimeError, match="broker unavailable"):
            await rpc_subscription.handler(request)
        assert _replies(publisher) == []

        with caplog.at_level(logging.INFO, logger="ravn.adapters.mesh.sleipnir_mesh"):
            await rpc_subscription.handler(_redelivered(request))

        handler.assert_awaited_once_with({"action": "test"})
        replies = _replies(publisher)
        assert [reply.payload for reply in replies] == [
            {"rpc_response": {"status": "complete", "output": "turn ran once"}}
        ]
        assert replies[0].correlation_id == "corr-rpc"
        assert "re-publishing its stored reply" in caplog.text
        assert f"event_id={request.event_id}" in caplog.text
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_every_redelivery_until_the_reply_is_out_reuses_the_reply(self) -> None:
        """Several failed reply publishes in a row still run the handler only once."""
        subscriber = _FakeSleipnirTransport()
        publisher = _FlakySleipnirPublisher(failures=3)
        handler = AsyncMock(return_value={"status": "accepted"})
        adapter = await self._started_peer(publisher, subscriber, handler)
        request = _rpc_request()
        rpc_subscription = subscriber.subscriptions[0]

        for _ in range(3):
            with pytest.raises(RuntimeError, match="broker unavailable"):
                await rpc_subscription.handler(_redelivered(request))
        await rpc_subscription.handler(_redelivered(request))

        handler.assert_awaited_once()
        assert [reply.payload for reply in _replies(publisher)] == [
            {"rpc_response": {"status": "accepted"}}
        ]
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_handler_error_reply_is_stored_and_not_rerun(self) -> None:
        """A handler that raised has completed; its redelivery re-sends the error reply."""
        transport = _FakeSleipnirTransport()
        handler = AsyncMock(side_effect=RuntimeError("drive loop refused the task"))
        adapter = await self._started_peer(transport, transport, handler)
        request = _rpc_request()
        rpc_subscription = transport.subscriptions[0]

        await rpc_subscription.handler(request)
        await rpc_subscription.handler(_redelivered(request))

        handler.assert_awaited_once()
        assert [reply.payload for reply in _replies(transport)] == [
            {"rpc_response": {"error": "drive loop refused the task"}}
        ] * 2
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_distinct_request_with_same_body_runs_handler_again(self) -> None:
        """Only the event_id identifies a redelivery, never the request body."""
        transport = _FakeSleipnirTransport()
        handler = AsyncMock(side_effect=[{"run": 1}, {"run": 2}])
        adapter = await self._started_peer(transport, transport, handler)
        rpc_subscription = transport.subscriptions[0]

        await rpc_subscription.handler(_rpc_request(correlation_id="corr-first"))
        await rpc_subscription.handler(_rpc_request(correlation_id="corr-second"))

        assert handler.await_count == 2
        assert [reply.payload for reply in _replies(transport)] == [
            {"rpc_response": {"run": 1}},
            {"rpc_response": {"run": 2}},
        ]
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_reply_store_keeps_the_most_recently_used_replies(self) -> None:
        """The configured cap bounds the store; the least recently used reply goes first."""
        transport = _FakeSleipnirTransport()
        handler = AsyncMock(side_effect=lambda request: {"handled": request["action"]})
        adapter = await self._started_peer(transport, transport, handler, rpc_reply_cache_size=2)
        rpc_subscription = transport.subscriptions[0]
        first, second, third = (_rpc_request(correlation_id=f"corr-{n}") for n in range(3))

        await rpc_subscription.handler(first)
        await rpc_subscription.handler(second)
        await rpc_subscription.handler(_redelivered(first))  # hit: first is now the newest
        await rpc_subscription.handler(third)  # evicts second
        assert handler.await_count == 3

        await rpc_subscription.handler(_redelivered(first))
        await rpc_subscription.handler(_redelivered(third))
        assert handler.await_count == 3

        await rpc_subscription.handler(_redelivered(second))
        assert handler.await_count == 4
        assert len(adapter._rpc_replies) == 2
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_interrupted_handler_runs_again_on_redelivery(self) -> None:
        """A handler cancelled mid-run (stop, unsubscribe) never completed: nothing is stored."""
        transport = _FakeSleipnirTransport()
        handler = AsyncMock(side_effect=[asyncio.CancelledError(), {"status": "complete"}])
        adapter = await self._started_peer(transport, transport, handler)
        request = _rpc_request()
        rpc_subscription = transport.subscriptions[0]

        with pytest.raises(asyncio.CancelledError):
            await rpc_subscription.handler(request)
        assert _replies(transport) == []

        await rpc_subscription.handler(_redelivered(request))

        assert handler.await_count == 2
        assert [reply.payload for reply in _replies(transport)] == [
            {"rpc_response": {"status": "complete"}}
        ]
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_reply_without_a_handler_is_not_stored(self) -> None:
        """No handler ran, so a redelivery after set_rpc_handler() reaches the handler."""
        subscriber = _FakeSleipnirTransport()
        publisher = _FlakySleipnirPublisher(failures=1)
        adapter = await self._started_peer(publisher, subscriber, handler=None)
        request = _rpc_request()
        rpc_subscription = subscriber.subscriptions[0]

        with pytest.raises(RuntimeError, match="broker unavailable"):
            await rpc_subscription.handler(request)
        handler = AsyncMock(return_value={"status": "complete"})
        adapter.set_rpc_handler(handler)
        await rpc_subscription.handler(_redelivered(request))

        handler.assert_awaited_once_with({"action": "test"})
        assert [reply.payload for reply in _replies(publisher)] == [
            {"rpc_response": {"status": "complete"}}
        ]
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_stored_replies_survive_stop_and_start(self) -> None:
        """A durable consumer can redeliver an earlier request to the new subscription."""
        transport = _FakeSleipnirTransport()
        handler = AsyncMock(return_value={"status": "complete"})
        adapter = await self._started_peer(transport, transport, handler)
        request = _rpc_request()
        await transport.subscriptions[0].handler(request)
        await adapter.stop()

        await adapter.start()
        restarted_rpc_subscription = transport.subscriptions[-1]
        assert restarted_rpc_subscription.active
        await restarted_rpc_subscription.handler(_redelivered(request))

        handler.assert_awaited_once()
        assert len(_replies(transport)) == 2
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_request_without_event_id_is_rejected(self) -> None:
        """An empty key would hand one requester another request's stored reply."""
        transport = _FakeSleipnirTransport()
        handler = AsyncMock(return_value={"status": "complete"})
        adapter = await self._started_peer(transport, transport, handler)

        with pytest.raises(ValueError, match="without an event_id"):
            await transport.subscriptions[0].handler(_rpc_request(event_id=""))

        handler.assert_not_awaited()
        assert _replies(transport) == []
        await adapter.stop()

    @pytest.mark.parametrize("size", [0, -1])
    def test_reply_cache_size_must_be_positive(self, size: int) -> None:
        transport = _FakeSleipnirTransport()
        with pytest.raises(ValueError, match="rpc_reply_cache_size must be >= 1"):
            SleipnirMeshAdapter(
                publisher=transport,
                subscriber=transport,
                own_peer_id="test-peer",
                rpc_reply_cache_size=size,
            )


# ---------------------------------------------------------------------------
# Config Tests
# ---------------------------------------------------------------------------


class TestMeshConfig:
    def test_defaults(self) -> None:
        cfg = MeshConfig()
        assert cfg.enabled is False
        assert cfg.adapter == ""
        assert cfg.rpc_timeout_s == 10.0
        assert cfg.rpc_reply_cache_size == DEFAULT_RPC_REPLY_CACHE_SIZE

    def test_custom_values(self) -> None:
        cfg = MeshConfig(
            enabled=True, adapter="rabbitmq", rpc_timeout_s=30.0, rpc_reply_cache_size=64
        )
        assert cfg.enabled is True
        assert cfg.adapter == "rabbitmq"
        assert cfg.rpc_timeout_s == 30.0
        assert cfg.rpc_reply_cache_size == 64

    def test_rpc_reply_cache_size_must_be_positive(self) -> None:
        with pytest.raises(ValidationError, match="rpc_reply_cache_size"):
            MeshConfig(rpc_reply_cache_size=0)
