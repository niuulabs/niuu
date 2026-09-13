"""Ordinary user input must honor live steering, not implicit interrupt/replacement.

Hermetic broker + actual Codex adapter; no app-server, model or provider calls.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from niuu.ports.cli import TransportCapabilities
from tests.test_skuld.test_codex_ws_transport import _make_transport
from tests.test_skuld.test_delivery_failed_steering_state import _broker


@pytest.mark.parametrize(
    "mode,active,expected",
    [
        ("live", True, "steer"),
        ("live", False, "send"),
        ("native", True, "redirect"),
        ("native", False, "redirect"),
        ("interrupt_resume", True, "redirect"),
        ("interrupt_resume", False, "send"),
    ],
)
async def test_broker_honors_transport_steering_semantics(tmp_path, mode, active, expected):
    broker = _broker(tmp_path)
    broker._transport.capabilities = TransportCapabilities(steer=True, steering_mode=mode)
    broker._transport.is_turn_active = active
    await broker._attempt_transport_delivery(
        "Also keep project context", msg_id="human", request_id="req"
    )
    if expected == "send":
        broker._transport.send_control.assert_not_awaited()
        broker._transport.send_message.assert_awaited_once_with(
            "Also keep project context", msg_id="human", request_id="req"
        )
    else:
        broker._transport.send_message.assert_not_awaited()
        broker._transport.send_control.assert_awaited_once_with(
            expected, content="Also keep project context", msg_id="human", request_id="req"
        )


async def test_real_codex_live_delivery_keeps_turn_tools_and_prompt_and_exact_ids(tmp_path):
    broker = _broker(tmp_path)
    transport = _make_transport(tmp_path)
    broker._transport = transport
    transport._thread_id = "thread"
    transport._current_turn_id = "turn"
    transport._active_user_prompt = "Implement the agreed project experience"
    transport._send_rpc = AsyncMock(return_value={"turnId": "turn"})
    events = []
    transport.on_event(AsyncMock(side_effect=events.append))
    # An independent in-flight command must not be cancelled by receipt of an update.
    release = asyncio.Event()
    command = asyncio.create_task(release.wait())
    try:
        for n in [1, 2]:
            await broker._attempt_transport_delivery(
                f"Additional requirement {n}", msg_id=f"human-{n}", request_id=f"req-{n}"
            )
        assert [call.args[0] for call in transport._send_rpc.await_args_list] == ["turn/steer"] * 2
        assert transport._current_turn_id == "turn"
        assert transport._active_user_prompt == "Implement the agreed project experience"
        assert not command.done()
        assert not transport._pending_redirects
        assert not transport._pending_prompt_correlations
        assert [(e["msg_id"], e["request_id"]) for e in events] == [
            ("human-1", "req-1"),
            ("human-2", "req-2"),
        ]
        assert all(e["event_type"] == "codex.turn.steer.accepted" for e in events)
    finally:
        release.set()
        await command


async def test_turn_finished_before_adapter_delivery_starts_once_with_same_identity(tmp_path):
    transport = _make_transport(tmp_path)
    transport._thread_id = "thread"
    transport._send_rpc = AsyncMock(return_value={})
    await transport.send_control("steer", content="Continue", msg_id="human", request_id="req")
    transport._send_rpc.assert_awaited_once_with(
        "turn/start",
        {
            "threadId": "thread",
            "input": [{"type": "text", "text": "Continue", "textElements": []}],
            "model": "o4-mini",
            "effort": "high",
        },
    )
    assert transport._pending_prompt_correlations == [("human", "req")]


@pytest.mark.parametrize("result", [{}, {"turnId": "different"}, None])
async def test_ambiguous_acceptance_never_fakes_consumption_or_starts_replacement(tmp_path, result):
    transport = _make_transport(tmp_path)
    transport._thread_id = "thread"
    transport._current_turn_id = "turn"
    transport._send_rpc = AsyncMock(return_value=result)
    emit = AsyncMock()
    transport.on_event(emit)
    with pytest.raises(RuntimeError, match="not confirmed"):
        await transport.send_control(
            "steer", content="Keep working", msg_id="human", request_id="req"
        )
    assert transport._send_rpc.await_count == 1
    assert transport._send_rpc.await_args.args[0] == "turn/steer"
    emit.assert_not_awaited()
    assert not transport._pending_prompt_correlations


async def test_explicit_stop_still_interrupts_without_replacement(tmp_path):
    transport = _make_transport(tmp_path)
    transport._thread_id = "thread"
    transport._current_turn_id = "turn"
    transport._send_rpc = AsyncMock(return_value={})
    await transport.send_control("interrupt")
    transport._send_rpc.assert_awaited_once_with(
        "turn/interrupt", {"threadId": "thread", "turnId": "turn"}
    )
    assert not transport._pending_redirects


@pytest.mark.parametrize("lost_response", [False, True])
async def test_live_input_replay_restart_and_duplicate_claim_keep_exact_identity(
    tmp_path, lost_response
):
    import json

    from niuu.domain.transcript_reducer import reduce_frames
    from tests.test_skuld.test_broker_review_regressions import (
        _broker as durable_broker,
    )
    from tests.test_skuld.test_broker_review_regressions import (
        _finish_deliveries,
        _Ledger,
    )
    from tests.test_skuld.test_delivery_failed_steering_state import _frames_from_buffer

    broker = durable_broker(tmp_path)
    transport = _make_transport(tmp_path)
    transport._thread_id = "thread"
    transport._current_turn_id = "turn"
    transport._send_rpc = AsyncMock(return_value={"turnId": "turn"})
    if lost_response:
        transport._send_rpc.side_effect = TimeoutError("steer acknowledgement lost")
    broker._transport = transport
    transport.on_event(broker._handle_cli_event)
    ledger = _Ledger()
    message = {"content": "Keep working and preserve Back navigation", "request_id": "same-request"}
    async with ledger.client() as client:
        broker._http_client = client
        await broker._dispatch_browser_message(message)
        await _finish_deliveries()
        await broker._dispatch_browser_message(message)
        await _finish_deliveries()
        assert transport._send_rpc.await_count == 1
        assert transport._send_rpc.await_args.args[0] == "turn/steer"
        expected = {
            "request_id": "same-request",
            "steering_state": "pending" if lost_response else "active",
        }
        users = [turn for turn in broker._conversation_turns if turn.role == "user"]
        assert len(users) == 1 and users[0].metadata == expected
        snapshot = json.loads(broker._conversation_history_path().read_text())
        assert (
            next(turn for turn in snapshot["turns"] if turn["role"] == "user")["metadata"]
            == expected
        )
        replay = reduce_frames(_frames_from_buffer(broker)).turns
        replay_user = next(turn for turn in replay if turn["role"] == "user")
        assert replay_user["id"] == users[0].id and replay_user["metadata"] == expected
        # A recreated broker must consult the durable claim, not retry the provider.
        restarted = durable_broker(tmp_path)
        restarted._http_client = client
        await restarted._dispatch_browser_message(message)
        await _finish_deliveries()
        restarted._transport.send_message.assert_not_awaited()
        restarted._transport.send_control.assert_not_awaited()
    assert ledger.rows["same-request"]["status"] == ("pending" if lost_response else "delivered")
