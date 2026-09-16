"""Bounded native evidence for text which precedes an already observed tool hook."""

from __future__ import annotations

import json
import os
from stat import S_ISREG

MAX_PREFIX_BYTES = 1024 * 1024


def preceding_tool_text(anchor: dict, *, max_bytes: int = MAX_PREFIX_BYTES) -> list[dict] | None:
    """Read only the exact tool's ancestral assistant message, never private blocks.

    Missing, truncated, replaced, foreign or ambiguous evidence yields None;
    an empty list proves the exact tool has no preceding public text. Callers
    can briefly retry an asynchronous native write within their own deadline.
    """
    if max_bytes <= 0 or not anchor.get("transcript_path"):
        return None
    original_end = anchor["transcript_offset"]
    try:
        fd = os.open(anchor["transcript_path"], os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            stat = os.fstat(stream.fileno())
            if (
                not S_ISREG(stat.st_mode)
                or stat.st_dev != anchor["transcript_device"]
                or stat.st_ino != anchor["transcript_inode"]
                or stat.st_size < original_end
            ):
                return None
            end = stat.st_size
            start = max(0, end - max_bytes)
            stream.seek(start)
            data = stream.read(end - start)
    except OSError:
        return None
    lines = data.splitlines(keepends=True)
    if start and lines:
        lines = lines[1:]  # First suffix fragment has no proven JSONL boundary.
    records = {}
    target = None
    target_index = None
    for line in lines:
        if not line.endswith(b"\n"):
            continue
        try:
            record = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            return None
        if not isinstance(record, dict):
            continue
        identifier = record.get("uuid")
        if not isinstance(identifier, str) or not identifier:
            continue
        if identifier in records:
            return None
        records[identifier] = record
        if not _parent_assistant(record, anchor["native_session_id"]):
            continue
        for index, block in enumerate(record["message"]["content"]):
            if (
                isinstance(block, dict)
                and block.get("type") == "tool_use"
                and block.get("id") == anchor["native_tool_use_id"]
            ):
                if target is not None:
                    return None
                target, target_index = record, index
    if target is None:
        return None
    message_id = target["message"]["id"]
    chain = []
    seen = set()
    current = target
    while current is not None:
        if current["uuid"] in seen:
            return None
        seen.add(current["uuid"])
        if (
            not _parent_assistant(current, anchor["native_session_id"])
            or current["message"]["id"] != message_id
        ):
            break
        chain.append(current)
        parent = current.get("parentUuid")
        if parent is not None and not isinstance(parent, str):
            return None
        if start and parent and parent not in records:
            return None  # The same-message prefix may have fallen outside the bounded tail.
        current = records.get(parent)
    result = []
    for record in reversed(chain):
        blocks = record["message"]["content"]
        if record is target:
            blocks = blocks[:target_index]
        for index, block in enumerate(blocks):
            if not isinstance(block, dict) or block.get("type") != "text":
                continue
            text = block.get("text")
            if isinstance(text, str) and text:
                result.append(
                    {
                        "row_id": record["uuid"],
                        "index": index,
                        "message_id": message_id,
                        "text": text,
                    }
                )
    return result


def _parent_assistant(record: dict, native_id: str) -> bool:
    message = record.get("message")
    return (
        record.get("type") == "assistant"
        and record.get("sessionId") == native_id
        and not any(
            record.get(key)
            for key in (
                "isSidechain",
                "is_sidechain",
                "isMeta",
                "agentId",
                "agent_id",
                "parent_tool_use_id",
            )
        )
        and isinstance(message, dict)
        and message.get("role", "assistant") == "assistant"
        and isinstance(message.get("id"), str)
        and bool(message["id"])
        and isinstance(message.get("content"), list)
    )
