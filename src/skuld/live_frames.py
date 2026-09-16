"""Bound live browser payloads while retaining full tool data in broker storage."""

import json

from skuld.control_errors import control_error_frame
from skuld.conversation_shallow import elide_parts, elide_tool_result_block, elide_tool_use_block


def prepare_live_frame(frame: dict, *, max_bytes: int) -> dict:
    """Project heavy tool blocks to the existing lazy REST expansion contract.

    This runs at the browser boundary only. The durable log and in-flight
    accumulator still contain the untouched native frame.
    """
    if len(json.dumps(frame, ensure_ascii=False).encode("utf-8")) <= max_bytes:
        return frame
    projected = elide_tool_use_block(elide_tool_result_block(frame))
    projected = {**projected}
    message = frame.get("message")
    if isinstance(message, dict) and isinstance(message.get("content"), list):
        projected["message"] = {**message, "content": elide_parts(message["content"])}
    if isinstance(frame.get("content"), list):
        projected["content"] = elide_parts(frame["content"])
    if isinstance(frame.get("content_block"), dict):
        projected["content_block"] = elide_tool_use_block(
            elide_tool_result_block(frame["content_block"])
        )
    if len(json.dumps(projected, ensure_ascii=False).encode("utf-8")) <= max_bytes:
        return projected
    # A raw hook or oversized non-tool payload has no safe textual truncation:
    # emit an explicit recovery notice instead of breaking the entire socket.
    return control_error_frame(
        "This live update exceeds the socket limit. Reload history through REST.",
        frame,
        code="live_frame_too_large",
    )
