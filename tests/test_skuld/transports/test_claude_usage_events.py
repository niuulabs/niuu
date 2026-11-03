"""Native Claude usage evidence, without a provider or terminal process."""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from claude_agent_sdk.types import (
    AssistantMessage,
    ConversationResetMessage,
    ResultMessage,
    StreamEvent,
    SystemMessage,
)

from skuld.transports.sdk import _to_stream_json
from skuld.transports.tmux_interactive import TmuxInteractiveTransport, _TmuxResult

NATIVE = "20000000-0000-4000-8000-000000000001"
FOREIGN = "30000000-0000-4000-8000-000000000001"


def _native_row(message_id="msg_1", output=236, **extra):
    return {
        "type": "assistant",
        "sessionId": NATIVE,
        "uuid": f"row-{message_id}-{output}",
        "timestamp": "2026-10-23T12:00:00Z",
        "message": {
            "id": message_id,
            "model": "claude-opus-4-8",
            "content": [{"type": "thinking", "thinking": "PRIVATE_REASONING"}],
            "usage": {
                "input_tokens": 2,
                "cache_read_input_tokens": 9779,
                "cache_creation_input_tokens": 10211,
                "output_tokens": output,
            },
        },
        **extra,
    }


def _jsonl(rows):
    return "".join(json.dumps(row) + "\n" for row in rows)


def _transport(tmp_path, **kwargs):
    transport = TmuxInteractiveTransport(str(tmp_path), resume_session_id=NATIVE, **kwargs)
    # Any accidental terminal interaction is a hard failure in these tests.
    transport._run_tmux = AsyncMock(side_effect=AssertionError("No TTY reads for metrics"))
    events = []

    async def collect(event):
        events.append(event)

    transport.on_event(collect)
    return transport, events


async def _hook(transport, path, **extra):
    await transport.handle_claude_hook(
        {
            "hook_event_name": "Notification",
            "session_id": NATIVE,
            "transcript_path": str(path),
            **extra,
        }
    )
    task = transport._native_usage_task
    if task is not None:
        await task


def _metrics(events):
    return [event for event in events if event.get("subtype") == "claude_usage"]


def test_sdk_preserves_native_response_identity_and_cache_components():
    usage = {
        "input_tokens": 2,
        "cache_read_input_tokens": 9779,
        "cache_creation_input_tokens": 10211,
        "output_tokens": 236,
    }
    start = _to_stream_json(
        StreamEvent(
            uuid="event-start",
            session_id="native-session",
            event={
                "type": "message_start",
                "message": {"id": "msg_1", "model": "claude-opus-4-8", "usage": usage},
            },
        )
    )
    final = _to_stream_json(
        AssistantMessage(
            content=[],
            model="claude-opus-4-8",
            usage=usage,
            message_id="msg_1",
            session_id="native-session",
            uuid="event-final",
            stop_reason="end_turn",
        )
    )
    assert start["message"]["id"] == final["message"]["id"] == "msg_1"
    assert start["uuid"] != final["uuid"]
    assert start["message"]["usage"] == final["message"]["usage"] == usage
    assert final["session_id"] == "native-session"


def test_sdk_keeps_model_window_cost_and_aggregate_usage_without_deriving_context():
    models = {
        "claude-opus-4-8": {
            "inputTokens": 14,
            "outputTokens": 300,
            "cacheReadInputTokens": 50000,
            "cacheCreationInputTokens": 20000,
            "webSearchRequests": 0,
            "costUSD": 0.15,
            "contextWindow": 1000000,
            "maxOutputTokens": 64000,
        }
    }
    event = _to_stream_json(
        ResultMessage(
            subtype="success",
            duration_ms=100,
            duration_api_ms=90,
            is_error=False,
            num_turns=3,
            session_id="native-session",
            total_cost_usd=0.15,
            usage={"input_tokens": 14, "output_tokens": 300},
            model_usage=models,
            uuid="result-1",
        )
    )
    assert event["modelUsage"] == models
    assert event["total_cost_usd"] == 0.15
    assert event["usage"] == {"input_tokens": 14, "output_tokens": 300}
    assert "context" not in event


def test_sdk_preserves_compaction_and_reset_as_distinct_native_events():
    boundary = {
        "type": "system",
        "subtype": "compact_boundary",
        "session_id": "native-session",
        "uuid": "compact-1",
        "compact_metadata": {"trigger": "auto", "pre_tokens": 190000},
    }
    assert _to_stream_json(SystemMessage(subtype="compact_boundary", data=boundary)) == boundary
    assert _to_stream_json(
        ConversationResetMessage(
            new_conversation_id="new-conversation", uuid="reset-1", session_id="native-session"
        )
    ) == {
        "type": "conversation_reset",
        "new_conversation_id": "new-conversation",
        "session_id": "native-session",
        "uuid": "reset-1",
    }


def test_sdk_retains_child_identity_for_main_session_projection_to_exclude():
    event = _to_stream_json(
        AssistantMessage(
            content=[],
            model="claude-opus-4-8",
            usage={"input_tokens": 42},
            message_id="child-message",
            session_id="native-session",
            parent_tool_use_id="task-1",
        )
    )
    assert event["parent_tool_use_id"] == "task-1"


async def test_tmux_native_usage_is_numeric_only_incremental_and_main_only(tmp_path):
    path = tmp_path / f"{NATIVE}.jsonl"
    path.write_text(
        _jsonl(
            [
                _native_row(output=1),
                _native_row(output=236),
                _native_row("child", isSidechain=True),
                _native_row("foreign", sessionId=FOREIGN),
                _native_row("agent", agentId="child-agent"),
                _native_row("nested", parent_tool_use_id="task-1"),
            ]
        )
    )
    transport, events = _transport(tmp_path)
    await _hook(transport, path)
    metrics = _metrics(events)
    assert [event["message"]["usage"]["output_tokens"] for event in metrics] == [1, 236, 236]
    assert [event["caught_up"] for event in metrics] == [False, False, True]
    assert {event["message"]["id"] for event in metrics} == {"msg_1"}
    assert metrics[-1]["timestamp"] == "2026-10-23T12:00:00Z"
    assert metrics[-1]["message"]["usage"]["cache_read_input_tokens"] == 9779
    assert "PRIVATE" not in json.dumps(metrics)
    assert "transcript_path" not in json.dumps(metrics)
    assert all(event["type"] == "system" for event in metrics)
    await _hook(transport, path)
    assert _metrics(events) == metrics  # No reread or idle snapshot spam.
    transport._run_tmux.assert_not_awaited()


async def test_tmux_single_hook_drains_multi_megabyte_history_in_bounded_chunks(tmp_path):
    path = tmp_path / f"{NATIVE}.jsonl"
    path.write_text(_jsonl([_native_row(f"msg_{i}", padding="x" * 1024) for i in range(2000)]))
    assert path.stat().st_size > 2 * 1024 * 1024
    transport, events = _transport(tmp_path, question_transcript_max_bytes=4096)
    original = transport._read_native_usage_chunk
    read_sizes = []

    def read(state):
        result = original(state)
        read_sizes.append(len(result[0]))
        return result

    transport._read_native_usage_chunk = read
    await _hook(transport, path)
    metrics = _metrics(events)
    assert len(metrics) == 2001
    assert all(not event["caught_up"] for event in metrics[:-1])
    assert metrics[-1]["caught_up"] is True
    assert metrics[-1]["message"]["id"] == "msg_1999"
    assert max(read_sizes) <= 4096
    assert len(read_sizes) > 2
    assert transport._native_usage_tail.offset == path.stat().st_size


async def test_tmux_torn_native_row_waits_for_a_later_hook_and_complete_line(tmp_path):
    path = tmp_path / f"{NATIVE}.jsonl"
    row = _jsonl([_native_row()])
    path.write_text(row[:100])
    transport, events = _transport(tmp_path)
    await _hook(transport, path)
    assert _metrics(events) == []
    assert events[-1]["reason"] == "incomplete_record"
    with path.open("a") as stream:
        stream.write(row[100:])
    await _hook(transport, path)
    assert len(_metrics(events)) == 2
    assert _metrics(events)[-1]["caught_up"] is True


@pytest.mark.parametrize(
    "extra",
    [
        {"session_id": FOREIGN},
        {"agent_id": "child"},
        {"is_sidechain": True},
    ],
)
async def test_tmux_child_or_foreign_hook_cannot_supply_main_usage(tmp_path, extra):
    path = tmp_path / f"{NATIVE}.jsonl"
    path.write_text(_jsonl([_native_row()]))
    transport, events = _transport(tmp_path)
    await _hook(transport, path, **extra)
    assert transport._native_usage_task is None
    assert _metrics(events) == []


async def test_tmux_compaction_boundary_never_republishes_pre_compaction_context(tmp_path):
    path = tmp_path / f"{NATIVE}.jsonl"
    path.write_text(
        _jsonl(
            [
                _native_row(),
                {
                    "type": "system",
                    "subtype": "compact_boundary",
                    "sessionId": NATIVE,
                    "uuid": "compact-1",
                    "timestamp": "2026-10-23T12:01:00Z",
                    "compactMetadata": {
                        "trigger": "auto",
                        "preTokens": 190000,
                        "content": "PRIVATE_SUMMARY",
                    },
                },
            ]
        )
    )
    transport, events = _transport(tmp_path)
    await _hook(transport, path, hook_event_name="PostCompact")
    boundary = [e for e in events if e.get("subtype") == "compact_boundary"]
    assert len(boundary) == 1
    assert boundary[0]["uuid"] == "compact-1"
    assert boundary[0]["compact_metadata"] == {"trigger": "auto", "preTokens": 190000}
    assert "PRIVATE" not in json.dumps(boundary)
    assert not any(event["caught_up"] for event in _metrics(events))
    with path.open("a") as stream:
        stream.write(_jsonl([_native_row("after-compact", output=10)]))
    await _hook(transport, path)
    assert _metrics(events)[-1]["message"]["id"] == "after-compact"
    assert _metrics(events)[-1]["caught_up"] is True


async def test_tmux_oversized_or_malformed_rows_report_a_gap_then_recover(tmp_path):
    path = tmp_path / f"{NATIVE}.jsonl"
    path.write_text("{bad json}\n" + "x" * 2000 + "\n" + _jsonl([_native_row()]))
    transport, events = _transport(tmp_path, question_transcript_max_bytes=512)
    await _hook(transport, path)
    reasons = [e["reason"] for e in events if e.get("subtype") == "claude_usage_unavailable"]
    assert "invalid_record" in reasons
    assert "record_too_large" in reasons
    assert _metrics(events)[-1]["caught_up"] is True
    assert _metrics(events)[-1]["message"]["id"] == "msg_1"


async def test_tmux_replaced_transcript_is_explicit_then_replays_native_ids(tmp_path):
    path = tmp_path / f"{NATIVE}.jsonl"
    path.write_text(_jsonl([_native_row()]))
    transport, events = _transport(tmp_path)
    await _hook(transport, path)
    replacement = tmp_path / "replacement"
    replacement.write_text(_jsonl([_native_row(), _native_row("later")]))
    replacement.replace(path)
    await _hook(transport, path)
    assert any(e.get("reason") == "transcript_replaced" for e in events)
    assert _metrics(events)[-1]["message"]["id"] == "later"


async def test_tmux_read_failure_is_unknown_without_paths_or_zero_estimates(tmp_path):
    path = tmp_path / f"{NATIVE}.jsonl"
    transport, events = _transport(tmp_path)
    await _hook(transport, path)
    assert _metrics(events) == []
    assert events[-1] == {
        "type": "system",
        "subtype": "claude_usage_unavailable",
        "session_id": NATIVE,
        "reason": "transcript_unreadable",
        "metadata": {"source": "claude_native_transcript"},
    }


async def test_tmux_overlapping_hooks_coalesce_one_drain_task(tmp_path):
    path = tmp_path / f"{NATIVE}.jsonl"
    path.write_text(_jsonl([_native_row()]))
    transport, events = _transport(tmp_path)
    original = transport._read_native_usage_chunk
    gate = asyncio.Event()
    entered = asyncio.Event()

    async def blocked_read(_func, state):
        entered.set()
        await gate.wait()
        return original(state)

    # Block the task before its first read, without any process or terminal.
    with patch("skuld.transports.tmux_interactive.asyncio.to_thread", blocked_read):
        payload = {"session_id": NATIVE, "transcript_path": str(path)}
        transport._schedule_native_usage(payload)
        first = transport._native_usage_task
        await entered.wait()
        transport._schedule_native_usage(payload)
        assert transport._native_usage_task is first
        gate.set()
        await first
    assert len(_metrics(events)) == 2


@pytest.mark.parametrize("kind", ["symlink", "directory"])
async def test_tmux_nonregular_or_symlink_transcripts_are_not_read(tmp_path, kind):
    path = tmp_path / f"{NATIVE}.jsonl"
    if kind == "symlink":
        target = tmp_path / "private-transcript"
        target.write_text(_jsonl([_native_row()]))
        path.symlink_to(target)
    else:
        path.mkdir()
    transport, events = _transport(tmp_path)
    await _hook(transport, path)
    assert _metrics(events) == []
    assert events[-1]["reason"] == "transcript_unreadable"


async def test_tmux_stop_cancels_the_hook_triggered_drain(tmp_path):
    transport, events = _transport(tmp_path)
    entered = asyncio.Event()

    async def blocked_read(_func, _state):
        entered.set()
        await asyncio.Event().wait()

    with patch("skuld.transports.tmux_interactive.asyncio.to_thread", blocked_read):
        transport._schedule_native_usage(
            {"session_id": NATIVE, "transcript_path": str(tmp_path / f"{NATIVE}.jsonl")}
        )
        task = transport._native_usage_task
        await entered.wait()
        transport._run_tmux = AsyncMock(return_value=_TmuxResult(0))
        with (
            patch.object(transport, "_graceful_terminate_pane", AsyncMock()),
            patch.object(transport, "_cleanup_socket"),
            patch.object(transport, "_cleanup_runtime_dir"),
        ):
            await transport.stop()
    assert task.cancelled()
    assert transport._native_usage_task is None
    assert _metrics(events) == []


async def test_tmux_new_main_identity_cancels_old_transcript_before_publishing(tmp_path):
    transport, events = _transport(tmp_path)
    entered = asyncio.Event()
    original = transport._read_native_usage_chunk
    new_path = tmp_path / f"{FOREIGN}.jsonl"
    new_path.write_text(_jsonl([_native_row("new-session", sessionId=FOREIGN)]))

    async def blocked_old_read(_func, state):
        if state.session_id == NATIVE:
            entered.set()
            await asyncio.Event().wait()
        return original(state)

    with patch("skuld.transports.tmux_interactive.asyncio.to_thread", blocked_old_read):
        transport._schedule_native_usage(
            {"session_id": NATIVE, "transcript_path": str(tmp_path / f"{NATIVE}.jsonl")}
        )
        old_task = transport._native_usage_task
        await entered.wait()
        transport._claude_native_session_id = FOREIGN
        transport._schedule_native_usage({"session_id": FOREIGN, "transcript_path": str(new_path)})
        await transport._native_usage_task
    assert old_task.cancelled()
    assert {event["session_id"] for event in _metrics(events)} == {FOREIGN}


async def test_tmux_hook_during_final_snapshot_does_not_lose_the_appended_response(tmp_path):
    path = tmp_path / f"{NATIVE}.jsonl"
    path.write_text(_jsonl([_native_row()]))
    transport, events = _transport(tmp_path)
    final_started = asyncio.Event()
    release = asyncio.Event()

    async def collect(event):
        events.append(event)
        if event.get("caught_up") and not final_started.is_set():
            final_started.set()
            await release.wait()

    transport.on_event(collect)
    payload = {"session_id": NATIVE, "transcript_path": str(path)}
    transport._schedule_native_usage(payload)
    await final_started.wait()
    with path.open("a") as stream:
        stream.write(_jsonl([_native_row("arrived-during-emit")]))
    transport._schedule_native_usage(payload)
    release.set()
    await transport._native_usage_task
    assert _metrics(events)[-1]["message"]["id"] == "arrived-during-emit"
    assert _metrics(events)[-1]["caught_up"] is True


async def test_tmux_failed_delivery_does_not_advance_past_unemitted_native_rows(tmp_path):
    path = tmp_path / f"{NATIVE}.jsonl"
    path.write_text(_jsonl([_native_row("first"), _native_row("second")]))
    transport, events = _transport(tmp_path)
    failed = False

    async def collect(event):
        nonlocal failed
        if event.get("message", {}).get("id") == "second" and not failed:
            failed = True
            raise RuntimeError("broker append failed")
        events.append(event)

    transport.on_event(collect)
    with pytest.raises(RuntimeError, match="broker append failed"):
        await _hook(transport, path)
    assert transport._native_usage_tail.offset == 0
    await _hook(transport, path)
    assert [event["message"]["id"] for event in _metrics(events)] == [
        "first",
        "first",
        "second",
        "second",
    ]
    assert _metrics(events)[-1]["caught_up"] is True
    assert transport._native_usage_tail.offset == path.stat().st_size
