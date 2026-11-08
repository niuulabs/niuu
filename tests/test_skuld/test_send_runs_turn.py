"""A transport whose ``send_message`` returns only when the turn is over is not timed out.

The SDK transport (and the one-shot and persistent subprocess transports) run the whole turn
inside ``send_message``, under the send lock. The broker used to cut every such send at
``delivery.attempt_timeout_seconds`` (10 s) and resend the message, so a turn that took longer
than that was cancelled and run again. Peer messages and workflow steps are handed over on this
path. What bounds the turn is the transport's own turn timeout, not the delivery attempt.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from claude_agent_sdk.types import AssistantMessage, ResultMessage, TextBlock

from niuu.ports.cli.transport import TransportCapabilities
from skuld.broker import Broker
from skuld.config import SkuldSettings
from skuld.transports.persistent_subprocess import PersistentSubprocessTransport
from skuld.transports.sdk import SDKTransport
from skuld.transports.subprocess import SubprocessTransport

#: Far longer than the delivery attempt timeout below, far shorter than a real turn.
TURN_SECONDS = 0.3
ATTEMPT_TIMEOUT_SECONDS = 0.05


class _SlowClient:
    """A Claude SDK client whose one turn takes TURN_SECONDS."""

    def __init__(self) -> None:
        self.query = AsyncMock()
        self.interrupt = AsyncMock()
        self.get_mcp_status = AsyncMock(return_value={"mcpServers": []})
        self.turns_started = 0
        self.turns_finished = 0

    async def __aenter__(self) -> _SlowClient:
        return self

    async def __aexit__(self, *_exc) -> bool:
        return False

    async def receive_response(self):
        self.turns_started += 1
        await asyncio.sleep(TURN_SECONDS)
        self.turns_finished += 1
        yield AssistantMessage(
            content=[TextBlock(text="done")],
            model="claude-opus-4-20250514",
            usage={"input_tokens": 1, "output_tokens": 1},
            session_id="sdk-session",
        )
        yield ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id="sdk-session",
            total_cost_usd=0.0,
            usage={"input_tokens": 1, "output_tokens": 1},
            result="done",
        )


def _broker(tmp_path, transport) -> Broker:
    broker = Broker(
        SkuldSettings(
            session={"id": "turn-session", "workspace_dir": str(tmp_path)},
            volundr_api_url="http://forge.test",
            transport="subprocess",
            chronicle_watcher_enabled=False,
            delivery={
                "max_attempts": 2,
                "attempt_timeout_seconds": ATTEMPT_TIMEOUT_SECONDS,
                "initial_backoff_seconds": 0.001,
            },
        )
    )
    broker._transport = transport
    broker._apply_retrieval_reflex = AsyncMock(side_effect=lambda message: message)
    for method in ("_complete_trace_span", "_start_trace_span"):
        setattr(broker, method, AsyncMock())
    broker._http_client = SimpleNamespace(post=AsyncMock())
    broker._http_client_jwt = None
    return broker


async def _deliver(broker: Broker, text: str) -> list[dict]:
    task = await broker._submit_user_turn(text, msg_id="turn-1", request_id=None, source="test")
    await task
    return [
        e["payload"]
        for e in broker._event_log_buffer
        if e["kind"] in ("user_delivered", "user_delivery_failed")
    ]


async def test_an_sdk_turn_longer_than_the_attempt_timeout_runs_once_and_is_delivered(
    monkeypatch, tmp_path
) -> None:
    client = _SlowClient()
    monkeypatch.setattr("skuld.transports.sdk.ClaudeSDKClient", lambda _options: client)
    transport = SDKTransport(workspace_dir=str(tmp_path))
    await transport.start()
    broker = _broker(tmp_path, transport)

    acks = await _deliver(broker, "hello")

    assert [a["type"] for a in acks] == ["user_delivered"]
    assert (client.turns_started, client.turns_finished) == (1, 1)
    client.query.assert_awaited_once_with("hello")
    assert broker._conversation_turns[0].metadata["steering_state"] != "failed"
    await transport.stop()


async def test_the_transports_own_turn_timeout_still_bounds_the_turn(monkeypatch, tmp_path) -> None:
    client = _SlowClient()
    monkeypatch.setattr("skuld.transports.sdk.ClaudeSDKClient", lambda _options: client)
    transport = SDKTransport(workspace_dir=str(tmp_path), turn_timeout_s=0.1)
    await transport.start()
    broker = _broker(tmp_path, transport)

    acks = await _deliver(broker, "hello")

    # The turn timeout interrupts the turn and recovers once; the delivery attempt timeout
    # (0.05 s) did not cut it first, so the SDK transport's own bound is what ran.
    assert client.interrupt.await_count >= 1
    assert acks[-1]["type"] in ("user_delivered", "user_delivery_failed")
    await transport.stop()


@pytest.mark.parametrize("transport_class", [SDKTransport, SubprocessTransport])
def test_transports_that_run_the_turn_inside_send_message_say_so(transport_class, tmp_path) -> None:
    assert transport_class(workspace_dir=str(tmp_path)).capabilities.send_runs_turn is True


def test_the_persistent_subprocess_transport_says_so_too(tmp_path) -> None:
    transport = PersistentSubprocessTransport(workspace_dir=str(tmp_path))

    assert transport.capabilities.send_runs_turn is True


async def test_a_transport_that_accepts_input_quickly_is_still_bounded(tmp_path) -> None:
    transport = MagicMock()
    transport.is_alive = True
    transport.capabilities = TransportCapabilities()

    async def wedged(*_args, **_kwargs):
        await asyncio.sleep(3600)

    transport.send_message = wedged
    broker = _broker(tmp_path, transport)

    acks = await _deliver(broker, "hello")

    assert [a["type"] for a in acks] == ["user_delivery_failed"]
    assert broker._conversation_turns[0].metadata["steering_state"] == "failed"
