"""Offline regressions against the installed Codex 0.154 app-server contract."""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from skuld.transports.codex_ws import CodexWebSocketTransport, _CodexApproval


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
    transport._pending_approvals["approval"] = _CodexApproval(
        "native-id", "item/commandExecution/requestApproval"
    )
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
    response = TestClient(broker_api.app, headers={"x-niuu-room-role": "owner"}).post(
        "/api/slash-commands/send", json={"command": "/review"}
    )
    assert response.status_code == status
    assert response.json()["detail"] == str(error)


async def _approval(transport, method, *, rid="native-request", **params):
    transport._emit = AsyncMock()
    await transport._handle_server_request(
        {"id": rid, "method": method, "params": {"threadId": "thread", **params}}
    )
    return transport._emit.await_args.args[0]["request_id"]


@pytest.mark.parametrize(
    "method, behavior, expected",
    [
        ("item/commandExecution/requestApproval", "allowOnce", {"decision": "accept"}),
        ("item/commandExecution/requestApproval", "allowForever", {"decision": "acceptForSession"}),
        ("item/fileChange/requestApproval", "allowForever", {"decision": "acceptForSession"}),
        ("item/fileChange/requestApproval", "cancel", {"decision": "cancel"}),
        ("execCommandApproval", "deny", {"decision": {"denied": {"rejection": "User declined"}}}),
        ("applyPatchApproval", "allow", {"decision": "approved"}),
        ("applyPatchApproval", "allowForever", {"decision": "approved_for_session"}),
        ("applyPatchApproval", "cancel", {"decision": "abort"}),
    ],
)
async def test_each_approval_uses_its_native_response_contract(
    tmp_path, method, behavior, expected
):
    transport = CodexWebSocketTransport(str(tmp_path))
    transport._ws = AsyncMock()
    request_id = await _approval(transport, method)
    await transport.send_control_response(request_id, {"behavior": behavior})
    assert json.loads(transport._ws.send.await_args.args[0]) == {
        "jsonrpc": "2.0",
        "id": "native-request",
        "result": expected,
    }


@pytest.mark.parametrize(
    "behavior, scope", [("allow", "turn"), ("allowForever", "session"), ("deny", "turn")]
)
async def test_permission_grant_is_requested_profile_not_command_decision(
    tmp_path, behavior, scope
):
    transport = CodexWebSocketTransport(str(tmp_path))
    transport._ws = AsyncMock()
    requested = {"network": {"enabled": True}, "fileSystem": {"write": ["/tmp/requested"]}}
    request_id = await _approval(
        transport,
        "item/permissions/requestApproval",
        permissions=requested,
        cwd="/tmp",
        reason="test",
    )
    event = transport._emit.await_args.args[0]
    assert event["tool"] == "Permissions"
    assert event["input"]["permissions"] == requested
    await transport.send_control_response(request_id, {"behavior": behavior})
    result = json.loads(transport._ws.send.await_args.args[0])["result"]
    assert result == {"permissions": {} if behavior == "deny" else requested, "scope": scope}


@pytest.mark.parametrize("response", [{}, {"behavior": "typo"}])
async def test_approval_never_invents_a_choice(tmp_path, response):
    transport = CodexWebSocketTransport(str(tmp_path))
    transport._ws = AsyncMock()
    request_id = await _approval(transport, "item/commandExecution/requestApproval")
    with pytest.raises(ValueError, match="behavior"):
        await transport.send_control_response(request_id, response)
    transport._ws.send.assert_not_awaited()
    assert request_id in transport._pending_approvals


async def test_command_approval_retains_network_stdin_and_scoped_context(tmp_path):
    transport = CodexWebSocketTransport(str(tmp_path))
    context = {
        "threadId": "thread",
        "turnId": "turn",
        "itemId": "item",
        "approvalId": "callback",
        "kind": "stdin",
        "cwd": "/tmp",
        "reason": "network needed",
        "networkApprovalContext": {"host": "example.com", "protocol": "https"},
        "availableDecisions": ["accept", "decline"],
    }
    await _approval(transport, "item/commandExecution/requestApproval", **context)
    assert transport._emit.await_args.args[0]["input"] == context


async def test_unavailable_native_decision_cannot_widen_approval(tmp_path):
    transport = CodexWebSocketTransport(str(tmp_path))
    transport._ws = AsyncMock()
    request_id = await _approval(
        transport, "item/commandExecution/requestApproval", availableDecisions=["decline", "cancel"]
    )
    with pytest.raises(ValueError, match="available"):
        await transport.send_control_response(request_id, {"behavior": "allowForever"})
    transport._ws.send.assert_not_awaited()
    assert request_id in transport._pending_approvals


@pytest.mark.parametrize("skip, policy", [(True, ""), (False, "never"), (False, "on-request")])
async def test_mcp_elicitation_never_fabricates_user_content(tmp_path, skip, policy):
    transport = CodexWebSocketTransport(
        str(tmp_path), skip_permissions=skip, approval_policy=policy
    )
    transport._ws = AsyncMock()
    request_id = await _approval(
        transport,
        "mcpServer/elicitation/request",
        mode="form",
        requestedSchema={"type": "object", "required": ["choice"]},
        message="Choose",
    )
    transport._ws.send.assert_not_awaited()
    assert transport._emit.await_args.args[0]["auto_approval_allowed"] is False
    with pytest.raises(ValueError, match="content"):
        await transport.send_control_response(request_id, {"behavior": "allow"})
    assert request_id in transport._pending_approvals
    await transport.send_control_response(
        request_id, {"behavior": "allow", "content": {"choice": "A"}}
    )
    assert json.loads(transport._ws.send.await_args.args[0])["result"] == {
        "action": "accept",
        "content": {"choice": "A"},
    }


@pytest.mark.parametrize(
    "method, resolved_type",
    [
        ("item/fileChange/requestApproval", "permission_resolved"),
        ("item/tool/requestUserInput", "ask_user_resolved"),
    ],
)
async def test_native_resolution_retires_exact_control_without_answering_again(
    tmp_path, method, resolved_type
):
    transport = CodexWebSocketTransport(str(tmp_path))
    transport._thread_id = "thread"
    transport._ws = AsyncMock()
    request_id = await _approval(transport, method, rid=7)
    other = await _approval(transport, "item/fileChange/requestApproval", rid="7")
    transport._emit.reset_mock()
    notification = {
        "method": "serverRequest/resolved",
        "params": {"threadId": "thread", "requestId": 7},
    }
    await transport._handle_server_message(notification)
    event = transport._emit.await_args.args[0]
    assert event["type"] == resolved_type
    assert event["request_id"] == request_id
    assert other in transport._pending_approvals
    assert request_id not in transport._pending_approvals
    assert request_id not in transport._pending_user_inputs
    transport._emit.reset_mock()
    await transport._handle_server_message(notification)
    transport._emit.assert_not_awaited()
    transport._ws.send.assert_not_awaited()


async def test_native_permission_resolution_clears_live_and_persisted_replay(tmp_path):
    from types import SimpleNamespace

    from skuld.broker import Broker
    from skuld.config import SkuldSettings
    from skuld.control_state import load_control_state, pending_controls

    broker = Broker(
        SkuldSettings(
            session={"id": "offline", "workspace_dir": str(tmp_path)},
            volundr_api_url="http://forge.test",
        )
    )
    broker._report_activity_state = AsyncMock()
    broker._evaluate_permission_auto_approval = AsyncMock(return_value=None)
    transport = CodexWebSocketTransport(str(tmp_path))
    broker._transport = transport
    transport.on_event(broker._handle_cli_event)
    await transport._handle_server_request(
        {
            "id": "native",
            "method": "item/fileChange/requestApproval",
            "params": {"threadId": "thread"},
        }
    )
    request_id = next(iter(transport._pending_approvals))
    assert request_id in broker._pending_permission_requests
    await transport._handle_server_message(
        {
            "method": "serverRequest/resolved",
            "params": {"threadId": "thread", "requestId": "native"},
        }
    )
    assert request_id not in broker._pending_permission_requests
    assert load_control_state(broker._control_state_path()) == ({}, {})
    frames = [
        SimpleNamespace(kind=f["kind"], payload=f["payload"]) for f in broker._event_log_buffer
    ]
    assert pending_controls(frames) == ({}, {})
    assert any(f.kind == "permission_resolved" for f in frames)


async def test_elicitation_content_reaches_native_without_auto_approval(tmp_path):
    from skuld.broker import Broker
    from skuld.config import SkuldSettings

    broker = Broker(
        SkuldSettings(
            session={"id": "offline", "workspace_dir": str(tmp_path)},
            volundr_api_url="http://forge.test",
        )
    )
    broker._report_activity_state = AsyncMock()
    broker._evaluate_permission_auto_approval = AsyncMock(return_value={"can_auto_approve": True})
    transport = CodexWebSocketTransport(str(tmp_path))
    transport._ws = AsyncMock()
    broker._transport = transport
    transport.on_event(broker._handle_cli_event)
    await transport._handle_server_request(
        {
            "id": "form",
            "method": "mcpServer/elicitation/request",
            "params": {"mode": "form", "message": "Choose"},
        }
    )
    request_id = next(iter(transport._pending_approvals))
    await asyncio.sleep(0)
    broker._evaluate_permission_auto_approval.assert_not_awaited()
    transport._ws.send.assert_not_awaited()
    assert request_id in broker._pending_attention
    await broker._dispatch_browser_message(
        {
            "type": "permission_response",
            "request_id": request_id,
            "behavior": "allow",
            "content": {"choice": "A"},
        }
    )
    assert json.loads(transport._ws.send.await_args.args[0])["result"] == {
        "action": "accept",
        "content": {"choice": "A"},
    }
    assert request_id not in broker._pending_permission_requests
    assert request_id not in broker._pending_attention


async def test_native_resolution_cancels_scheduled_attention_before_it_can_reopen(tmp_path):
    from skuld.broker import Broker
    from skuld.config import SkuldSettings

    broker = Broker(
        SkuldSettings(session={"id": "offline", "workspace_dir": str(tmp_path)}, volundr_api_url="")
    )
    broker._report_activity_state = AsyncMock()
    await broker._handle_cli_event(
        {"type": "control_request", "request_id": "r", "auto_approval_allowed": False}
    )
    task = broker._permission_auto_approval_tasks["r"]
    await broker._handle_cli_event({"type": "permission_resolved", "request_id": "r"})
    await asyncio.gather(task, return_exceptions=True)
    assert task.cancelled()
    assert "r" not in broker._pending_attention


@pytest.mark.parametrize(
    "method", ["item/commandExecution/requestApproval", "item/permissions/requestApproval"]
)
@pytest.mark.parametrize(
    "edits",
    [{"updatedInput": {"command": "changed"}}, {"updatedPermissions": [{"type": "addRules"}]}],
)
async def test_approval_does_not_silently_discard_input_or_policy_edits(tmp_path, method, edits):
    transport = CodexWebSocketTransport(str(tmp_path))
    transport._ws = AsyncMock()
    request_id = await _approval(transport, method, permissions={"network": {"enabled": True}})
    with pytest.raises(ValueError, match="cannot edit"):
        await transport.send_control_response(request_id, {"behavior": "allow", **edits})
    transport._ws.send.assert_not_awaited()
    assert request_id in transport._pending_approvals


async def test_permission_profile_cannot_be_mutated_by_channel_or_failed_send(tmp_path):
    transport = CodexWebSocketTransport(str(tmp_path))
    transport._ws = AsyncMock()
    transport._ws.send.side_effect = RuntimeError("lost socket")
    request_id = await _approval(
        transport, "item/permissions/requestApproval", permissions={"network": {"enabled": False}}
    )
    transport._emit.await_args.args[0]["input"]["permissions"]["network"]["enabled"] = True
    with pytest.raises(RuntimeError, match="lost socket"):
        await transport.send_control_response(request_id, {"behavior": "allow"})
    assert request_id in transport._pending_approvals
    assert json.loads(transport._ws.send.await_args.args[0])["result"]["permissions"] == {
        "network": {"enabled": False}
    }


@pytest.mark.parametrize(
    "behavior, expected",
    [
        ("deny", {"action": "decline", "content": None}),
        ("cancel", {"action": "cancel", "content": None}),
        ("allow", {"action": "accept"}),
    ],
)
async def test_url_elicitation_has_explicit_native_actions(tmp_path, behavior, expected):
    transport = CodexWebSocketTransport(str(tmp_path))
    transport._ws = AsyncMock()
    request_id = await _approval(
        transport, "mcpServer/elicitation/request", mode="url", url="https://example.com/consent"
    )
    await transport.send_control_response(request_id, {"behavior": behavior})
    assert json.loads(transport._ws.send.await_args.args[0])["result"] == expected


async def test_malformed_permission_request_rejected_before_user_prompt(tmp_path):
    transport = CodexWebSocketTransport(str(tmp_path))
    transport._ws = AsyncMock()
    transport._emit = AsyncMock()
    await transport._handle_server_request(
        {"id": 1, "method": "item/permissions/requestApproval", "params": {}}
    )
    assert json.loads(transport._ws.send.await_args.args[0])["error"]["code"] == -32602
    assert not transport._pending_approvals
    transport._emit.assert_not_awaited()


async def test_disconnected_error_response_fails_explicitly(tmp_path):
    transport = CodexWebSocketTransport(str(tmp_path))
    with pytest.raises(RuntimeError, match="not sent"):
        await transport._send_rpc_error("id", -32601, "Unsupported")


async def test_closed_transport_retires_approval_outside_active_turn(tmp_path):
    transport = CodexWebSocketTransport(str(tmp_path))
    request_id = await _approval(transport, "mcpServer/elicitation/request", mode="url")
    transport._emit.reset_mock()
    await transport._finalize_stranded_turn("error", "closed")
    resolved = [
        call.args[0]
        for call in transport._emit.await_args_list
        if call.args[0]["type"] == "permission_resolved"
    ]
    assert [event["request_id"] for event in resolved] == [request_id]
    assert not transport._pending_approvals


async def test_resolution_does_not_deadlock_reader_behind_inflight_answer(tmp_path):
    transport = CodexWebSocketTransport(str(tmp_path))
    request_id = await _approval(transport, "item/fileChange/requestApproval")
    async with transport._answer_lock:
        await asyncio.wait_for(transport._resolve_server_request("native-request"), timeout=0.1)
    assert request_id not in transport._pending_approvals


@pytest.mark.parametrize("invalid_id", [None, True, [], {}])
async def test_resolution_without_exact_rpc_id_never_retires_pending_request(tmp_path, invalid_id):
    transport = CodexWebSocketTransport(str(tmp_path))
    request_id = await _approval(transport, "item/fileChange/requestApproval", rid=1)
    transport._emit.reset_mock()
    await transport._resolve_server_request(invalid_id)
    assert request_id in transport._pending_approvals
    transport._emit.assert_not_awaited()


@pytest.mark.parametrize("operation", ["start", "import", "resume"])
async def test_native_history_requests_do_not_send_removed_persistence_flag(tmp_path, operation):
    transport = CodexWebSocketTransport(
        str(tmp_path), resume_session_id="thread" if operation == "import" else ""
    )
    transport._send_rpc = AsyncMock(return_value={"thread": {"id": "thread"}})
    transport._send_notification = AsyncMock()
    transport._authenticate_codex = AsyncMock()
    transport._emit = AsyncMock()
    if operation == "resume":
        await transport.resume("thread")
    else:
        await transport._handshake()
    calls = [
        call
        for call in transport._send_rpc.await_args_list
        if call.args[0] in ("thread/start", "thread/resume")
    ]
    assert len(calls) == 1
    assert "persistExtendedHistory" not in calls[0].args[1]
    assert calls[0].args[1].get("historyMode", "legacy") == "legacy"


@pytest.mark.parametrize("operation", ["start", "import", "resume"])
async def test_native_thread_response_cannot_invent_success_without_identity(tmp_path, operation):
    transport = CodexWebSocketTransport(
        str(tmp_path), resume_session_id="thread" if operation == "import" else ""
    )
    transport._send_rpc = AsyncMock(return_value={})
    transport._send_notification = AsyncMock()
    transport._authenticate_codex = AsyncMock()
    transport._emit = AsyncMock()
    with pytest.raises(RuntimeError, match="identify"):
        if operation == "resume":
            await transport.resume("thread")
        else:
            await transport._handshake()
    assert transport._thread_id is None
    transport._emit.assert_not_awaited()


async def test_resume_cannot_replace_bound_conversation_with_wrong_thread(tmp_path):
    transport = CodexWebSocketTransport(str(tmp_path))
    transport._thread_id = "original"
    transport._send_rpc = AsyncMock(return_value={"thread": {"id": "unexpected"}})
    transport._emit = AsyncMock()
    with pytest.raises(RuntimeError, match="different conversation"):
        await transport.resume("requested")
    assert transport._thread_id == "original"
    transport._emit.assert_not_awaited()
