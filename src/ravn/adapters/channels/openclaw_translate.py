"""Translate Ravn's event stream into OpenClaw ``chat`` events.

The two models disagree in a way that silently doubles every answer if you get
it wrong, so that inversion is the whole point of this module.

**Ravn deltas are incremental.** A turn streams ``thought{"text": "p"}`` then
``thought{"text": "ong"}``, and then a terminal ``response{"text": "pong"}``
carrying the whole answer *again*. Captured, not assumed —
``tests/test_ravn/fixtures/ravn_turn_plain.jsonl``.

**OpenClaw deltas are cumulative snapshots.** ``ChatScreenModel.handleChatEvent``
*replaces* the streaming bubble's content with ``message.blocks`` on every delta
(appending was a shipped ballooning-bubble bug). So a client that receives
Ravn's fragments unchanged renders "pongpong".

Doing the inversion here, once, in Python, means that class of bug cannot occur
in Swift at all.

What else this module owns
--------------------------
* **Synthesizing terminals Ravn never sends.** ``RavnAgent`` has zero
  ``RavnEvent.error`` call sites and ``_run_and_signal`` has no ``except``, so a
  failed turn emits nothing and the stream simply ends. There is also no
  end-of-turn frame at all: "closed without a response" and "still streaming"
  are indistinguishable at the transport layer. A turn that ends without a
  ``response`` must become ``state:"error"`` and never a turn that looks
  complete.
* **Tool pairing without ids.** Ravn's ``tool_start`` / ``tool_result`` carry no
  ``ToolCall.id`` (it exists two lines above the emit site and is not passed).
  We pair to the most recent *unmatched* start of the same name, which is
  correct while the per-session lock serialises turns, and mint our own
  ``tool_use`` ids so the client's cross-block pairing works.
* **Tolerating the unknown.** Only 5 of ``RavnEventType``'s 12 members can reach
  this path today; the rest come from DriveLoop/Watchdog and could start
  arriving. Unknown types are preserved rather than dropped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Ravn event types we translate. Everything else is passed through as an
#: unknown block so a new DriveLoop type shows up as data rather than vanishing.
THOUGHT = "thought"
TOOL_START = "tool_start"
TOOL_RESULT = "tool_result"
RESPONSE = "response"
ERROR = "error"


@dataclass
class _PendingTool:
    block_index: int
    tool_use_id: str
    name: str
    matched: bool = False


@dataclass
class TurnTranslator:
    """Stateful translator for exactly one Ravn turn.

    Feed it Ravn events with :meth:`ingest`; each call returns zero or more
    OpenClaw ``chat`` event payloads to broadcast. Call :meth:`finish` when the
    Ravn stream ends so a missing ``response`` becomes an explicit error.
    """

    session_key: str
    run_id: str

    #: Accumulated plain answer text. Ravn sends increments; we send the total.
    text_buf: str = ""
    #: Accumulated extended-thinking text, kept in its own block.
    thinking_buf: str = ""
    blocks: list[dict[str, Any]] = field(default_factory=list)
    _text_block: int | None = None
    _thinking_block: int | None = None
    _tools: list[_PendingTool] = field(default_factory=list)
    _tool_n: int = 0
    finished: bool = False

    # -- helpers -----------------------------------------------------------

    def _message(self) -> dict[str, Any]:
        return {
            "messageId": self.run_id,
            "id": self.run_id,
            "role": "assistant",
            "blocks": [dict(b) for b in self.blocks],
        }

    def _event(self, state: str, **extra: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "sessionKey": self.session_key,
            "state": state,
            "runId": self.run_id,
            "message": self._message(),
        }
        payload.update(extra)
        return payload

    def _ensure_text_block(self) -> int:
        if self._text_block is None:
            self.blocks.append({"type": "text", "text": ""})
            self._text_block = len(self.blocks) - 1
        return self._text_block

    def _ensure_thinking_block(self) -> int:
        if self._thinking_block is None:
            self.blocks.append({"type": "thinking", "text": ""})
            self._thinking_block = len(self.blocks) - 1
        return self._thinking_block

    # -- ingest ------------------------------------------------------------

    def ingest(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        """Translate one Ravn frame. Returns chat events to broadcast."""
        etype = str(event.get("type", ""))
        payload = event.get("payload") or {}

        if etype == THOUGHT:
            return self._on_thought(payload)
        if etype == TOOL_START:
            return self._on_tool_start(payload)
        if etype == TOOL_RESULT:
            return self._on_tool_result(payload)
        if etype == RESPONSE:
            return self._on_response(payload)
        if etype == ERROR:
            return self._on_error(payload)
        return self._on_unknown(etype, payload)

    def _on_thought(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        text = str(payload.get("text") or "")
        if not text:
            return []
        # Extended thinking rides the SAME event type, distinguished only by
        # this boolean. Note: no captured turn has ever carried it — the adapter
        # translates the request to adaptive thinking for opus-5 and no
        # thinking_delta was observed. The branch is kept because the shape is
        # certain and a model change could switch it on.
        if payload.get("thinking"):
            self.thinking_buf += text
            self.blocks[self._ensure_thinking_block()]["text"] = self.thinking_buf
        else:
            self.text_buf += text
            self.blocks[self._ensure_text_block()]["text"] = self.text_buf
        return [self._event("delta")]

    def _on_tool_start(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        self._tool_n += 1
        tool_use_id = f"{self.run_id}-tool-{self._tool_n:03d}"
        name = str(payload.get("tool_name") or "tool")
        block: dict[str, Any] = {
            "type": "tool_use",
            "id": tool_use_id,
            "name": name,
            "input": payload.get("input") or {},
        }
        # `diff` is a unified-diff preview computed BEFORE the tool runs, so it
        # is present even when the tool then fails. Carried through for a future
        # tool card; the client ignores unknown keys.
        if payload.get("diff") is not None:
            block["diff"] = payload["diff"]
        self.blocks.append(block)
        self._tools.append(
            _PendingTool(block_index=len(self.blocks) - 1, tool_use_id=tool_use_id, name=name)
        )
        # A new tool means any subsequent text is a NEW paragraph of the answer,
        # not a continuation of the block before the tool ran.
        self._text_block = None
        return [self._event("delta")]

    def _on_tool_result(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        name = str(payload.get("tool_name") or "tool")
        pending = next((t for t in reversed(self._tools) if t.name == name and not t.matched), None)
        if pending is not None:
            pending.matched = True
            tool_use_id = pending.tool_use_id
        else:
            # Unpaired result: emit it rather than drop it, with no back-
            # reference, so the data survives even if the pairing heuristic
            # was wrong.
            tool_use_id = None
        block: dict[str, Any] = {
            "type": "tool_result",
            "content": payload.get("result"),
            "is_error": bool(payload.get("is_error")),
            "name": name,
        }
        if tool_use_id:
            block["tool_use_id"] = tool_use_id
        self.blocks.append(block)
        return [self._event("delta")]

    def _on_response(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        """The terminal frame. Its text is authoritative and REPLACES the buffer.

        Ravn re-sends the whole answer here. Assigning rather than appending is
        what stops the answer rendering twice.
        """
        text = str(payload.get("text") or "")
        if text:
            self.text_buf = text
            self.blocks[self._ensure_text_block()]["text"] = text
        self.finished = True
        return [self._event("final")]

    def _on_error(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        self.finished = True
        message = str(payload.get("message") or "the agent reported an error")
        return [self._event("error", error={"message": message, **_kind(payload)})]

    def _on_unknown(self, etype: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
        """Preserve a frame we do not model, rather than silently dropping it."""
        if not etype:
            return []
        self.blocks.append({"type": "ravn_unknown", "ravnType": etype, "payload": payload})
        return [self._event("delta")]

    # -- termination -------------------------------------------------------

    def finish(self, *, reason: str = "stream closed") -> list[dict[str, Any]]:
        """Close the turn. Emits an error if no ``response`` ever arrived.

        Ravn's stream just ends — there is no terminator frame — so a dropped
        upstream, a raised turn, or a cancelled task all look identical here.
        Presenting any of them as a completed turn would show a truncated answer
        as if it were the whole one.
        """
        if self.finished:
            return []
        self.finished = True
        return [
            self._event(
                "error",
                error={
                    "message": (
                        f"the turn ended without a response ({reason}) — "
                        "the answer above may be incomplete"
                    ),
                    "kind": "incomplete",
                },
            )
        ]

    def aborted(self) -> list[dict[str, Any]]:
        if self.finished:
            return []
        self.finished = True
        return [self._event("aborted")]

    # -- persistence -------------------------------------------------------

    def final_blocks(self) -> list[dict[str, Any]]:
        """Blocks to persist for this turn, in wire order."""
        return [dict(b) for b in self.blocks]

    def preview_text(self) -> str:
        return self.text_buf


def _kind(payload: dict[str, Any]) -> dict[str, Any]:
    failure = payload.get("failure_kind")
    return {"kind": failure} if failure else {}
