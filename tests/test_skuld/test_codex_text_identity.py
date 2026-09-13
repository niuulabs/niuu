"""Native text items retain exact bytes and identity through Codex normalization."""

import hashlib
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from niuu.domain.transcript_reducer import reduce_frames
from skuld.channels import WebSocketChannel
from skuld.transports.codex import CodexSubprocessTransport
from skuld.transports.codex_ws import CodexWebSocketTransport


class Probe:
    def __init__(self, tmp_path):
        self.transport = CodexWebSocketTransport(str(tmp_path), model="gpt-6-astra")
        self.transport._thread_id = "thread-a"
        self.events = []
        self.transport.on_event(self.capture)
        self.turn = "turn-a"

    async def capture(self, event):
        self.events.append(event)

    async def send(self, method, **params):
        await self.transport._handle_server_message(
            {
                "method": method,
                "params": {"threadId": "thread-a", "turnId": self.turn, **params},
            }
        )

    async def start_turn(self, turn="turn-a"):
        self.turn = turn
        await self.send("turn/started", turn={"id": turn})

    async def start(self, identifier, *, phase="commentary"):
        await self.send(
            "item/started",
            item={"type": "agentMessage", "id": identifier, "text": "", "phase": phase},
        )

    async def delta(self, identifier, text):
        await self.send("item/agentMessage/delta", itemId=identifier, delta=text)

    async def complete(self, identifier, text, *, phase="commentary"):
        await self.send(
            "item/completed",
            item={"type": "agentMessage", "id": identifier, "text": text, "phase": phase},
        )

    def text_completions(self):
        return [
            block
            for event in self.events
            if event["type"] == "assistant"
            for block in event.get("message", {}).get("content", [])
            if block.get("type") == "text"
        ]

    def turns(self):
        return reduce_frames(
            [
                SimpleNamespace(
                    session_id="test-session",
                    seq=index + 1,
                    kind=event["type"],
                    payload=event,
                    request_id=None,
                    ts=datetime.now(UTC),
                )
                for index, event in enumerate(self.events)
            ]
        ).turns


@pytest.mark.parametrize("width", [1, 2, 7, 1000])
async def test_exact_unicode_markdown_chunks_and_native_identity(tmp_path, width):
    probe = Probe(tmp_path)
    await probe.start_turn()
    await probe.start("message-a")
    text = "Café 東京.\n\n```python\nprint('α')\n\nprint('β')\n```\n"
    chunks = [text[index : index + width] for index in range(0, len(text), width)]
    for chunk in chunks:
        await probe.delta("message-a", chunk)
    await probe.complete("message-a", text)
    start = next(event for event in probe.events if event["type"] == "content_block_start")
    assert start["item_id"] == "message-a"
    assert start["content_block"]["phase"] == "commentary"
    assert start["content_block"]["complete"] is False
    deltas = [event for event in probe.events if event["type"] == "content_block_delta"]
    assert [event["delta"]["text"] for event in deltas] == chunks
    assert all(
        event["item_id"] == "message-a" and event["index"] == start["index"] for event in deltas
    )
    complete = probe.text_completions()
    assert len(complete) == 1
    assert complete[0]["text"] == text
    assert complete[0]["id"] == "message-a" and complete[0]["complete"] is True
    assert complete[0]["turn_id"] == "turn-a" and complete[0]["thread_id"] == "thread-a"
    assert complete[0]["id_source"] == "native"
    assert probe.events[-1]["item_id"] == "message-a"
    assert probe.events[-1]["index"] == start["index"]
    assert probe.events[-1]["text_bytes"] == len(text.encode("utf-8"))
    assert probe.events[-1]["text_sha256"] == hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.mark.parametrize("prefix", ["", "Partial "])
async def test_completion_only_partial_and_duplicate_completion(tmp_path, prefix):
    probe = Probe(tmp_path)
    await probe.start_turn()
    if prefix:
        await probe.start("final", phase="final_answer")
        await probe.delta("final", prefix)
    text = "Partial complete answer."
    await probe.complete("final", text, phase="final_answer")
    await probe.complete("final", text, phase="final_answer")
    await probe.delta("final", "stale duplicate suffix")
    assert len(probe.text_completions()) == 1
    assert probe.text_completions()[0]["text"] == text
    assert probe.text_completions()[0]["phase"] == "final_answer"
    assert sum(event["type"] == "content_block_start" for event in probe.events) == 1
    assert not any(
        event.get("delta", {}).get("text") == "stale duplicate suffix" for event in probe.events
    )


async def test_interleaving_adjacent_items_and_same_tool_refresh_keep_order(tmp_path):
    probe = Probe(tmp_path)
    await probe.start_turn()
    await probe.start("a")
    await probe.delta("a", "Checking.")
    await probe.complete("a", "Checking.")
    await probe.send("item/started", item={"type": "webSearch", "id": "search-1", "query": ""})
    await probe.start("b")
    await probe.delta("b", "I found it.")
    await probe.complete("b", "I found it.")
    await probe.send(
        "item/completed",
        item={
            "type": "webSearch",
            "id": "search-1",
            "action": {"type": "search", "query": "fixture"},
            "results": [{"title": "Fixture", "url": "https://example.test/fixture"}],
        },
    )
    await probe.start("final", phase="final_answer")
    await probe.delta("final", "Done.")
    await probe.complete("final", "Done.", phase="final_answer")
    await probe.send("turn/completed", turn={"id": probe.turn, "status": "completed"})
    turns = probe.turns()
    assert len(turns) == 1
    assert [
        (part["type"], part.get("id"))
        for part in turns[0]["parts"]
        if part["type"] != "tool_result"
    ] == [
        ("text", "a"),
        ("tool_use", "search-1"),
        ("text", "b"),
        ("text", "final"),
    ]
    assert [part["text"] for part in turns[0]["parts"] if part["type"] == "text"] == [
        "Checking.",
        "I found it.",
        "Done.",
    ]
    assert turns[0]["content"] == "Checking.\n\nI found it.\n\nDone."


async def test_parallel_text_items_stop_and_delta_target_native_id(tmp_path):
    probe = Probe(tmp_path)
    await probe.start_turn()
    await probe.start("a")
    await probe.start("b")
    await probe.delta("a", "A")
    await probe.delta("b", "B")
    await probe.complete("a", "A")
    await probe.complete("b", "B")
    starts = {
        event["item_id"]: event["index"]
        for event in probe.events
        if event["type"] == "content_block_start"
    }
    assert starts["a"] != starts["b"]
    assert all(
        event["index"] == starts[event["item_id"]]
        for event in probe.events
        if event["type"] in {"content_block_delta", "content_block_stop"}
    )


async def test_private_analysis_text_is_not_normalized(tmp_path):
    probe = Probe(tmp_path)
    await probe.start_turn()
    await probe.start("private", phase="analysis")
    await probe.delta("private", "PRIVATE_ANALYSIS")
    await probe.complete("private", "PRIVATE_ANALYSIS", phase="analysis")
    assert not probe.text_completions()
    assert not any(event.get("delta", {}).get("text") for event in probe.events)


async def test_missing_native_identity_is_explicitly_synthetic_and_stable(tmp_path):
    probe = Probe(tmp_path)
    await probe.start_turn()
    await probe.start(None)
    await probe.delta(None, "Anonymous text")
    await probe.complete(None, "Anonymous text")
    block = probe.text_completions()[0]
    assert block["id"] and block["id_source"] == "synthetic"
    assert all(
        event["item_id"] == block["id"]
        for event in probe.events
        if event["type"].startswith("content_block_")
    )


async def test_reused_item_id_is_scoped_to_next_turn_and_old_delta_rejected(tmp_path):
    probe = Probe(tmp_path)
    await probe.start_turn()
    await probe.complete("item_0", "First.")
    await probe.send("turn/completed", turn={"id": probe.turn, "status": "completed"})
    await probe.start_turn("turn-b")
    await probe.complete("item_0", "Second.")
    await probe.send("item/agentMessage/delta", turnId="turn-a", itemId="item_0", delta="STALE")
    assert [block["text"] for block in probe.text_completions()] == ["First.", "Second."]
    assert [block["turn_id"] for block in probe.text_completions()] == ["turn-a", "turn-b"]
    assert not any(event.get("delta", {}).get("text") == "STALE" for event in probe.events)


async def test_completion_without_observed_turn_start_keeps_native_context(tmp_path):
    probe = Probe(tmp_path)
    assert probe.transport._current_turn_id is None
    await probe.complete("late-attachment", "Observed completion.", phase="final_answer")
    block = probe.text_completions()[0]
    assert block["turn_id"] == "turn-a" and block["thread_id"] == "thread-a"
    assert all(event["turn_id"] == "turn-a" for event in probe.events)


async def test_subprocess_completed_text_survives_real_reducer(tmp_path):
    transport = CodexSubprocessTransport(str(tmp_path))
    events = []

    async def capture(event):
        events.append(event)

    transport.on_event(capture)
    await transport._handle_codex_event(
        {
            "type": "item.completed",
            "item": {
                "type": "agent_message",
                "id": "item_0",
                "text": "READY",
                "phase": "final_answer",
            },
        }
    )
    await transport._handle_codex_event({"type": "turn.completed"})
    block = events[0]["message"]["content"][0]
    assert block["type"] == "text" and block["text"] == "READY"
    assert block["id"] == "item_0" and block["phase"] == "final_answer"
    assert block["complete"] is True
    frames = [
        SimpleNamespace(
            session_id="test",
            seq=i + 1,
            kind=e["type"],
            payload=e,
            request_id=None,
            ts=datetime.now(UTC),
        )
        for i, e in enumerate(events)
    ]
    assert reduce_frames(frames).turns[0]["content"] == "READY"


async def test_large_matching_completion_uses_bounded_stop_and_preserves_exact_prose(tmp_path):
    probe = Probe(tmp_path)
    await probe.start_turn()
    await probe.start("large", phase=None)
    text = "東京🙂\n" * 90_000  # 990,000 UTF-8 bytes, above the actual 900 KiB channel cap.
    for index in range(0, len(text), 20_000):
        await probe.delta("large", text[index : index + 20_000])
    await probe.complete("large", text, phase="final_answer")
    assert probe.text_completions() == []  # Exact previously-emitted bytes need no second copy.
    stop = probe.events[-1]
    assert stop["item_id"] == "large" and stop["complete"] is True
    assert stop["phase"] == "final_answer"
    assert stop["text_bytes"] == len(text.encode("utf-8"))
    assert stop["text_sha256"] == hashlib.sha256(text.encode("utf-8")).hexdigest()
    socket = SimpleNamespace(send_text=AsyncMock())
    channel = WebSocketChannel(socket, max_frame_bytes=900 * 1024)
    for event in probe.events:
        await channel.send_event(event)
    sent = [call.args[0] for call in socket.send_text.call_args_list]
    assert all(len(frame.encode("utf-8")) <= 900 * 1024 for frame in sent)
    assert not any(json.loads(frame).get("code") == "live_frame_too_large" for frame in sent)
    await probe.send("turn/completed", turn={"id": probe.turn, "status": "completed"})
    part = probe.turns()[0]["parts"][0]
    assert part["text"] == text and part["complete"] is True
    assert part["phase"] == "final_answer"


@pytest.mark.parametrize("prefix", ["", "Partial"])
async def test_large_completion_only_or_mismatch_remains_exact_with_explicit_rest_recovery(
    tmp_path, prefix
):
    probe = Probe(tmp_path)
    await probe.start_turn()
    if prefix:
        await probe.start("large")
        await probe.delta("large", prefix)
    text = "東京🙂\n" * 90_000
    await probe.complete("large", text, phase="final_answer")
    completion = probe.text_completions()[0]
    assert completion["text"] == text  # Durable producer data is never truncated.
    socket = SimpleNamespace(send_text=AsyncMock())
    channel = WebSocketChannel(socket, max_frame_bytes=900 * 1024)
    for event in probe.events:
        await channel.send_event(event)
    sent = [call.args[0] for call in socket.send_text.call_args_list]
    assert all(len(frame.encode("utf-8")) <= 900 * 1024 for frame in sent)
    assert sum(json.loads(frame).get("code") == "live_frame_too_large" for frame in sent) == 1
    await probe.send("turn/completed", turn={"id": probe.turn, "status": "completed"})
    assert probe.turns()[0]["parts"][0]["text"] == text


@pytest.mark.parametrize("large", [False, True])
async def test_hidden_tools_do_not_swallow_interleaved_public_text(tmp_path, large):
    """Exercise the actual adapter -> default channel -> replay path, not only normalization."""
    probe = Probe(tmp_path)
    await probe.start_turn()
    await probe.start("public-message")
    await probe.send(
        "item/started", item={"type": "commandExecution", "id": "tool-a", "command": "true"}
    )
    text = "Checking 東京🙂.\n" * (90_000 if large else 1)
    for index in range(0, len(text), 20_000):
        await probe.delta("public-message", text[index : index + 20_000])
    await probe.complete("public-message", text)
    await probe.send(
        "item/completed",
        item={"type": "commandExecution", "id": "tool-a", "exitCode": 0, "aggregatedOutput": ""},
    )
    await probe.send("turn/completed", turn={"id": probe.turn, "status": "completed"})
    socket = SimpleNamespace(send_text=AsyncMock())
    channel = WebSocketChannel(socket, max_frame_bytes=900 * 1024)
    for event in probe.events:
        await channel.send_event(event)
    sent = [json.loads(call.args[0]) for call in socket.send_text.call_args_list]
    assert "".join(event.get("delta", {}).get("text", "") for event in sent) == text
    assert any(
        event["type"] == "content_block_stop" and event.get("item_id") == "public-message"
        for event in sent
    )
    frames = [
        SimpleNamespace(
            session_id="channel-test",
            seq=i + 1,
            kind=e["type"],
            payload=e,
            request_id=None,
            ts=datetime.now(UTC),
        )
        for i, e in enumerate(sent)
    ]
    turn = reduce_frames(frames).turns[0]
    assert turn["content"] == text
    assert not any(part["type"] in {"tool_use", "tool_result"} for part in turn["parts"])
