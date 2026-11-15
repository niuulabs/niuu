"""Give Claude's streamed text blocks one identity across their two spellings.

With partial messages on, Claude Code spells a single text block twice: as
``content_block_start`` / ``content_block_delta`` / ``content_block_stop`` stream
events (keyed by the block's index inside its message, no message id), and then as
one ``assistant`` snapshot per block (keyed by the message id, no index, and a
content list holding only that one block). Nothing on the wire joins the two, so the
shared transcript reducer saw an identity-less snapshot block as new text and appended
it after the text it had already built from the deltas.

The pairing is carried by the stream itself: ``message_start`` names the message, and
the snapshots of that message arrive in the order its blocks were started. This
tracker follows that and stamps both spellings with the Codex convention the reducer
already folds by identity (``item_id`` on stream frames, ``id`` and ``index`` on the
text block), ``<message id>:<block index>``. It stamps only; it never drops or merges
an event.

A subagent streams its own messages interleaved with the parent's, so state is kept
per ``parent_tool_use_id``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

_STREAM_EVENTS = ("content_block_start", "content_block_delta", "content_block_stop")


@dataclass
class _StreamedBlock:
    kind: str
    snapshotted: bool = False


@dataclass
class _StreamedMessage:
    message_id: str
    blocks: dict[int, _StreamedBlock] = field(default_factory=dict)


def text_item_id(message_id: str, index: int) -> str:
    return f"{message_id}:{index}"


class ClaudeTextIdentity:
    """Stamps one text identity on the stream events and snapshot of each text block."""

    def __init__(self) -> None:
        self._messages: dict[str | None, _StreamedMessage] = {}

    def reset(self) -> None:
        self._messages.clear()

    def observe_stream_event(self, raw_event: dict[str, Any], event: dict[str, Any]) -> None:
        """Track a raw ``StreamEvent`` and stamp its already-converted ``event``.

        ``raw_event`` is the Anthropic wire event; ``event`` is the dict the broker will
        receive for it (for ``message_start`` that is the normalised ``assistant`` frame,
        which carries no text and needs no stamp).
        """
        scope = event.get("parent_tool_use_id") or None
        kind = raw_event.get("type")
        if kind == "message_start":
            self._start_message(scope, raw_event)
            return
        if kind == "message_stop":
            self._messages.pop(scope, None)
            return
        if kind not in _STREAM_EVENTS:
            return
        index = raw_event.get("index")
        if type(index) is not int:
            raise ValueError(f"Claude {kind} event carries no integer block index: {raw_event!r}")
        message = self._messages.get(scope)
        if message is None:
            raise ValueError(
                f"Claude {kind} event for block {index} arrived before any message_start "
                f"(parent_tool_use_id={scope!r}); the stream is out of order"
            )
        if kind == "content_block_start":
            block = raw_event.get("content_block")
            block_type = block.get("type") if isinstance(block, dict) else None
            message.blocks[index] = _StreamedBlock(kind=str(block_type))
        streamed = message.blocks.get(index)
        if streamed is None or streamed.kind != "text":
            return
        event["item_id"] = text_item_id(message.message_id, index)

    def stamp_snapshot(self, event: dict[str, Any]) -> None:
        """Give the text block of an ``assistant`` snapshot the identity of its stream block.

        A snapshot with no streamed counterpart (partial messages off, or a message the
        stream never announced) has no deltas to duplicate and is left as it is.
        """
        body = event.get("message")
        if not isinstance(body, dict):
            return
        message_id = body.get("id")
        message = self._messages.get(event.get("parent_tool_use_id") or None)
        if message is None or message.message_id != message_id:
            return
        content = body.get("content")
        if not isinstance(content, list):
            return
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "text":
                continue
            index = self._next_unsnapshotted_text(message)
            if index is None:
                continue
            message.blocks[index].snapshotted = True
            block["id"] = text_item_id(message.message_id, index)
            block["index"] = index

    def _start_message(self, scope: str | None, raw_event: dict[str, Any]) -> None:
        raw_message = raw_event.get("message")
        message_id = raw_message.get("id") if isinstance(raw_message, dict) else None
        if not isinstance(message_id, str) or not message_id:
            raise ValueError(f"Claude message_start carries no message id: {raw_event!r}")
        self._messages[scope] = _StreamedMessage(message_id=message_id)

    @staticmethod
    def _next_unsnapshotted_text(message: _StreamedMessage) -> int | None:
        for index in sorted(message.blocks):
            streamed = message.blocks[index]
            if streamed.kind == "text" and not streamed.snapshotted:
                return index
        return None
