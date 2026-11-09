"""A streamed Claude text block is one text part, not its deltas plus its snapshot.

The event shapes are taken from the durable log of a real Claude Code 2.1.319 session
(workflow run ``f2ee888e``, coder step): ``message_start`` (the SDK transport turns it
into an empty ``assistant`` frame carrying the message id), a ``content_block_start`` /
``content_block_delta`` / ``content_block_stop`` stream per block keyed only by block
index, and one ``assistant`` snapshot per block keyed only by message id, delivered
between the last delta and the stop.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from claude_agent_sdk.types import AssistantMessage, ResultMessage, StreamEvent, TextBlock

from niuu.domain.transcript_reducer import reduce_frames
from skuld.transports.claude_text_identity import ClaudeTextIdentity
from skuld.transports.sdk import SDKTransport

SESSION = "df360949-c2a7-51da-a8a8-29c13b41cef2"
NATIVE = "f68864ac-b3fe-4092-88a0-a23e3161c6b6"
MODEL = "claude-opus-5-5"
FIRST_MESSAGE = "msg_011Cgt3qAydH9oETWnxP8fta"
FINAL_MESSAGE = "msg_011Cgt3riGLXb5aGH2pMk2VE"

PUSHED = "Branch pushed with the two files; opening the PR against main now."
FINAL = (
    "I added the `count_chars` utility and its tests on a new branch, `feat/char-count-20`, "
    "pushed it, and opened PR #7 into main. All 3 new tests pass.\n\n"
    "---outcome---\nreason:\nverdict: pass\n---end---"
)


@dataclass
class _Frame:
    seq: int
    kind: str
    payload: dict[str, Any]
    request_id: str | None = None


def _stream(event: dict[str, Any], parent: str | None = None) -> StreamEvent:
    return StreamEvent(uuid="u", session_id=NATIVE, event=event, parent_tool_use_id=parent)


def _message_start(message_id: str, parent: str | None = None) -> StreamEvent:
    return _stream(
        {"type": "message_start", "message": {"id": message_id, "role": "assistant"}}, parent
    )


def _start(index: int, block: dict[str, Any], parent: str | None = None) -> StreamEvent:
    return _stream({"type": "content_block_start", "index": index, "content_block": block}, parent)


def _delta(index: int, text: str, parent: str | None = None) -> StreamEvent:
    return _stream(
        {
            "type": "content_block_delta",
            "index": index,
            "delta": {"type": "text_delta", "text": text},
        },
        parent,
    )


def _stop(index: int, parent: str | None = None) -> StreamEvent:
    return _stream({"type": "content_block_stop", "index": index}, parent)


def _snapshot(message_id: str, blocks: list[Any], parent: str | None = None) -> AssistantMessage:
    return AssistantMessage(
        content=blocks,
        model=MODEL,
        message_id=message_id,
        session_id=NATIVE,
        parent_tool_use_id=parent,
    )


def _result(text: str) -> ResultMessage:
    return ResultMessage(
        subtype="success",
        duration_ms=1,
        duration_api_ms=1,
        is_error=False,
        num_turns=2,
        session_id=NATIVE,
        stop_reason="end_turn",
        result=text,
    )


def _split(text: str) -> list[str]:
    midpoint = len(text) // 2
    return [text[:5], text[5:midpoint], text[midpoint:]]


def _stream_of_a_real_run() -> list[Any]:
    """Message one: thinking, a text block, a tool call. Message two: the final text."""
    return [
        _message_start(FIRST_MESSAGE),
        _start(0, {"type": "thinking", "thinking": "", "signature": ""}),
        _stop(0),
        _start(1, {"type": "text", "text": ""}),
        *[_delta(1, chunk) for chunk in _split(PUSHED)],
        _snapshot(FIRST_MESSAGE, [TextBlock(text=PUSHED)]),
        _stop(1),
        _start(2, {"type": "tool_use", "id": "toolu_01B6", "name": "Bash", "input": {}}),
        _stop(2),
        _stream({"type": "message_stop"}),
        _message_start(FINAL_MESSAGE),
        _start(1, {"type": "text", "text": ""}),
        *[_delta(1, chunk) for chunk in _split(FINAL)],
        _snapshot(FINAL_MESSAGE, [TextBlock(text=FINAL)]),
        _stop(1),
        _stream({"type": "message_stop"}),
        _result(FINAL),
    ]


@pytest.fixture
def transport(tmp_path) -> SDKTransport:
    return SDKTransport(workspace_dir=str(tmp_path))


async def _frames(transport: SDKTransport, messages: list[Any]) -> list[_Frame]:
    frames: list[_Frame] = []
    for message in messages:
        event = await transport._translate_sdk_message(message)
        if event is None:
            continue
        frames.append(_Frame(seq=len(frames) + 1, kind=event["type"], payload=event))
    return frames


def _text_parts(turn: dict[str, Any]) -> list[dict[str, Any]]:
    return [part for part in turn["parts"] if part["type"] == "text"]


async def test_streamed_text_blocks_are_not_repeated_by_their_snapshots(transport):
    frames = await _frames(transport, _stream_of_a_real_run())

    turns = reduce_frames(frames).turns

    assert len(turns) == 1
    texts = [part["text"] for part in _text_parts(turns[0])]
    assert texts == [PUSHED, FINAL]
    assert turns[0]["content"] == f"{PUSHED}\n\n{FINAL}"
    assert turns[0]["content"].count(PUSHED) == 1
    assert turns[0]["content"].count("---outcome---") == 1


async def test_stream_events_and_snapshot_share_message_id_and_block_index(transport):
    frames = await _frames(transport, _stream_of_a_real_run())

    expected = f"{FIRST_MESSAGE}:1"
    deltas = [f for f in frames if f.kind == "content_block_delta"][:3]
    assert {f.payload["item_id"] for f in deltas} == {expected}
    start = [f for f in frames if f.kind == "content_block_start"][1]
    assert start.payload["item_id"] == expected
    snapshot = next(
        f
        for f in frames
        if f.kind == "assistant"
        and f.payload["message"]["content"][:1]
        == [{"type": "text", "text": PUSHED, "id": expected, "index": 1}]
    )
    assert snapshot.payload["message"]["id"] == FIRST_MESSAGE
    # The thinking and tool blocks of the same message are not text and carry no identity.
    assert "item_id" not in frames[1].payload
    tool_start = [f for f in frames if f.kind == "content_block_start"][2]
    assert "item_id" not in tool_start.payload


async def test_same_block_index_in_two_messages_stays_two_texts(transport):
    frames = await _frames(transport, _stream_of_a_real_run())

    turn = reduce_frames(frames).turns[0]

    ids = [part["id"] for part in _text_parts(turn)]
    assert ids == [f"{FIRST_MESSAGE}:1", f"{FINAL_MESSAGE}:1"]


async def test_snapshot_without_a_stream_is_kept_as_it_arrived(transport):
    messages = [
        _snapshot(FIRST_MESSAGE, [TextBlock(text=PUSHED)]),
        _snapshot(FINAL_MESSAGE, [TextBlock(text=FINAL)]),
        _result(FINAL),
    ]
    frames = await _frames(transport, messages)

    turn = reduce_frames(frames).turns[0]

    assert "id" not in frames[0].payload["message"]["content"][0]
    assert [part["text"] for part in _text_parts(turn)] == [PUSHED, FINAL]


async def test_a_subagent_streaming_between_parent_blocks_keeps_its_own_identity(transport):
    child = "msg_child"
    messages = [
        _message_start(FIRST_MESSAGE),
        _start(0, {"type": "text", "text": ""}),
        _delta(0, "parent says "),
        _message_start(child, parent="toolu_task"),
        _start(0, {"type": "text", "text": ""}, parent="toolu_task"),
        _delta(0, "child says hi", parent="toolu_task"),
        _snapshot(child, [TextBlock(text="child says hi")], parent="toolu_task"),
        _stop(0, parent="toolu_task"),
        _delta(0, "hello"),
        _snapshot(FIRST_MESSAGE, [TextBlock(text="parent says hello")]),
        _stop(0),
    ]
    frames = await _frames(transport, messages)

    turn = reduce_frames(frames).turns[0]

    assert [part["id"] for part in _text_parts(turn)] == [f"{FIRST_MESSAGE}:0", f"{child}:0"]
    assert [part["text"] for part in _text_parts(turn)] == ["parent says hello", "child says hi"]


def test_a_block_event_before_any_message_start_is_an_error():
    tracker = ClaudeTextIdentity()
    event = {"type": "content_block_delta", "index": 0, "delta": {"text": "x"}}

    with pytest.raises(ValueError, match="before any message_start"):
        tracker.observe_stream_event(event, dict(event))


def test_a_block_event_without_an_index_is_an_error():
    tracker = ClaudeTextIdentity()
    tracker.observe_stream_event(
        {"type": "message_start", "message": {"id": FIRST_MESSAGE}}, {"type": "assistant"}
    )
    event: dict[str, Any] = {"type": "content_block_stop"}

    with pytest.raises(ValueError, match="no integer block index"):
        tracker.observe_stream_event(event, dict(event))


def test_a_message_start_without_an_id_is_an_error():
    tracker = ClaudeTextIdentity()

    with pytest.raises(ValueError, match="no message id"):
        tracker.observe_stream_event({"type": "message_start", "message": {}}, {})


def test_reset_forgets_open_messages():
    tracker = ClaudeTextIdentity()
    tracker.observe_stream_event(
        {"type": "message_start", "message": {"id": FIRST_MESSAGE}}, {"type": "assistant"}
    )
    tracker.reset()
    event = {"type": "content_block_stop", "index": 0}

    with pytest.raises(ValueError, match="before any message_start"):
        tracker.observe_stream_event(event, dict(event))
