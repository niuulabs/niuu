"""Tests for skuld.sleipnir_bridge.SleipnirBridge."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from skuld.channels import ChannelRegistry
from skuld.sleipnir_bridge import DEFAULT_PATTERNS, SleipnirBridge
from sleipnir.adapters.in_process import InProcessBus
from tests.test_sleipnir.conftest import make_event

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ws_channel():
    ch = MagicMock()
    ch.channel_type = "websocket"
    ch.is_open = True
    ch.send_event = AsyncMock()
    return ch


def _make_registry() -> ChannelRegistry:
    registry = ChannelRegistry()
    return registry


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class TestSleipnirBridgeLifecycle:
    async def test_start_subscribes_to_bus(self):
        bus = InProcessBus()
        registry = _make_registry()
        bridge = SleipnirBridge(bus, registry)

        await bridge.start()
        assert bridge._subscription is not None
        await bridge.stop()

    async def test_stop_unsubscribes(self):
        bus = InProcessBus()
        registry = _make_registry()
        bridge = SleipnirBridge(bus, registry)

        await bridge.start()
        await bridge.stop()
        assert bridge._subscription is None

    async def test_double_start_is_idempotent(self):
        bus = InProcessBus()
        registry = _make_registry()
        bridge = SleipnirBridge(bus, registry)

        await bridge.start()
        first_sub = bridge._subscription
        await bridge.start()  # second call should be ignored
        assert bridge._subscription is first_sub
        await bridge.stop()

    async def test_stop_without_start_is_safe(self):
        bus = InProcessBus()
        registry = _make_registry()
        bridge = SleipnirBridge(bus, registry)
        await bridge.stop()  # must not raise

    async def test_context_manager(self):
        bus = InProcessBus()
        registry = _make_registry()

        async with SleipnirBridge(bus, registry) as bridge:
            assert bridge._subscription is not None

        assert bridge._subscription is None


# ---------------------------------------------------------------------------
# Event forwarding — no session filter
# ---------------------------------------------------------------------------


class TestSleipnirBridgeForwarding:
    async def test_event_forwarded_to_channel(self):
        bus = InProcessBus()
        registry = _make_registry()
        ch = _make_ws_channel()
        registry.add(ch)

        async with SleipnirBridge(bus, registry):
            await bus.publish(make_event(event_type="ravn.tool.complete"))
            await bus.flush()

        ch.send_event.assert_awaited_once()
        call_args = ch.send_event.call_args[0][0]
        assert call_args["event_type"] == "ravn.tool.complete"
        assert call_args["type"] == "sleipnir"

    async def test_wire_format_contains_required_fields(self):
        bus = InProcessBus()
        registry = _make_registry()
        ch = _make_ws_channel()
        registry.add(ch)

        async with SleipnirBridge(bus, registry):
            await bus.publish(
                make_event(
                    event_type="volundr.session.started",
                    event_id="evt-999",
                    summary="test summary",
                )
            )
            await bus.flush()

        wire = ch.send_event.call_args[0][0]
        assert "event_id" in wire
        assert "event_type" in wire
        assert "source" in wire
        assert "summary" in wire
        assert "payload" in wire
        assert "timestamp" in wire

    async def test_multiple_channels_receive_event(self):
        bus = InProcessBus()
        registry = _make_registry()
        ch1, ch2 = _make_ws_channel(), _make_ws_channel()
        registry.add(ch1)
        registry.add(ch2)

        async with SleipnirBridge(bus, registry):
            await bus.publish(make_event(event_type="ting.saga.created"))
            await bus.flush()

        ch1.send_event.assert_awaited_once()
        ch2.send_event.assert_awaited_once()


# ---------------------------------------------------------------------------
# Session context filtering
# ---------------------------------------------------------------------------


class TestSleipnirBridgeSessionFilter:
    async def _bridge_with_session(self, bus, registry, session_id):
        return SleipnirBridge(bus, registry, session_id=session_id)

    async def test_matching_correlation_id_passes_filter(self):
        bus = InProcessBus()
        registry = _make_registry()
        ch = _make_ws_channel()
        registry.add(ch)

        bridge = SleipnirBridge(bus, registry, session_id="session-abc")
        async with bridge:
            await bus.publish(
                make_event(event_type="ravn.tool.complete", correlation_id="session-abc")
            )
            await bus.flush()

        ch.send_event.assert_awaited_once()

    async def test_matching_payload_session_id_passes_filter(self):
        bus = InProcessBus()
        registry = _make_registry()
        ch = _make_ws_channel()
        registry.add(ch)

        bridge = SleipnirBridge(bus, registry, session_id="session-xyz")
        async with bridge:
            await bus.publish(
                make_event(
                    event_type="volundr.session.started",
                    payload={"session_id": "session-xyz"},
                )
            )
            await bus.flush()

        ch.send_event.assert_awaited_once()

    async def test_non_matching_session_id_dropped(self):
        bus = InProcessBus()
        registry = _make_registry()
        ch = _make_ws_channel()
        registry.add(ch)

        bridge = SleipnirBridge(bus, registry, session_id="session-A")
        async with bridge:
            await bus.publish(
                make_event(
                    event_type="ravn.tool.complete",
                    correlation_id="session-B",
                    payload={"session_id": "session-B"},
                )
            )
            await bus.flush()

        ch.send_event.assert_not_awaited()

    async def test_no_filter_passes_all_events(self):
        bus = InProcessBus()
        registry = _make_registry()
        ch = _make_ws_channel()
        registry.add(ch)

        bridge = SleipnirBridge(bus, registry, session_id=None)
        async with bridge:
            await bus.publish(make_event(event_type="ravn.tool.complete", correlation_id="any"))
            await bus.flush()

        ch.send_event.assert_awaited_once()


# ---------------------------------------------------------------------------
# Custom patterns
# ---------------------------------------------------------------------------


class TestSleipnirBridgePatterns:
    async def test_default_patterns_include_ravn_and_volundr(self):
        assert any("ravn" in p for p in DEFAULT_PATTERNS)
        assert any("volundr" in p for p in DEFAULT_PATTERNS)

    async def test_custom_patterns_only_receive_matching_events(self):
        bus = InProcessBus()
        registry = _make_registry()
        ch = _make_ws_channel()
        registry.add(ch)

        bridge = SleipnirBridge(bus, registry, event_patterns=["ting.*"])
        async with bridge:
            await bus.publish(make_event(event_type="ravn.tool.complete"))  # not matched
            await bus.publish(make_event(event_type="ting.saga.created"))  # matched
            await bus.flush()

        assert ch.send_event.await_count == 1
        wire = ch.send_event.call_args[0][0]
        assert wire["event_type"] == "ting.saga.created"


# ---------------------------------------------------------------------------
# Fault tolerance
# ---------------------------------------------------------------------------


class TestSleipnirBridgeFaultTolerance:
    async def test_channel_error_does_not_crash_bridge(self):
        bus = InProcessBus()
        registry = _make_registry()
        ch = _make_ws_channel()
        ch.send_event.side_effect = RuntimeError("channel broken")
        registry.add(ch)

        bridge = SleipnirBridge(bus, registry)
        async with bridge:
            # Must not propagate the exception
            await bus.publish(make_event(event_type="ravn.tool.complete"))
            await bus.flush()
