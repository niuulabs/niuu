"""Offline regressions against the installed Codex 0.154 app-server contract."""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

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


@pytest.mark.parametrize(
    "arguments, method, params",
    [
        ("pause", "thread/goal/set", {"status": "paused"}),
        ("resume", "thread/goal/set", {"status": "active"}),
        ("clear", "thread/goal/clear", {}),
        ("edit Keep investigating", "thread/goal/set", {"objective": "Keep investigating"}),
        ("set pause", "thread/goal/set", {"objective": "pause", "status": "active"}),
    ],
)
async def test_goal_controls_are_not_replacement_objectives(tmp_path, arguments, method, params):
    transport = CodexWebSocketTransport(str(tmp_path))
    transport._thread_id = "thread"
    transport._send_rpc = AsyncMock(return_value={})
    await transport.send_control("slash_command", command="/goal", arguments=arguments)
    transport._send_rpc.assert_awaited_once_with(method, {"threadId": "thread", **params})


@pytest.mark.parametrize(
    "command, arguments",
    [
        ("/not-real", ""),
        ("/rename", ""),
        ("/compact", "ignored"),
        ("/goal", "edit"),
        ("/goal", "pause ignored"),
        ("/skills", "not-an-invocation"),
    ],
)
async def test_unsupported_command_semantics_reject_before_rpc(tmp_path, command, arguments):
    transport = CodexWebSocketTransport(str(tmp_path))
    transport._thread_id = "thread"
    transport._send_rpc = AsyncMock()
    with pytest.raises(ValueError):
        await transport.send_control("slash_command", command=command, arguments=arguments)
    transport._send_rpc.assert_not_awaited()


async def test_skills_come_from_codex_workspace_discovery(tmp_path):
    transport = CodexWebSocketTransport(str(tmp_path))
    transport._thread_id = "thread"
    transport._send_rpc = AsyncMock(
        return_value={
            "data": [
                {
                    "cwd": str(tmp_path),
                    "skills": [{"name": "triage", "description": "Triage issues", "enabled": True}],
                    "errors": [],
                }
            ]
        }
    )
    transport._emit = AsyncMock()
    await transport.send_control("slash_command", command="/skills")
    transport._send_rpc.assert_awaited_once_with(
        "skills/list", {"cwds": [str(tmp_path)], "forceReload": True}
    )
    assert "$triage (enabled): Triage issues" in transport._emit.await_args.args[0]["content"]


async def test_mcp_discovery_consumes_pages_and_uses_native_thread(tmp_path):
    transport = CodexWebSocketTransport(str(tmp_path))
    transport._thread_id = "thread"
    transport._send_rpc = AsyncMock(
        side_effect=[
            {
                "data": [{"name": "one", "tools": {"read": {}}, "authStatus": "oAuth"}],
                "nextCursor": "next",
            },
            {
                "data": [{"name": "two", "tools": {}, "authStatus": "notLoggedIn"}],
                "nextCursor": None,
            },
        ]
    )
    transport._emit = AsyncMock()
    await transport.send_control("slash_command", command="/mcp")
    assert [call.args[1] for call in transport._send_rpc.await_args_list] == [
        {"threadId": "thread"},
        {"threadId": "thread", "cursor": "next"},
    ]
    content = transport._emit.await_args.args[0]["content"]
    assert "one: 1 tools" in content and "two: 0 tools" in content


async def test_mcp_repeated_cursor_is_not_an_infinite_loop(tmp_path):
    transport = CodexWebSocketTransport(str(tmp_path))
    transport._thread_id = "thread"
    transport._send_rpc = AsyncMock(return_value={"data": [], "nextCursor": "repeated"})
    with pytest.raises(RuntimeError, match="repeated MCP cursor"):
        await transport.send_control("slash_command", command="/mcp")
    assert transport._send_rpc.await_count == 2


@pytest.mark.parametrize("command", ["/rename", "/title"])
async def test_rename_and_legacy_alias_share_native_action(tmp_path, command):
    transport = CodexWebSocketTransport(str(tmp_path))
    transport._thread_id = "thread"
    transport._send_rpc = AsyncMock(return_value={})
    await transport.send_control("slash_command", command=command, arguments="Review work")
    transport._send_rpc.assert_awaited_once_with(
        "thread/name/set", {"threadId": "thread", "name": "Review work"}
    )


async def test_status_reads_native_metadata_without_hydrating_all_turns(tmp_path):
    transport = CodexWebSocketTransport(
        str(tmp_path), model="gpt-6-astra", reasoning_effort="xhigh"
    )
    transport._thread_id = "thread"
    transport._send_rpc = AsyncMock(
        return_value={"thread": {"id": "thread", "status": {"type": "idle"}}}
    )
    transport._emit = AsyncMock()
    await transport.send_control("slash_command", command="/status")
    transport._send_rpc.assert_awaited_once_with(
        "thread/read", {"threadId": "thread", "includeTurns": False}
    )
    assert "Requested next-turn model: gpt-6-astra" in transport._emit.await_args.args[0]["content"]


def test_broker_keeps_native_command_hints_and_method_metadata():
    from skuld.broker import Broker

    entries = Broker._normalize_slash_commands(
        [
            {
                "name": "/rename",
                "description": "Rename",
                "source": "codex-app-server",
                "argument_hint": "<name>",
                "method": "thread/name/set",
                "capability": "thread.name.set",
            }
        ]
    )
    assert entries[0]["method"] == "thread/name/set"
    assert entries[0]["argument_hint"] == "<name>"


@pytest.mark.parametrize("rollout_frame", [False, True])
async def test_raw_tool_observation_cannot_execute_or_inject_output(tmp_path, rollout_frame):
    transport = CodexWebSocketTransport(str(tmp_path))
    transport._thread_id = "thread"
    transport._send_rpc = AsyncMock(return_value={})
    transport._execute_shell_command_tool = AsyncMock(
        return_value={"contentItems": [], "success": True}
    )
    item = {
        "type": "function_call",
        "name": "shell_command",
        "call_id": "native-call",
        "arguments": json.dumps({"command": "must not run again"}),
    }
    if rollout_frame:
        await transport._handle_response_item_frame(item)
    else:
        await transport._handle_server_message(
            {"method": "rawResponseItem/completed", "params": {"threadId": "thread", "item": item}}
        )
    await asyncio.sleep(0)  # Also catches the old background execution path.
    transport._execute_shell_command_tool.assert_not_awaited()
    transport._send_rpc.assert_not_awaited()


@pytest.mark.parametrize(
    "error, status", [(ValueError("unsupported"), 400), (RuntimeError("native failure"), 502)]
)
def test_command_http_rejection_is_not_reported_as_sent(tmp_path, monkeypatch, error, status):
    from types import SimpleNamespace

    from skuld import broker_api

    transport = CodexWebSocketTransport(str(tmp_path))
    transport.send_control = AsyncMock(side_effect=error)
    monkeypatch.setattr(broker_api, "_broker_getter", lambda: SimpleNamespace(_transport=transport))
    # No lifespan context: this test must never initialize the application runtime.
    response = TestClient(broker_api.app).post(
        "/api/slash-commands/send", json={"command": "/review"}
    )
    assert response.status_code == status
    assert response.json()["detail"] == str(error)
