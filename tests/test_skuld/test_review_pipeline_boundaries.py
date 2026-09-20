"""Review acceptance at provider races and workspace artifact boundaries."""

import asyncio
import importlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from skuld.transports.muse import MuseMSPTransport, MuseProtocolError
from skuld.transports.tmux_interactive import TmuxInteractiveTransport
from tests.test_skuld.test_broker_review_regressions import (
    _broker,
    _events,
    _finish_deliveries,
    _Ledger,
)


@pytest.mark.parametrize("steering", [False, True])
@pytest.mark.parametrize("outcomes", [[False, None], [False, False]])
async def test_claimed_definite_refusal_retries_but_never_duplicates(tmp_path, steering, outcomes):
    from niuu.ports.cli import TransportCapabilities

    ledger = _Ledger()
    b = _broker(tmp_path)
    b._settings.delivery.initial_backoff_seconds = 0
    b._transport.capabilities = TransportCapabilities(
        steer=steering, steering_mode="native" if steering else "none"
    )
    sender = b._transport.send_control if steering else b._transport.send_message
    sender.side_effect = outcomes
    message = {"content": "review only", "request_id": "review"}
    async with ledger.client() as client:
        b._http_client = client
        await b._dispatch_browser_message(message)
        await _finish_deliveries()
        await b._dispatch_browser_message(message)
    assert sender.await_count == 2
    expected = "failed" if outcomes[-1] is False else "delivered"
    assert ledger.rows["review"]["status"] == expected
    assert len(b._conversation_turns) == 1
    assert _events(b, "user_delivery_failed" if expected == "failed" else "user_delivered")


@pytest.mark.parametrize("reason", ["failed", "error", "interrupted", "completed"])
async def test_claude_plan_and_subagent_hooks_merge_identity_and_complete(tmp_path, reason):
    t = TmuxInteractiveTransport(str(tmp_path))
    t._emit = AsyncMock()
    t._agent_usage.register = MagicMock()
    await t._surface_plan_from_todowrite({"todos": "bad shape"})
    t._emit.assert_not_called()
    await t._surface_plan_from_todowrite(
        {
            "todos": [
                None,
                {},
                {"content": "scope", "status": "done"},
                {"content": "review", "status": "running", "activeForm": "Reviewing"},
                {"content": "tests"},
                {"content": "approval", "status": "blocked"},
            ]
        }
    )
    plan = t._emit.call_args.args[0]
    assert plan["counts"] == {"total": 4, "pending": 1, "in_progress": 1, "completed": 1}
    assert plan["tasks"][1]["activeForm"] == "Reviewing"
    await t._surface_agent_started_from_task("task-1", {"description": "review changes"})
    await t._surface_subagent_start(
        {
            "tool_use_id": "task-1",
            "agent_id": "native-id",
            "agent_type": "reviewer",
            "task": "inspect replay",
            "model": "claude-fable-5-1",
            "transcript_path": "/fixture.jsonl",
        }
    )
    assert list(t._hook_agents) == ["task-1"]
    assert t._hook_agents["task-1"]["name"] == "reviewer"
    t._agent_usage.register.assert_called_once_with("task-1", "/fixture.jsonl")
    await t._surface_subagent_stop({"tool_use_id": "task-1", "reason": reason})
    end = t._emit.call_args.args[0]
    assert end["agent"]["status"] == ("done" if reason == "completed" else "failed")
    assert end["agent"]["ended_at"] >= end["agent"]["started_at"]
    assert not t._hook_agents
    await t._surface_subagent_stop({"agent_id": "unknown"})
    await t._surface_subagent_start({})
    assert not t._hook_agents


@pytest.mark.parametrize(
    "status,expected",
    [
        ("pending", "pending"),
        ("in_progress", "in_progress"),
        ("completed", "completed"),
        ("complete", "completed"),
        ("finished", "completed"),
        ("active", "in_progress"),
        ("doing", "in_progress"),
        (None, "pending"),
        (" blocked ", "blocked"),
    ],
)
def test_claude_status_vocabulary_preserves_unknown_states(status, expected):
    assert TmuxInteractiveTransport._normalize_status(status) == expected


@pytest.mark.parametrize(
    "text",
    [
        "?",
        "│",
        "╭",
        "╰",
        "────",
        "? for shortcuts",
        "esc to interrupt",
        "Esc to cancel",
        "Bash command",
        "Do you want to proceed?",
        "1. Allow",
        "⎿ Tip: try /help",
        "⚠ run /doctor",
        "◉ /effort xhigh",
        "✻ ·",
        "✻ Thinking...",
        "thinking with xhigh effort",
        "↑ 1.2k tokens",
        "Thought for 30s",
        "You're now using usage credits",
        "Your session limit resets at noon",
    ],
)
def test_native_terminal_chrome_cannot_leak_into_assistant_response(text):
    assert TmuxInteractiveTransport._is_terminal_chrome_row(text)
    assert not TmuxInteractiveTransport._is_terminal_chrome_row("The migration needs a retry.")


def _muse(tmp_path):
    t = MuseMSPTransport(str(tmp_path))
    t._ensure_started = AsyncMock()
    t._ensure_session_loaded = AsyncMock()
    t._start_watchdog = MagicMock()
    t._emit = AsyncMock()
    return t


@pytest.mark.parametrize("failure", ["sessionNotLoaded", "sessionNotFound", "commandRejected"])
async def test_muse_idle_unload_retries_same_command_identity(tmp_path, failure):
    t = _muse(tmp_path)
    t._request = AsyncMock(
        side_effect=[MuseProtocolError({"data": {"kind": failure}}), {"disposition": "queued"}]
    )
    if failure == "commandRejected":
        with pytest.raises(MuseProtocolError):
            await t._submit_turn("review", if_busy="queue", msg_id="m", request_id="r")
        assert not t._turns
        assert t._request.await_count == 1
    else:
        state = await t._submit_turn("review", if_busy="queue", msg_id="m", request_id="r")
        assert state.correlation == ("m", "r")
        assert t._request.call_args_list[0].args == t._request.call_args_list[1].args
        assert t._ensure_session_loaded.await_count == 2
        t._start_watchdog.assert_not_called()


@pytest.mark.parametrize("race", ["existing", "completed", "unseen", "steered"])
async def test_muse_ack_race_adopts_native_turn_without_stranding_waiter(tmp_path, race):
    t = _muse(tmp_path)
    terminal = {"type": "result", "turnId": "native", "is_error": False}

    async def ack(method, params, **kwargs):
        if race in {"existing", "steered"}:
            state = t._ensure_turn("native")
            state.started = True
        elif race == "completed":
            t._recent_results["native"] = terminal
        return {"turnId": "native", "disposition": "steered" if race == "steered" else "started"}

    t._request = ack
    state = await t._submit_turn("review", if_busy="steer", msg_id="m", request_id="r")
    if race == "steered":
        assert state is None
        assert t._turns["native"].in_chars == 6
    elif race == "completed":
        assert state.future.result() == terminal
        assert not t._turns
    else:
        assert list(t._turns) == ["native"]
        assert state.correlation == ("m", "r")
        assert state.turn_id == "native"
        t._start_watchdog.assert_called_once_with(state)
    if race in {"existing", "steered"}:
        consumed = [
            c.args[0] for c in t._emit.call_args_list if c.args[0]["type"] == "user_consumed"
        ]
        assert consumed[0]["request_id"] == "r"


async def test_muse_unqueue_resolves_waiter_and_cancels_watchdog(tmp_path):
    t = _muse(tmp_path)
    state = t._ensure_turn("queued")
    state.watchdog = asyncio.create_task(asyncio.Event().wait())
    await t._on_notification("turn/unqueued", {"turnId": "queued"})
    await asyncio.gather(state.watchdog, return_exceptions=True)
    assert state.future.result()["stop_reason"] == "unqueued"
    assert state.watchdog.cancelled()
    assert not t._turns
    await t._on_notification("turn/unqueued", {"turnId": "unknown"})


@pytest.fixture
def endpoint_module(tmp_path, monkeypatch):
    module = importlib.import_module("skuld.broker")
    monkeypatch.setattr(module, "broker", _broker(tmp_path, api_url=""))
    return module


@pytest.mark.parametrize("base", ["last-commit", "default-branch"])
async def test_diff_file_counts_include_binary_and_unicode_paths(
    endpoint_module, monkeypatch, base
):
    proc = SimpleNamespace(
        returncode=0,
        communicate=AsyncMock(
            return_value=("2\t3\tsrc/α.py\n-\t-\timage.png\ninvalid\n".encode(), b"")
        ),
    )
    spawn = AsyncMock(return_value=proc)
    monkeypatch.setattr(endpoint_module.asyncio, "create_subprocess_exec", spawn)
    result = await endpoint_module.get_diff_files(base)
    assert result == {
        "files": [
            {"path": "src/α.py", "status": "mod", "ins": 2, "del": 3},
            {"path": "image.png", "status": "mod", "ins": 0, "del": 0},
        ]
    }
    assert spawn.call_args.args[2] == ("HEAD" if base == "last-commit" else "main...HEAD")


@pytest.mark.parametrize("failure,status", [("base", 400), ("timeout", 504), ("git", 502)])
async def test_diff_files_surfaces_bounded_native_failures(
    endpoint_module, monkeypatch, failure, status
):
    proc = SimpleNamespace(
        returncode=128, communicate=AsyncMock(return_value=(b"", b"bad revision"))
    )
    if failure == "timeout":
        proc.communicate.side_effect = TimeoutError()
    monkeypatch.setattr(
        endpoint_module.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)
    )
    with pytest.raises(HTTPException) as caught:
        await endpoint_module.get_diff_files("bogus" if failure == "base" else "last-commit")
    assert caught.value.status_code == status


@pytest.mark.parametrize(
    "fault,status",
    [
        ("ok", 200),
        ("bad_uuid", 404),
        ("noncanonical", 404),
        ("missing_call", 404),
        ("failed_result", 404),
        ("bad_json", 404),
        ("wrong_attachment", 404),
        ("empty_path", 404),
        ("unrequested_path", 404),
        ("bad_files", 404),
        ("missing_file", 410),
        ("directory", 410),
        ("oversize", 413),
    ],
)
async def test_presented_file_requires_matching_successful_tool_pair(
    tmp_path, endpoint_module, monkeypatch, fault, status
):
    path = tmp_path / "report.txt"
    path.write_text("verified report")
    identity = "11111111-2222-4333-8444-555555555555"
    call = {
        "type": "tool_use",
        "id": "tool",
        "name": "SendUserFile",
        "input": {"files": [str(path)]},
    }
    attachment = {"file_uuid": identity, "path": str(path)}
    result = {
        "type": "tool_result",
        "tool_use_id": "tool",
        "content": {"attachments": [None, attachment]},
    }
    if fault == "bad_uuid":
        identity = "bad"
    elif fault == "noncanonical":
        identity = identity.replace("-", "")
    elif fault == "missing_call":
        call["id"] = "other"
    elif fault == "failed_result":
        result["is_error"] = True
    elif fault == "bad_json":
        result["content"] = "{broken"
    elif fault == "wrong_attachment":
        attachment["file_uuid"] = "other"
    elif fault == "empty_path":
        attachment["path"] = ""
    elif fault == "unrequested_path":
        call["input"]["files"] = ["/other/file"]
    elif fault == "bad_files":
        call["input"]["files"] = str(path)
    elif fault == "missing_file":
        path.unlink()
    elif fault == "directory":
        path.unlink()
        path.mkdir()
    elif fault == "oversize":
        monkeypatch.setattr(endpoint_module.broker._settings, "max_presented_file_bytes", 2)
    if isinstance(result["content"], dict):
        result["content"] = json.dumps(result["content"])
    endpoint_module.broker._conversation_turns = [SimpleNamespace(parts=[None, call, result])]
    if status == 200:
        response = await endpoint_module.download_send_user_file("tool", identity)
        assert response.path == path
        assert response.media_type == "text/plain"
    else:
        with pytest.raises(HTTPException) as caught:
            await endpoint_module.download_send_user_file("tool", identity)
        assert caught.value.status_code == status


@pytest.mark.parametrize(
    "fault,status",
    [
        ("ok", 200),
        ("directory", 400),
        ("oversize", 413),
        ("symlink", 400),
        ("prefix", 400),
        ("swap", 400),
        ("swap_prefix", 400),
    ],
)
async def test_raw_upload_confines_initial_and_post_body_destination(
    tmp_path, endpoint_module, fault, status
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    endpoint_module.broker.workspace_dir = str(workspace)
    outside = tmp_path / ("workspace-escape" if "prefix" in fault else "outside")
    outside.mkdir()
    parent = workspace / "uploads"
    parent.mkdir()
    if fault in {"symlink", "prefix"}:
        parent.rmdir()
        parent.symlink_to(outside, target_is_directory=True)
    if fault == "oversize":
        endpoint_module.broker._settings.max_upload_size_bytes = 1

    async def body():
        if fault.startswith("swap"):
            parent.rmdir()
            parent.symlink_to(outside, target_is_directory=True)
        return b"hello"

    request = SimpleNamespace(body=body)
    if status == 200:
        result = await endpoint_module.upload_file_raw(request, "uploads/nested/file.bin")
        assert result["size"] == 5
        assert (workspace / "uploads/nested/file.bin").read_bytes() == b"hello"
    else:
        with pytest.raises(HTTPException) as caught:
            await endpoint_module.upload_file_raw(
                request, "uploads" if fault == "directory" else "uploads/file.bin"
            )
        assert caught.value.status_code == status
        assert not list(outside.iterdir())


@pytest.mark.parametrize(
    "participants,level,query,expected",
    [
        (None, "DEBUG", "", ["peer line", "first", "match", "source hit"]),
        (" peer ", "DEBUG", "", ["peer line"]),
        (None, "WARNING", "", ["peer line", "match", "source hit"]),
        (None, "DEBUG", "MATCH", ["peer line", "match"]),
        ("skuld", "DEBUG", "service", ["peer line", "source hit"]),
        (None, "DEBUG", "skuld", ["peer line", "first", "match", "source hit"]),
        (None, "DEBUG", "absent", ["peer line"]),
    ],
)
async def test_aggregate_log_memory_fallback_filters_without_losing_peer_logs(
    endpoint_module, monkeypatch, participants, level, query, expected
):
    from collections import deque

    monkeypatch.setattr(
        importlib.import_module("skuld.broker_api"),
        "_log_buffer",
        deque(
            [
                {"message": "first", "level": "INFO", "timestamp": 1},
                {"message": "match", "level": "WARNING", "timestamp": 2},
                {"message": "source hit", "level": "ERROR", "logger": "service", "timestamp": None},
            ]
        ),
    )
    monkeypatch.setattr(
        importlib.import_module("skuld.broker_api"),
        "aggregate_workspace_logs",
        lambda *a, **kw: {
            "available_participants": [{"id": "peer"}],
            "lines": [
                {"id": "peer", "timestamp": "1970-01-01T00:00:00+00:00", "message": "peer line"}
            ],
            "total": 1,
            "filtered": 1,
        },
    )
    result = await endpoint_module.get_aggregate_logs(100, level, participants, query)
    assert [line["message"] for line in result["lines"]] == expected
    assert result["total"] == 4
    assert result["available_participants"][0]["id"] == "skuld"
    assert result["session_id"] == endpoint_module.broker.session_id


@pytest.mark.parametrize("decision", [" APPROVE ", "changes_requested"])
@pytest.mark.parametrize("notes", ["", "  fix recovery  "])
async def test_human_review_gate_resolves_once_and_publishes_correlated_outcome(
    tmp_path, decision, notes
):
    from skuld.broker import WorkflowGateState

    b = _broker(tmp_path, api_url="")
    b._mesh_adapter = SimpleNamespace(publish=AsyncMock())
    b.handle_human_room_message = AsyncMock()
    b._finish_trace_span = AsyncMock()
    b._workflow_gate_states["gate"] = WorkflowGateState(
        id="gate",
        node_id="review",
        activation_id="activation",
        label="Review",
        condition="",
        status="pending",
        mode="human_approval",
        pending_behavior="help_needed",
        instructions="",
        auto_forward_after="30m",
        requested_at="now",
        updated_at="now",
        triggered_by_event_type="review.done",
        approval_event_type="review.approved",
        changes_requested_event_type="review.changes",
    )
    result = await b.resolve_workflow_gate("gate", decision, notes=notes, source="ios")
    assert result["decision"] == decision.strip().upper()
    assert result["notes"] == notes.strip()
    event, topic = b._mesh_adapter.publish.call_args.args
    assert event.correlation_id == "activation"
    assert topic == ("review.approved" if decision.strip() == "APPROVE" else "review.changes")
    assert event.payload["fields"]["approved"] == (decision.strip() == "APPROVE")
    assert b.handle_human_room_message.call_args.kwargs["deliver_to_transport"] is False
    with pytest.raises(ValueError, match="already resolved"):
        await b.resolve_workflow_gate("gate", "APPROVE")
    b._mesh_adapter.publish.assert_awaited_once()


@pytest.mark.parametrize(
    "fault,error", [("missing", LookupError), ("decision", ValueError), ("mesh", RuntimeError)]
)
async def test_invalid_review_gate_resolution_never_publishes(tmp_path, fault, error):
    b = _broker(tmp_path, api_url="")
    if fault != "missing":
        b._workflow_gate_states["gate"] = SimpleNamespace(status="pending", mode="human_approval")
    with pytest.raises(error):
        await b.resolve_workflow_gate("gate", "garbage" if fault == "decision" else "APPROVE")
    assert not b._event_log_buffer


@pytest.mark.parametrize(
    "outcome,passed",
    [
        ({"valid": False}, False),
        ({"verdict": "rejected"}, False),
        ({"verdict": "approved"}, True),
        ({"fields": {"approved": False}}, False),
        ({"fields": {"approved": True}}, True),
        ({"fields": {"tests_passing": False}}, False),
        ({"tests_passing": False}, False),
        ({}, True),
    ],
)
def test_workflow_terminal_gating_never_turns_failed_check_into_approval(outcome, passed):
    from skuld.workflow_runtime import _workflow_join_satisfied, _workflow_outcome_passed

    assert _workflow_outcome_passed(outcome) is passed
    assert _workflow_join_satisfied("merge", [outcome, {"verdict": "approved"}]) is passed
    assert _workflow_join_satisfied("any", [outcome, {"verdict": "approved"}])
    assert not _workflow_join_satisfied("all", [])


def test_developer_terminal_requires_its_explicit_verdict() -> None:
    from skuld.workflow_runtime import _workflow_join_satisfied, _workflow_outcome_passed

    required = frozenset({"publish"})
    assert _workflow_outcome_passed(
        {"valid": True, "verdict": "publish"}, passing_verdicts=required
    )
    assert not _workflow_outcome_passed({}, passing_verdicts=required)
    assert not _workflow_outcome_passed(
        {"valid": True, "verdict": "approved"}, passing_verdicts=required
    )
    assert not _workflow_join_satisfied("all", [{"valid": True}], passing_verdicts=required)


def test_workflow_merged_outcome_keeps_all_evidence_and_strictest_scope():
    from skuld.broker import _merge_workflow_terminal_outcomes

    result = _merge_workflow_terminal_outcomes(
        [
            {
                "persona": "reviewer",
                "summary": "code read",
                "files_changed": [" a.py ", "a.py", None, ""],
                "tests_passing": True,
                "scope_adherence": 0.9,
            },
            {
                "event_type": "test.done",
                "summary": "test failed",
                "files_changed": ["b.py"],
                "tests_passing": False,
                "scope_adherence": 0.5,
            },
            {"summary": "manual review"},
            {},
        ]
    )
    assert result["files_changed"] == ["a.py", "b.py"]
    assert result["tests_passing"] is False
    assert result["scope_adherence"] == 0.5
    assert (
        result["summary"] == "reviewer: code read | test.done: test failed | outcome: manual review"
    )
    assert len(result["checks"]) == 4
    assert _merge_workflow_terminal_outcomes([])["summary"] == "Workflow checks passed"


def test_workflow_terminal_preserves_only_explicit_child_result_object():
    from skuld.broker import _merge_workflow_terminal_outcomes

    result = _merge_workflow_terminal_outcomes(
        [
            {
                "persona": "developer-coordinator",
                "fields": {
                    "result": {
                        "attemptId": "attempt-1",
                        "candidateSha": "a" * 40,
                    },
                    "untrustedExtra": "discarded",
                },
            }
        ]
    )

    assert result["result"] == {
        "attemptId": "attempt-1",
        "candidateSha": "a" * 40,
    }
    assert "untrustedExtra" not in result


@pytest.mark.parametrize("terminal", ["response", "error", "task_complete"])
async def test_peer_terminal_closes_nested_tool_spans_even_without_tool_result(tmp_path, terminal):
    b = _broker(tmp_path, api_url="")
    b._room_bridge = SimpleNamespace(
        participants={
            "peer": SimpleNamespace(persona="Reviewer", participant_type="agent", id="peer")
        }
    )
    b._start_trace_span = AsyncMock(side_effect=["turn-span", "tool-span", "second-span"])
    b._finish_trace_span = AsyncMock()
    await b._observe_room_peer_event(
        "peer", "task_started", {"metadata": {"title": "review", "task_id": "task"}}
    )
    await b._observe_room_peer_event(
        "peer",
        "tool_start",
        {"metadata": {"tool_name": "Bash", "input": {"command": "git status"}}},
    )
    await b._observe_room_peer_event(
        "peer", "tool_start", {"metadata": {"tool_name": "Read", "input": {}}}
    )
    await b._observe_room_peer_event("peer", terminal, {})
    assert not b._trace_peer_tool_spans
    assert not b._trace_peer_turn_spans
    assert not b._peer_pending_commands
    calls = b._finish_trace_span.call_args_list
    assert [call.args[0] for call in calls] == ["tool-span", "second-span", "turn-span"]
    assert all(
        c.kwargs["status"] == ("failed" if terminal == "error" else "completed") for c in calls
    )


@pytest.mark.parametrize(
    "wire",
    [
        {"result": '{"summary":"review done","unfinished_work":"approval"}'},
        {"result": '```json\n{"summary":"review done","unfinished_work":"approval"}\n```'},
        {
            "content": [
                None,
                {"type": "text", "text": '{"summary":"review done","unfinished_work":"approval"}'},
            ]
        },
    ],
)
async def test_session_summary_accepts_native_result_shapes(tmp_path, wire):
    b = _broker(tmp_path, api_url="")
    b._transport.last_result = wire
    result = await b._generate_summary()
    assert result["summary"] == "review done"
    assert result["unfinished_work"] == "approval"


@pytest.mark.parametrize("fault", ["offline", "timeout", "exception", "invalid_json"])
async def test_summary_failure_preserves_artifact_fallback(tmp_path, monkeypatch, fault):
    b = _broker(tmp_path, api_url="")
    b._artifacts.files_changed = ["report.md"]
    b._transport.is_alive = fault != "offline"
    if fault == "exception":
        b._transport.send_message.side_effect = RuntimeError("closed")
    b._transport.last_result = None if fault == "timeout" else {"result": "not json"}
    if fault == "timeout":
        # chronicle.py reads the constant; patching the broker's re-export is a no-op.
        monkeypatch.setattr(
            importlib.import_module("skuld.chronicle"), "SUMMARY_TIMEOUT_SECONDS", 0
        )
    result = await b._generate_summary()
    assert result == {"summary": None, "key_changes": ["report.md"], "unfinished_work": None}


@pytest.mark.parametrize(
    "options,expected",
    [
        (
            [{"optionId": "deny", "kind": "reject"}, {"optionId": "allow", "kind": "allow_once"}],
            "allow",
        ),
        ([None, {"id": "native", "kind": "allow_always"}], "native"),
        ([{"id": "fallback", "kind": "future_kind"}], "fallback"),
        ([], None),
        ([None], None),
    ],
)
async def test_grok_always_approve_uses_only_offered_native_option_ids(tmp_path, options, expected):
    from skuld.transports.grok import GrokACPTransport

    t = GrokACPTransport(str(tmp_path))
    t._acp_respond = AsyncMock()
    await t._handle_agent_request(
        {"id": 7, "method": "session/request_permission", "params": {"options": options}}
    )
    reply = t._acp_respond.call_args.kwargs["result"]["outcome"]
    assert reply == (
        {"outcome": "cancelled"}
        if expected is None
        else {"outcome": "selected", "optionId": expected}
    )
    assert t._acp_respond.call_args.args == (7,)


async def test_grok_unsupported_client_request_gets_correlated_refusal_not_silent_hang(tmp_path):
    from skuld.transports.grok import GrokACPTransport

    t = GrokACPTransport(str(tmp_path))
    t._acp_respond = AsyncMock()
    await t._handle_agent_request(
        {"id": 8, "method": "fs/read_text_file", "params": {"path": "/fixture"}}
    )
    t._acp_respond.assert_awaited_once()
    assert t._acp_respond.call_args.args == (8,)
    assert t._acp_respond.call_args.kwargs["error"]["code"] == -32601


@pytest.mark.parametrize(
    "wire_input,expected",
    [("broken json", {"raw": "broken json"}), ('{"command":"pwd"}', {"command": "pwd"})],
)
@pytest.mark.parametrize("wire_output", [{"text": "native output"}, ["native output"]])
async def test_grok_tool_wire_variants_still_pair_one_call_and_result(
    tmp_path, wire_input, expected, wire_output
):
    from skuld.transports.grok import GrokACPTransport

    t = GrokACPTransport(str(tmp_path))
    call = t._map_acp_update(
        {
            "session_update": "toolCall",
            "tool_call_id": "native-tool",
            "name": "shell",
            "rawInput": wire_input,
        }
    )
    assert call["message"]["content"][0]["input"] == expected
    assert (
        t._map_acp_update(
            {"type": "toolCallUpdate", "tool_call_id": "native-tool", "status": "in_progress"}
        )
        is None
    )
    result = t._map_acp_update(
        {
            "type": "toolCallUpdate",
            "tool_call_id": "native-tool",
            "status": "failed",
            "output": wire_output,
        }
    )
    block = result["message"]["content"][0]
    assert result["type"] == "user"
    assert block["tool_use_id"] == call["message"]["content"][0]["id"]
    assert json.loads(block["content"]) == wire_output
    assert block["is_error"] is True
    assert not t._open_tool_calls


@pytest.mark.parametrize(
    "behavior,decision",
    [
        ("deny", "deniedFuture"),
        ("deny", "abortFuture"),
        ("allow", "approvedFuture"),
        ("allowForever", "approvedFuture"),
        ("allow", "unknownFuture"),
        ("deny", "unknownFuture"),
    ],
)
def test_muse_future_approval_vocabulary_does_not_turn_denial_into_allow(behavior, decision):
    from skuld.transports.muse import _pick_choice

    choice = {"choiceId": "server-choice", "decision": decision}
    actual = _pick_choice([None, {}, choice], behavior)
    assert actual == (None if behavior == "deny" and decision == "unknownFuture" else choice)
    assert _pick_choice([], behavior) is None


async def test_muse_native_question_shape_rejection_preserves_answer_as_clarification(tmp_path):
    t = _muse(tmp_path)
    t._pending_user_input = {
        "userInputId": "question",
        "questions": [{"header": "Scope", "question": "Review what?", "options": []}],
    }
    t._command = AsyncMock(
        side_effect=[MuseProtocolError({"data": {"kind": "userInputAnswerInvalid"}}), {}]
    )
    await t._answer_user_input("question", [{"answer": "all changes"}])
    methods = [call.args[0] for call in t._command.call_args_list]
    assert methods == ["userInput/answer", "userInput/clarify"]
    assert "all changes" in t._command.call_args.args[1]["clarification"]["content"]
    assert t._pending_user_input is None
    assert t._emit.call_args.args[0]["accepted"] is True


@pytest.mark.parametrize("fault", ["no_pending", "oversize", "native_error"])
async def test_muse_clarification_cancellation_never_claims_accepted_answer(tmp_path, fault):
    from skuld.transports.muse import _USER_INPUT_TEXT_LIMIT

    t = _muse(tmp_path)
    t._command = AsyncMock()
    if fault != "no_pending":
        t._pending_user_input = {"userInputId": "question", "questions": []}
    if fault == "native_error":
        t._command.side_effect = MuseProtocolError({"data": {"kind": "questionClosed"}})
    text = "x" * (_USER_INPUT_TEXT_LIMIT + 1) if fault == "oversize" else "answer"
    assert not await t._clarify_pending_user_input(text, ("m", "r"))
    assert t._pending_user_input is None
    assert not any(call.args[0].get("accepted") for call in t._emit.call_args_list)


async def test_codex_lost_turn_keeps_originating_request_when_clearing_question(tmp_path):
    from skuld.transports.codex_ws import CodexWebSocketTransport

    t = CodexWebSocketTransport(str(tmp_path))
    events = []

    async def on_event(event):
        events.append(event)

    t.on_event(on_event)
    t._thread_id = "thread"
    t._current_turn_id = "turn"
    t._current_turn_request_id = "user-request"
    t._pending_user_inputs["question-request"] = {"id": 1}
    await t._finalize_stranded_turn("error", "connection closed")
    result = next(frame for frame in events if frame["type"] == "result")
    assert result["turn_id"] == "turn"
    assert result["thread_id"] == "thread"
    assert result["request_id"] == "user-request"
    resolved = next(frame for frame in events if frame["type"] == "ask_user_resolved")
    assert resolved["request_id"] == "question-request"
    assert resolved["accepted"] is False


async def test_health_reports_startup_identity_even_if_source_changes(endpoint_module, monkeypatch):
    before = await endpoint_module.health()
    monkeypatch.setattr(
        importlib.import_module("skuld.broker_api"),
        "build_identity",
        MagicMock(side_effect=AssertionError("rehash")),
    )
    after = await endpoint_module.health()
    assert after["source_sha256"] == before["source_sha256"]
    assert len(after["source_sha256"]) == 64
    assert after["revision"] == before["revision"]


async def test_codex_large_native_tool_result_survives_socket_and_lazy_replay(
    tmp_path, monkeypatch
):
    """Use real local WS framing: mocks would miss websockets' 1 MiB default cap."""
    import tempfile
    from pathlib import Path

    from websockets.asyncio.server import unix_serve

    from skuld.channels import WebSocketChannel
    from skuld.transports.codex_ws import CodexWebSocketTransport

    large_output = "captured output 🧪\n" * 100_000
    frames = [
        {"method": "turn/started", "params": {"threadId": "thread", "turn": {"id": "turn"}}},
        {
            "method": "item/started",
            "params": {
                "threadId": "thread",
                "turnId": "turn",
                "item": {"id": "tool", "type": "commandExecution", "command": "fixture output"},
            },
        },
        {
            "method": "item/completed",
            "params": {
                "threadId": "thread",
                "turnId": "turn",
                "item": {
                    "id": "tool",
                    "type": "commandExecution",
                    "aggregatedOutput": large_output,
                    "exitCode": 0,
                },
            },
        },
        {
            "method": "turn/completed",
            "params": {"threadId": "thread", "turn": {"id": "turn", "status": "completed"}},
        },
    ]
    finished = asyncio.Event()
    release = asyncio.Event()
    browser_frames = []
    b = _broker(tmp_path)
    module = importlib.import_module("skuld.broker")
    monkeypatch.setattr(module, "broker", b)

    async def browser_send(raw):
        frame = json.loads(raw)
        browser_frames.append(frame)
        assert len(raw.encode()) <= b._settings.live_frame_max_bytes
        if frame.get("type") == "result":
            finished.set()

    channel = WebSocketChannel(
        SimpleNamespace(send_text=browser_send), max_frame_bytes=b._settings.live_frame_max_bytes
    )
    b._channels.add(channel)
    t = CodexWebSocketTransport(str(tmp_path))
    t._thread_id = "thread"
    t.on_event(b._handle_cli_event)

    async def native_peer(socket):
        for frame in frames:
            await socket.send(json.dumps(frame, ensure_ascii=False))
        await release.wait()

    with tempfile.TemporaryDirectory(prefix="forge-ws-") as directory:
        path = str(Path(directory) / "native.sock")
        t._codex_socket_path = path
        async with unix_serve(native_peer, path):
            try:
                await t._connect_ws()
                await asyncio.wait_for(finished.wait(), timeout=5)
                fetched = await module.get_tool_result("tool")
                assert fetched["content"] == large_output
                assert len(b._conversation_turns) == 1
                assert t._alive
                assert not any(
                    frame.get("code") == "live_frame_too_large" for frame in browser_frames
                )
                assert any(
                    row["payload"].get("type") == "user"
                    and any(
                        block.get("content") == large_output
                        for block in row["payload"].get("message", {}).get("content", [])
                        if isinstance(block, dict)
                    )
                    for row in b._event_log_buffer
                )
            finally:
                release.set()
                if t._ws:
                    await t._ws.close()
                if t._receive_task:
                    await t._receive_task


@pytest.mark.parametrize("fault", ["missing_path", "dead_process", "exhausted", "warming"])
async def test_codex_connect_reports_startup_failures_and_recovers_warming_socket(
    tmp_path, monkeypatch, fault
):
    from skuld.transports.codex_ws import CodexWebSocketTransport

    module = importlib.import_module("skuld.transports.codex_ws")
    t = CodexWebSocketTransport(str(tmp_path), codex_receive_max_bytes=8 * 1024 * 1024)
    if fault != "missing_path":
        t._codex_socket_path = "/fixture/codex.sock"
    if fault == "dead_process":
        t._process = SimpleNamespace(returncode=3)
    websocket = MagicMock()
    connect = AsyncMock(
        side_effect=[OSError("warming"), websocket]
        if fault == "warming"
        else OSError("not available")
    )
    monkeypatch.setattr(module, "unix_connect", connect)
    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())
    t._receive_loop = AsyncMock()
    if fault == "warming":
        await t._connect_ws()
        await t._receive_task
        assert t._alive
        assert connect.await_count == 2
        assert connect.call_args.kwargs["max_size"] == 8 * 1024 * 1024
    else:
        with pytest.raises(
            RuntimeError,
            match={
                "missing_path": "socket path missing",
                "dead_process": "exited with code 3",
                "exhausted": "after 30 attempts",
            }[fault],
        ):
            await t._connect_ws()
        assert connect.await_count == (30 if fault == "exhausted" else 0)


def test_codex_native_receive_cap_configuration_reaches_constructed_transport(tmp_path):
    b = _broker(tmp_path, api_url="")
    b._settings.transport_adapter = "skuld.transports.codex_ws.CodexWebSocketTransport"
    b._settings.codex_receive_max_bytes = 8 * 1024 * 1024
    t = b._create_transport()
    assert t._receive_max_bytes == 8 * 1024 * 1024
    assert b._settings.live_frame_max_bytes == 900 * 1024


async def test_codex_ambiguous_delivery_needs_exact_consumption_and_replays_request_identity(
    tmp_path,
):
    from niuu.domain.transcript_reducer import reduce_frames
    from niuu.ports.cli import TransportCapabilities
    from tests.test_skuld.test_delivery_failed_steering_state import _frames_from_buffer

    b = _broker(tmp_path)
    b._transport.capabilities = TransportCapabilities(steer=True, steering_mode="live")
    b._transport.is_turn_active = False
    b._transport.send_message.side_effect = TimeoutError("native acknowledgement lost")
    ledger = _Ledger()
    async with ledger.client() as client:
        b._http_client = client
        await b._dispatch_browser_message(
            {"content": "review the recovery", "request_id": "exact-request"}
        )
        await _finish_deliveries()
    user = next(turn for turn in b._conversation_turns if turn.role == "user")
    input_stamp = _events(b, "user")[0]["conversation_timeline"]
    assert user.metadata == {
        "steering_state": "pending",
        "request_id": "exact-request",
        "conversation_timeline": input_stamp,
    }
    for frame in [
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "old turn reply"}]}},
        {"type": "error", "content": "old transport error"},
        {"type": "result", "result": "old completion"},
    ]:
        await b._handle_cli_event(frame)
        assert user.metadata["steering_state"] == "pending"
    assert not _events(b, "user_active")
    await b._handle_cli_event(
        {"type": "user_consumed", "msg_id": user.id, "request_id": "exact-request"}
    )
    expected = {
        "steering_state": "active",
        "request_id": "exact-request",
        "conversation_timeline": input_stamp,
        "steering_accepted_at": _events(b, "user_active")[-1]["accepted_at"],
    }
    assert user.metadata == expected
    stored = json.loads(b._conversation_history_path().read_text())
    assert next(turn for turn in stored["turns"] if turn["role"] == "user")["metadata"] == expected
    rebuilt = reduce_frames(_frames_from_buffer(b)).turns
    assert next(turn for turn in rebuilt if turn["role"] == "user")["metadata"] == expected
    assert _events(b, "user_active")[-1]["request_id"] == "exact-request"
    # Consumed history is evidence for iOS; settlement remains an independent durable fact.
    assert ledger.rows["exact-request"]["status"] == "pending"


@pytest.mark.parametrize(
    "resume,expected",
    [
        ("8624870b-b45d-4a40-a89e-8432964ebcfc", "8624870b-b45d-4a40-a89e-8432964ebcfc"),
        (" 8624870B-B45D-4A40-A89E-8432964EBCFC ", "8624870b-b45d-4a40-a89e-8432964ebcfc"),
        ("", None),
        ("skuld-11111111-2222-4333-8444-555555555555", None),
    ],
)
async def test_tmux_resume_identity_survives_idle_heartbeat_before_first_prompt(
    tmp_path, resume, expected
):
    import httpx

    from skuld.broker import Broker

    t = TmuxInteractiveTransport(
        str(tmp_path), session_id="forge-session", resume_session_id=resume
    )
    assert t.session_id == expected
    b = _broker(tmp_path)
    b._transport = t
    posts = []

    def handle(request):
        posts.append(json.loads(request.content))
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(
        base_url="http://forge.test", transport=httpx.MockTransport(handle)
    ) as client:
        b._http_client = client
        await Broker._report_activity_state(b, "idle", extra_metadata={"heartbeat": True})
        await Broker._report_activity_state(b, "idle", extra_metadata={"heartbeat": True})
    assert len(posts) == 2
    for post in posts:
        assert post["metadata"].get("cli_session_id") == expected
        assert post["metadata"].get("cli_session_id") != t._session_name
    assert t.session_id == expected
