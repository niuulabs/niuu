"""The voice call's tools: they act on the one session the call is bound to.

The phone's realtime model calls them directly. They run in the broker against this
session only: send a message into it (and say whether it landed, is queued or failed),
its status right now, and a full read of its conversation. There are no other tools: the
call steers this session, and the session does the work.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from niuu.cap_telemetry import record_truncation
from niuu.domain.message_author import MessageAuthor
from skuld.voice.session_port import SendStatus, SessionTurn, VoiceSessionPort

#: Category the app files these tools under.
SESSION_CATEGORY = "session"
SEND_TOOL = "send_to_this_session"
STATUS_TOOL = "this_session_status"
READ_TOOL = "read_this_session_in_full"

#: The knob that bounds one tool result.
FULL_READ_KNOB = "voice.full_read_max_chars"
#: Characters a full read keeps for its first line (the header, or the note on what it
#: leaves out); ``voice.full_read_max_chars`` has a floor well above it.
_HEADER_ROOM = 512


class UnknownToolError(LookupError):
    """No voice tool by that name."""


@dataclass(frozen=True)
class ToolOutcome:
    """What the voice model hears back."""

    content: str
    is_error: bool = False


class SessionVoiceTools:
    """List and run the tools of a call bound to one session."""

    def __init__(
        self,
        session: VoiceSessionPort,
        *,
        tool_timeout_seconds: float,
        full_read_max_chars: int,
    ) -> None:
        self._session = session
        self._timeout_ms = int(tool_timeout_seconds * 1000)
        self._full_read_max_chars = full_read_max_chars

    @property
    def names(self) -> list[str]:
        return [SEND_TOOL, STATUS_TOOL, READ_TOOL]

    def definitions(self) -> list[dict[str, Any]]:
        """The catalog in the app's shape (``AgentServiceToolDefinitionPayload``)."""
        return [
            self._definition(
                SEND_TOOL,
                "Send a message to THIS call's Forge session to steer it. Use it whenever the "
                "person wants the session to do, change or stop something. Returns once the "
                "message landed (delivered), was queued because the session is busy, or failed.",
                {
                    "message": {
                        "type": "string",
                        "description": (
                            "The instruction for the coding agent, as the person meant it"
                        ),
                    }
                },
                ["message"],
                idempotent=False,
            ),
            self._definition(
                STATUS_TOOL,
                "THIS session's state right now: whether it is working or idle and since when, "
                "what it asks the person, the last message to it and its latest answer.",
                {},
                [],
            ),
            self._definition(
                READ_TOOL,
                "Read THIS session's whole conversation. Only when the person explicitly asks "
                "for a full or deep read.",
                {},
                [],
            ),
        ]

    def _definition(
        self,
        name: str,
        description: str,
        properties: dict[str, Any],
        required: list[str],
        *,
        idempotent: bool = True,
    ) -> dict[str, Any]:
        return {
            "type": "function",
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
            "category": SESSION_CATEGORY,
            "idempotent": idempotent,
            "timeoutMs": self._timeout_ms,
        }

    async def call(
        self, name: str, args: dict[str, Any], *, author: MessageAuthor | None, call_id: str
    ) -> ToolOutcome:
        match name:
            case "send_to_this_session":
                return await self._send(str(args.get("message") or ""), author, call_id)
            case "this_session_status":
                return self._status()
            case "read_this_session_in_full":
                return self._read()
        raise UnknownToolError(name)

    async def _send(self, message: str, author: MessageAuthor | None, call_id: str) -> ToolOutcome:
        text = message.strip()
        name = self._session.status().name
        if not text:
            return ToolOutcome("Nothing to send: the message was empty.", is_error=True)
        outcome = await self._session.send(text, author=author, call_id=call_id)
        match outcome.status:
            case SendStatus.DELIVERED:
                waiting = (
                    f" It is also waiting for the person to answer {outcome.pending_questions} "
                    "open question(s); say so."
                    if outcome.pending_questions
                    else ""
                )
                return ToolOutcome(
                    f"Delivered: {name} received the message and will act on it.{waiting} "
                    "Confirm it to the person in a few words."
                )
            case SendStatus.QUEUED:
                return ToolOutcome(
                    f"Queued: {name} is busy finishing its current step; it takes the message "
                    "next. Tell the person plainly that it is queued, not yet acted on."
                )
        return ToolOutcome(
            f"Not sent: {outcome.reason}. Tell the person plainly; do not say it was sent.",
            is_error=True,
        )

    def _status(self) -> ToolOutcome:
        status = self._session.status()
        turns = self._session.turns()
        state = "working" if status.busy else status.activity_state.replace("_", " ")
        line = f"{status.name}: {state}"
        if status.state_since is not None:
            elapsed = int(datetime.now(UTC).timestamp() - status.state_since)
            line += f" for {_duration(elapsed)}"
        lines = [line]
        for question in status.questions:
            lines.append(f"asking the person: {question}")
        last_human = _last(turns, "user")
        if last_human is not None:
            lines.append(f"last message to it: {last_human.text}")
        last_answer = _last(turns, "assistant")
        if last_answer is not None:
            lines.append(f"latest answer: {last_answer.text}")
        return self._fit("\n".join(lines))

    def _read(self) -> ToolOutcome:
        status = self._session.status()
        turns = self._session.turns()
        if not turns:
            return ToolOutcome(f"{status.name} has no turns yet.")
        header = f"{status.name}: the whole conversation, {len(turns)} turns, oldest first."
        rendered = [_render(turn) for turn in turns]
        # Room for the header or the note on what is left out, whichever goes first.
        budget = self._full_read_max_chars - _HEADER_ROOM
        kept: list[str] = []
        used = 0
        for line in reversed(rendered):
            if used + len(line) + 1 > budget:
                break
            kept.append(line)
            used += len(line) + 1
        kept.reverse()
        cut_newest = not kept
        if cut_newest:
            # The newest turn alone exceeds one tool result: it goes in cut, marked.
            marker = " […cut: the rest of this turn does not fit in one tool result]"
            kept = [rendered[-1][: budget - len(marker)] + marker]
        left_out = len(rendered) - len(kept)
        if left_out == 0 and not cut_newest:
            return ToolOutcome(header + "\n\n" + "\n".join(kept))
        record_truncation(
            FULL_READ_KNOB,
            what="a full read of the session on a voice call",
            kept=len(kept),
            total=len(rendered),
            remedy="the realtime client clamps tool results; ask about a part of the session",
        )
        parts = [f"{status.name}: the newest {len(kept)} of {len(turns)} turns"]
        if cut_newest:
            parts.append("the newest is cut to fit")
        if left_out:
            parts.append(f"the {left_out} earlier turns do not fit in one tool result")
        note = "; ".join(parts) + ". Say so if the person asks about what is left out."
        return ToolOutcome(note + "\n\n" + "\n".join(kept))

    def _fit(self, text: str) -> ToolOutcome:
        if len(text) <= self._full_read_max_chars:
            return ToolOutcome(text)
        record_truncation(
            FULL_READ_KNOB,
            what="a session status on a voice call",
            kept=self._full_read_max_chars,
            total=len(text),
            remedy="the realtime client clamps tool results",
        )
        marker = " […cut: the realtime client takes no more]"
        return ToolOutcome(text[: self._full_read_max_chars - len(marker)] + marker)


def _last(turns: list[SessionTurn], role: str) -> SessionTurn | None:
    return next((turn for turn in reversed(turns) if turn.role == role), None)


def _render(turn: SessionTurn) -> str:
    speaker = "agent" if turn.role == "assistant" else turn.author or "person"
    return f"[{speaker}] {turn.text.strip()}"


def _duration(seconds: int) -> str:
    if seconds < 60:
        return f"{max(seconds, 0)} s"
    if seconds < 3600:
        return f"{seconds // 60} min"
    return f"{seconds // 3600} h {(seconds % 3600) // 60} min"
