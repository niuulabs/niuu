"""Offline regressions against the installed Codex 0.154 app-server contract."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from skuld.transports.codex_ws import CodexWebSocketTransport


@pytest.fixture(autouse=True)
def no_real_subprocesses(monkeypatch):
    """These are protocol unit tests; a regressed fallback must not call a provider."""
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        AsyncMock(side_effect=AssertionError("Real subprocess launch in an offline protocol test")),
    )


async def test_selected_model_reaches_next_native_turn(tmp_path):
    transport = CodexWebSocketTransport(str(tmp_path), model="gpt-5.6-sol", reasoning_effort="high")
    transport._thread_id = "thread"
    transport._send_rpc = AsyncMock(return_value={"turn": {"id": "turn"}})
    await transport.send_control("set_model", model="gpt-6-astra")
    await transport.send_message("Use the selected model")
    assert transport._send_rpc.await_args.args[1]["model"] == "gpt-6-astra"


@pytest.mark.parametrize("stage", ["_spawn_app_server", "_connect_ws", "_handshake"])
async def test_app_server_startup_failure_never_launches_a_different_harness(tmp_path, stage):
    transport = CodexWebSocketTransport(
        str(tmp_path),
        model="gpt-6-astra",
        skip_permissions=False,
        approval_policy="on-request",
        sandbox="read-only",
        initial_prompt="Do not execute twice",
    )
    for name in ("_spawn_app_server", "_connect_ws", "_handshake"):
        setattr(transport, name, AsyncMock())
    setattr(transport, stage, AsyncMock(side_effect=RuntimeError("unavailable")))
    transport.stop = AsyncMock()
    with (
        patch(
            "skuld.transports.codex.CodexSubprocessTransport.start", new_callable=AsyncMock
        ) as start,
        patch(
            "skuld.transports.codex.CodexSubprocessTransport.send_message", new_callable=AsyncMock
        ),
    ):
        with pytest.raises(RuntimeError, match="Codex app-server"):
            await transport.start()
    start.assert_not_awaited()
    transport.stop.assert_awaited_once()


async def test_unknown_server_request_is_rejected_not_approved(tmp_path):
    transport = CodexWebSocketTransport(str(tmp_path))
    transport._ws = AsyncMock()
    await transport._handle_server_request(
        {"id": "opaque-request-id", "method": "future/request", "params": {}}
    )
    response = json.loads(transport._ws.send.await_args.args[0])
    assert response["id"] == "opaque-request-id"
    assert response["error"]["code"] == -32601
    assert "result" not in response


async def test_disconnected_approval_remains_pending(tmp_path):
    transport = CodexWebSocketTransport(str(tmp_path))
    transport._pending_approvals["approval"] = ("native-id", "command_execution")
    with pytest.raises(RuntimeError, match="WebSocket"):
        await transport.send_control_response("approval", {"behavior": "allow"})
    assert "approval" in transport._pending_approvals


async def test_plan_items_keep_authoritative_public_text(tmp_path):
    transport = CodexWebSocketTransport(str(tmp_path))
    transport._thread_id = "thread"
    transport._current_turn_id = "turn"
    events = []

    async def capture(event):
        events.append(event)

    transport.on_event(capture)
    for method, data in [
        ("item/started", {"item": {"type": "plan", "id": "plan", "text": ""}}),
        ("item/plan/delta", {"itemId": "plan", "delta": "Draft plan"}),
        ("item/completed", {"item": {"type": "plan", "id": "plan", "text": "Corrected plan"}}),
    ]:
        await transport._handle_server_message(
            {"method": method, "params": {"threadId": "thread", "turnId": "turn", **data}}
        )
    assert any(e.get("delta", {}).get("text") == "Draft plan" for e in events)
    completed = [
        block
        for event in events
        if event["type"] == "assistant"
        for block in event["message"]["content"]
    ]
    assert len(completed) == 1
    assert completed[0]["id"] == "plan"
    assert completed[0]["text"] == "Corrected plan"
    assert completed[0]["complete"] is True
