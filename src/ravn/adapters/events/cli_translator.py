"""CLI format translator — converts MeshEvents to Claude CLI stream-json.

Translates :class:`~niuu.mesh.events.MeshEvent` objects into the event
format emitted by ``claude --output-format stream-json``, so that the
existing ``useSkuldChat`` browser hook can consume Ravn output without
modification.

The translator is stateful per-turn: it tracks the current content-block
index and whether certain block types are "open" so it can group
consecutive deltas and emit ``content_block_start``/``content_block_stop``
at the right boundaries.
"""

from __future__ import annotations

import json

from niuu.mesh.events import MeshEvent, MeshEventType
from ravn.ports.event_translator import EventTranslatorPort


class CliFormatTranslator(EventTranslatorPort):
    """Translates :class:`MeshEvent` into Claude CLI stream-json events."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._block_index: int = 0
        self._turn_started: bool = False
        self._in_text_block: bool = False
        self._in_thinking_block: bool = False
        self._tool_counter: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def translate(self, event: MeshEvent) -> list[dict]:
        """Return a list of CLI-format event dicts for *event*."""
        out: list[dict] = []

        # Ensure we've emitted the turn-start event.
        if not self._turn_started:
            out.append(self._make_assistant_start())
            self._turn_started = True

        match event.type:
            case MeshEventType.THOUGHT:
                out.extend(self._translate_thought(event))
            case MeshEventType.TOOL_START:
                out.extend(self._translate_tool_start(event))
            case MeshEventType.TOOL_RESULT:
                pass  # Skipped — results fed back to LLM, not streamed to UI
            case MeshEventType.RESPONSE:
                out.extend(self._translate_response(event))
            case MeshEventType.ERROR:
                out.extend(self._translate_error(event))
            case MeshEventType.TASK_COMPLETE:
                out.extend(self._translate_task_complete(event))
            case _:
                pass  # DECISION and unknown types are silently skipped

        return out

    # ------------------------------------------------------------------
    # Turn start
    # ------------------------------------------------------------------

    @staticmethod
    def _make_assistant_start() -> dict:
        return {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [],
            },
        }

    # ------------------------------------------------------------------
    # THOUGHT → text or thinking deltas
    # ------------------------------------------------------------------

    def _translate_thought(self, event: MeshEvent) -> list[dict]:
        is_thinking = event.payload.get("thinking", False)
        text = event.payload["text"]
        out: list[dict] = []

        if is_thinking:
            # Close text block if open.
            out.extend(self._close_text_block())
            if not self._in_thinking_block:
                out.append(self._make_block_start("thinking"))
                self._in_thinking_block = True
            out.append(
                {
                    "type": "content_block_delta",
                    "index": self._block_index,
                    "delta": {"type": "thinking_delta", "thinking": text},
                }
            )
        else:
            # Close thinking block if open.
            out.extend(self._close_thinking_block())
            if not self._in_text_block:
                out.append(self._make_block_start("text"))
                self._in_text_block = True
            out.append(
                {
                    "type": "content_block_delta",
                    "index": self._block_index,
                    "delta": {"type": "text_delta", "text": text},
                }
            )

        return out

    # ------------------------------------------------------------------
    # TOOL_START → tool_use block (start + input + stop)
    # ------------------------------------------------------------------

    def _translate_tool_start(self, event: MeshEvent) -> list[dict]:
        out: list[dict] = []

        # Close any open text/thinking blocks.
        out.extend(self._close_text_block())
        out.extend(self._close_thinking_block())

        self._tool_counter += 1
        tool_id = f"tool_{self._tool_counter:03d}"
        tool_name = event.payload["tool_name"]
        tool_input = event.payload.get("input", {})

        # content_block_start
        out.append(
            {
                "type": "content_block_start",
                "index": self._block_index,
                "content_block": {
                    "type": "tool_use",
                    "id": tool_id,
                    "name": tool_name,
                },
            }
        )

        # Emit full input JSON in one delta (Ravn has complete input upfront).
        out.append(
            {
                "type": "content_block_delta",
                "index": self._block_index,
                "delta": {
                    "type": "input_json_delta",
                    "partial_json": json.dumps(tool_input),
                },
            }
        )

        # content_block_stop
        out.append(
            {
                "type": "content_block_stop",
                "index": self._block_index,
            }
        )

        self._block_index += 1
        return out

    # ------------------------------------------------------------------
    # RESPONSE → result event
    # ------------------------------------------------------------------

    def _translate_response(self, event: MeshEvent) -> list[dict]:
        out: list[dict] = []
        out.extend(self._close_text_block())
        out.extend(self._close_thinking_block())
        out.append(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": event.payload["text"],
                "duration_ms": 0,
            }
        )
        return out

    # ------------------------------------------------------------------
    # ERROR → error event
    # ------------------------------------------------------------------

    def _translate_error(self, event: MeshEvent) -> list[dict]:
        out: list[dict] = []
        out.extend(self._close_text_block())
        out.extend(self._close_thinking_block())
        out.append(
            {
                "type": "error",
                "error": {"message": event.payload["message"]},
            }
        )
        return out

    # ------------------------------------------------------------------
    # TASK_COMPLETE → result event
    # ------------------------------------------------------------------

    def _translate_task_complete(self, event: MeshEvent) -> list[dict]:
        out: list[dict] = []
        out.extend(self._close_text_block())
        out.extend(self._close_thinking_block())
        success = event.payload.get("success", True)
        out.append(
            {
                "type": "result",
                "subtype": "success" if success else "error",
                "is_error": not success,
            }
        )
        return out

    # ------------------------------------------------------------------
    # Block lifecycle helpers
    # ------------------------------------------------------------------

    def _make_block_start(self, block_type: str) -> dict:
        return {
            "type": "content_block_start",
            "index": self._block_index,
            "content_block": {"type": block_type},
        }

    def _close_text_block(self) -> list[dict]:
        if not self._in_text_block:
            return []
        self._in_text_block = False
        stop = {"type": "content_block_stop", "index": self._block_index}
        self._block_index += 1
        return [stop]

    def _close_thinking_block(self) -> list[dict]:
        if not self._in_thinking_block:
            return []
        self._in_thinking_block = False
        stop = {"type": "content_block_stop", "index": self._block_index}
        self._block_index += 1
        return [stop]
