"""Real transport normalization → persisted usage reducer contract, offline."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from claude_agent_sdk.types import AssistantMessage, ResultMessage, StreamEvent, SystemMessage

from skuld.transports.codex_ws import CodexWebSocketTransport
from skuld.transports.sdk import _to_stream_json
from skuld.transports.tmux_interactive import TmuxInteractiveTransport
from volundr.domain.session_usage import fold_usage, observation, projection_key

NATIVE = "20000000-0000-4000-8000-000000000001"
AT = datetime(2026, 10, 23, tzinfo=UTC)
USAGE = {
    "input_tokens": 2,
    "cache_read_input_tokens": 9779,
    "cache_creation_input_tokens": 10211,
    "output_tokens": 236,
}


@pytest.fixture(autouse=True)
def no_provider_processes(monkeypatch):
    for name in ("create_subprocess_exec", "create_subprocess_shell"):
        monkeypatch.setattr(
            asyncio, name, AsyncMock(side_effect=AssertionError("Unexpected provider process"))
        )


class Projection:
    def __init__(self):
        self.state, self.identities, self.seq = None, {}, 0

    def feed(self, frame):
        self.seq += 1
        event = observation(frame, seq=self.seq, at=AT + timedelta(seconds=self.seq))
        if event is None:
            return self.state
        key = projection_key(event, self.state)
        self.state, self.identities[key] = fold_usage(
            self.state, event, self.identities.get(key), model_windows={"claude-opus-4-8": 1000000}
        )
        return self.state


def _claude_row(identity="msg_1", usage=None, **extra):
    return {
        "type": "assistant",
        "sessionId": NATIVE,
        "uuid": f"row-{identity}",
        "timestamp": (AT + timedelta(seconds=1)).isoformat(),
        "message": {
            "id": identity,
            "model": "claude-opus-4-8",
            "usage": usage or USAGE,
            "content": [],
        },
        **extra,
    }


def test_sdk_stream_and_complete_response_share_one_usage_identity():
    projection = Projection()
    start = _to_stream_json(
        StreamEvent(
            uuid="stream-1",
            session_id=NATIVE,
            event={
                "type": "message_start",
                "message": {
                    "id": "msg_1",
                    "model": "claude-opus-4-8",
                    "usage": {**USAGE, "output_tokens": 1},
                },
            },
        )
    )
    final = _to_stream_json(
        AssistantMessage(
            content=[],
            model="claude-opus-4-8",
            usage=USAGE,
            message_id="msg_1",
            session_id=NATIVE,
            uuid="response-final",
            stop_reason="end_turn",
        )
    )
    projection.feed(start)
    projection.feed(final)
    projection.feed(start)
    assert projection.state["totals"] == {
        "input": 2,
        "cache_read": 9779,
        "cache_write": 10211,
        "output": 236,
    }
    assert projection.state["context"] == {"tokens": 19992, "window": 1000000, "share": 0.019992}
    child = _to_stream_json(
        AssistantMessage(
            content=[],
            model="claude-opus-4-8",
            usage=USAGE,
            message_id="child",
            session_id=NATIVE,
            parent_tool_use_id="task-1",
        )
    )
    projection.feed(child)
    assert projection.state["totals"]["input"] == 2
    result = _to_stream_json(
        ResultMessage(
            subtype="success",
            duration_ms=100,
            duration_api_ms=90,
            is_error=False,
            num_turns=1,
            session_id=NATIVE,
            total_cost_usd=0.5,
            model_usage={"claude-opus-4-8": {"contextWindow": 200000, "costUSD": 0.5}},
        )
    )
    projection.feed(result)
    projection.feed(result)
    assert projection.state["cost_estimate"] == 0.5
    assert projection.state["context"]["window"] == 200000
    boundary = _to_stream_json(
        SystemMessage(
            subtype="compact_boundary",
            data={
                "type": "system",
                "subtype": "compact_boundary",
                "session_id": NATIVE,
                "uuid": "compact-1",
            },
        )
    )
    projection.feed(boundary)
    projection.feed(boundary)
    assert projection.state["compactions"] == 1
    assert projection.state["context"]["tokens"] is None


async def _tmux_frames(tmp_path, rows):
    path = tmp_path / f"{NATIVE}.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    transport = TmuxInteractiveTransport(str(tmp_path), resume_session_id=NATIVE)
    transport._run_tmux = AsyncMock(side_effect=AssertionError("No TTY reads for metrics"))
    frames = []

    async def emit(frame):
        frames.append(frame)

    transport.on_event(emit)
    await transport.handle_claude_hook(
        {
            "hook_event_name": "Notification",
            "session_id": NATIVE,
            "transcript_path": str(path),
        }
    )
    await transport._native_usage_task
    transport._run_tmux.assert_not_awaited()
    return frames


async def test_tmux_native_history_and_compaction_replay_without_duplicate_totals(tmp_path):
    frames = await _tmux_frames(
        tmp_path,
        [
            _claude_row(usage={**USAGE, "output_tokens": 1}),
            _claude_row(),
            _claude_row("child", isSidechain=True),
            {
                "type": "system",
                "subtype": "compact_boundary",
                "sessionId": NATIVE,
                "uuid": "compact-1",
                "timestamp": (AT + timedelta(seconds=2)).isoformat(),
            },
            _claude_row(
                "msg_2",
                usage={
                    "input_tokens": 30,
                    "cache_read_input_tokens": 10,
                    "cache_creation_input_tokens": 0,
                    "output_tokens": 9,
                },
                timestamp=(AT + timedelta(seconds=3)).isoformat(),
            ),
        ],
    )
    projection = Projection()
    for frame in [*frames, *frames]:
        projection.feed(frame)
    assert projection.state["totals"] == {
        "input": 32,
        "cache_read": 9789,
        "cache_write": 10211,
        "output": 245,
    }
    assert projection.state["context"]["tokens"] == 40
    assert projection.state["usage_available"] is True
    assert projection.state["compactions"] == 1


async def test_tmux_partial_counter_row_cannot_manufacture_zero_context(tmp_path):
    frames = await _tmux_frames(tmp_path, [_claude_row(usage={"output_tokens": 3})])
    numeric = [frame for frame in frames if frame.get("subtype") == "claude_usage"]
    assert numeric
    assert all(frame["message"]["usage"] == {"output_tokens": 3} for frame in numeric)
    projection = Projection()
    for frame in frames:
        projection.feed(frame)
    assert projection.state is None


async def test_reviewed_codex_corpus_normalizes_into_exact_durable_context_and_totals(tmp_path):
    corpus = Path(__file__).parents[1] / "fixtures/forge-corpus/codex-agents-da06fa93af.frames.json"
    samples = [
        frame["payload"]["payload"]
        for frame in json.loads(corpus.read_text())
        if frame["payload"].get("method") == "thread/tokenUsage/updated"
    ]
    transport = CodexWebSocketTransport(str(tmp_path), model="gpt-6-astra")
    transport._thread_id, transport._current_turn_id = samples[0]["threadId"], samples[0]["turnId"]
    projection = Projection()
    emitted = []

    async def emit(frame):
        emitted.append(frame)
        projection.feed(frame)

    transport.on_event(emit)
    for sample in samples:  # Other native threads must remain excluded.
        await transport._handle_server_message(
            {"method": "thread/tokenUsage/updated", "params": sample}
        )
    snapshots = [frame for frame in emitted if frame["type"] == "session_usage"]
    assert len(snapshots) == 3
    projection.feed(snapshots[-1])
    assert projection.state["totals"] == {
        "input": 4835,
        "cache_read": 31616,
        "cache_write": 0,
        "output": 250,
    }
    assert projection.state["context"] == {
        "tokens": 12291,
        "window": 258400,
        "share": 12291 / 258400,
    }


async def test_codex_last_without_total_supplies_context_without_lifetime_estimate(tmp_path):
    transport = CodexWebSocketTransport(str(tmp_path), model="gpt-6-astra")
    transport._thread_id, transport._current_turn_id = "thread", "turn"
    projection = Projection()

    async def emit(frame):
        projection.feed(frame)

    transport.on_event(emit)
    await transport._handle_server_message(
        {
            "method": "thread/tokenUsage/updated",
            "params": {
                "threadId": "thread",
                "turnId": "turn",
                "tokenUsage": {
                    "last": {"inputTokens": 100, "cachedInputTokens": 40, "outputTokens": 10},
                    "modelContextWindow": 258400,
                },
            },
        }
    )
    assert projection.state["context"]["tokens"] == 100
    assert projection.state["context"]["window"] == 258400
    assert "totals" not in projection.state
