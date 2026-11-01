"""Tests for the gateway orchestrator (RavnGateway + GatewayChannel)."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from ravn.adapters.channels.gateway import (
    AgentFactory,
    GatewayChannel,
    GatewaySession,
    RavnGateway,
)
from ravn.config import GatewayConfig
from ravn.domain.events import RavnEvent, RavnEventType
from ravn.ports.channel import ChannelPort

_SRC = "ravn-test"
_CID = "corr-1"
_SID = "sess-1"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config() -> GatewayConfig:
    return GatewayConfig()


def _make_agent_factory(response: str = "Hello from agent") -> AgentFactory:
    """Returns a factory that creates mock agents emitting a RESPONSE event."""

    def factory(channel: ChannelPort) -> MagicMock:
        agent = MagicMock()

        async def run_turn(text: str):
            await channel.emit(RavnEvent.response(_SRC, response, _CID, _SID))

        agent.run_turn = run_turn
        return agent

    return factory


def _make_agent_factory_with_thought(thought: str, response: str) -> AgentFactory:
    """Factory producing THOUGHT then RESPONSE events."""

    def factory(channel: ChannelPort) -> MagicMock:
        agent = MagicMock()

        async def run_turn(text: str):
            await channel.emit(RavnEvent.thought(_SRC, thought, _CID, _SID))
            await channel.emit(RavnEvent.response(_SRC, response, _CID, _SID))

        agent.run_turn = run_turn
        return agent

    return factory


def _make_agent_factory_error(error_msg: str) -> AgentFactory:
    """Factory producing an ERROR event."""

    def factory(channel: ChannelPort) -> MagicMock:
        agent = MagicMock()

        async def run_turn(text: str):
            await channel.emit(RavnEvent.error(_SRC, error_msg, _CID, _SID))

        agent.run_turn = run_turn
        return agent

    return factory


# ---------------------------------------------------------------------------
# GatewayChannel tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gateway_channel_emit_and_collect_response():
    ch = GatewayChannel()
    await ch.emit(RavnEvent.response(_SRC, "Hello!", _CID, _SID))

    result = await ch.collect_response()
    assert result == "Hello!"


@pytest.mark.asyncio
async def test_gateway_channel_collect_response_on_error():
    ch = GatewayChannel()
    await ch.emit(RavnEvent.error(_SRC, "boom", _CID, _SID))

    result = await ch.collect_response()
    assert result == "[error] boom"


@pytest.mark.asyncio
async def test_gateway_channel_collect_response_sentinel():
    ch = GatewayChannel()
    await ch.signal_done()

    result = await ch.collect_response()
    assert result == ""


@pytest.mark.asyncio
async def test_gateway_channel_collect_response_skips_thought():
    """THOUGHT events are consumed but the RESPONSE data is used as the final answer."""
    ch = GatewayChannel()
    await ch.emit(RavnEvent.thought(_SRC, "thinking...", _CID, _SID))
    await ch.emit(RavnEvent.response(_SRC, "Final answer", _CID, _SID))

    result = await ch.collect_response()
    assert result == "Final answer"


@pytest.mark.asyncio
async def test_gateway_channel_stream_yields_events_until_response():
    ch = GatewayChannel()
    await ch.emit(RavnEvent.thought(_SRC, "step 1", _CID, _SID))
    await ch.emit(RavnEvent.thought(_SRC, "step 2", _CID, _SID))
    await ch.emit(RavnEvent.response(_SRC, "Done", _CID, _SID))

    events: list[RavnEvent] = []
    async for event in ch.stream():
        events.append(event)

    assert len(events) == 3
    assert events[0].type == RavnEventType.THOUGHT
    assert events[1].type == RavnEventType.THOUGHT
    assert events[2].type == RavnEventType.RESPONSE


@pytest.mark.asyncio
async def test_gateway_channel_stream_stops_on_error():
    ch = GatewayChannel()
    await ch.emit(RavnEvent.error(_SRC, "oops", _CID, _SID))
    await ch.emit(RavnEvent.thought(_SRC, "should not appear", _CID, _SID))

    events: list[RavnEvent] = []
    async for event in ch.stream():
        events.append(event)

    assert len(events) == 1
    assert events[0].type == RavnEventType.ERROR


@pytest.mark.asyncio
async def test_gateway_channel_stream_stops_on_sentinel():
    ch = GatewayChannel()
    await ch.emit(RavnEvent.thought(_SRC, "t", _CID, _SID))
    await ch.signal_done()

    events: list[RavnEvent] = []
    async for event in ch.stream():
        events.append(event)

    assert len(events) == 1


@pytest.mark.asyncio
async def test_gateway_channel_broadcast_callback_called():
    received: list[RavnEvent] = []

    async def cb(event: RavnEvent) -> None:
        received.append(event)

    ch = GatewayChannel(broadcast_cb=cb)
    evt = RavnEvent.response(_SRC, "hi", _CID, _SID)
    await ch.emit(evt)

    assert received == [evt]


@pytest.mark.asyncio
async def test_gateway_channel_no_broadcast_callback():
    """Channel without callback must not raise."""
    ch = GatewayChannel()
    await ch.emit(RavnEvent.response(_SRC, "ok", _CID, _SID))
    result = await ch.collect_response()
    assert result == "ok"


# ---------------------------------------------------------------------------
# RavnGateway.get_or_create_session
# ---------------------------------------------------------------------------


def test_gateway_creates_new_session():
    gw = RavnGateway(_make_config(), _make_agent_factory())
    s1 = gw.get_or_create_session("telegram:1")
    assert isinstance(s1, GatewaySession)
    assert "telegram:1" in gw.session_ids()


def test_gateway_returns_same_session_on_second_call():
    gw = RavnGateway(_make_config(), _make_agent_factory())
    s1 = gw.get_or_create_session("telegram:1")
    s2 = gw.get_or_create_session("telegram:1")
    assert s1 is s2


def test_gateway_separate_sessions_for_different_ids():
    gw = RavnGateway(_make_config(), _make_agent_factory())
    s1 = gw.get_or_create_session("telegram:1")
    s2 = gw.get_or_create_session("telegram:2")
    assert s1 is not s2
    assert len(gw.session_ids()) == 2


# ---------------------------------------------------------------------------
# RavnGateway.handle_message
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gateway_handle_message_returns_response():
    gw = RavnGateway(_make_config(), _make_agent_factory("Hello!"))
    result = await gw.handle_message("s1", "hi")
    assert result == "Hello!"


@pytest.mark.asyncio
async def test_gateway_handle_message_with_thought_and_response():
    gw = RavnGateway(
        _make_config(),
        _make_agent_factory_with_thought("thinking", "Done"),
    )
    result = await gw.handle_message("s1", "go")
    assert result == "Done"


@pytest.mark.asyncio
async def test_gateway_handle_message_error_response():
    gw = RavnGateway(_make_config(), _make_agent_factory_error("fail"))
    result = await gw.handle_message("s1", "hi")
    assert result == "[error] fail"


@pytest.mark.asyncio
async def test_gateway_handle_message_serialises_concurrent_calls():
    """Two concurrent calls to the same session should not interleave."""
    order: list[str] = []

    def factory(channel: ChannelPort) -> MagicMock:
        agent = MagicMock()

        async def run_turn(text: str):
            order.append(f"start:{text}")
            await asyncio.sleep(0)
            await channel.emit(RavnEvent.response(_SRC, f"resp:{text}", _CID, _SID))
            order.append(f"end:{text}")

        agent.run_turn = run_turn
        return agent

    gw = RavnGateway(_make_config(), factory)
    await asyncio.gather(
        gw.handle_message("s1", "a"),
        gw.handle_message("s1", "b"),
    )
    # Turns must not interleave — each must fully complete before the next.
    a_done_before_b = order.index("end:a") < order.index("start:b")
    b_done_before_a = order.index("end:b") < order.index("start:a")
    assert a_done_before_b or b_done_before_a


# ---------------------------------------------------------------------------
# RavnGateway.handle_message_stream
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gateway_handle_message_stream_yields_events():
    gw = RavnGateway(
        _make_config(),
        _make_agent_factory_with_thought("t", "r"),
    )
    events: list[RavnEvent] = []
    async for event in gw.handle_message_stream("s1", "go"):
        events.append(event)

    types = [e.type for e in events]
    assert RavnEventType.THOUGHT in types
    assert RavnEventType.RESPONSE in types


# ---------------------------------------------------------------------------
# RavnGateway subscribe / unsubscribe / broadcast
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gateway_broadcast_reaches_subscriber():
    gw = RavnGateway(_make_config(), _make_agent_factory("hi"))
    q = gw.subscribe()

    await gw.handle_message("s1", "hello")

    # At least one event should have been broadcast (the RESPONSE event).
    assert not q.empty()
    event = q.get_nowait()
    assert event is not None


@pytest.mark.asyncio
async def test_gateway_unsubscribe_removes_queue():
    gw = RavnGateway(_make_config(), _make_agent_factory("hi"))
    q = gw.subscribe()
    gw.unsubscribe(q)

    await gw.handle_message("s1", "hello")
    assert q.empty()


def test_gateway_unsubscribe_unknown_queue_is_safe():
    gw = RavnGateway(_make_config(), _make_agent_factory())
    q: asyncio.Queue = asyncio.Queue()
    # Should not raise even if q was never subscribed.
    gw.unsubscribe(q)
