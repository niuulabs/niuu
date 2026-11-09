"""A member session's work, shared with its realm's room.

A workflow run's member sessions each run their own harness; the run's room is a session of its
own that runs none. With ``mesh.share_work`` a member publishes what its harness does, in the
shape the room already keeps as a participant's turns (the shape in-pod Ravn workers used):

- each tool call and its result (``TOOL_START`` / ``TOOL_RESULT``): the member's work turn;
- each reply (``RESPONSE``): the member's message;
- each workflow step close (``OUTCOME``): the member's verdict and note, its outcome card.

Everything goes on the member's own ``activity.<peer>`` topic, which no member subscribes to, so
only the realm's room (which hears the whole realm) keeps it; no peer is asked to act on it.

Sharing is a copy for the room. A copy that cannot be published does not fail the member's own
turn: it is logged at error and counted as degraded, naming what was lost.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from niuu.cap_telemetry import record_degraded
from niuu.mesh.events import MeshEvent, MeshEventType

logger = logging.getLogger(__name__)

Publish = Callable[[MeshEvent, str], Awaitable[None]]

_DEGRADED_PATH = "skuld.mesh.share_work"


def activity_topic(peer_id: str) -> str:
    """The topic a member shares its work on: its own, which no member subscribes to."""
    return f"activity.{peer_id}"


def tool_result_text(content: Any) -> str:
    """A tool result's content as the text the room shows."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = [
            str(block.get("text") or "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        if texts:
            return "\n".join(texts)
    return json.dumps(content, default=str)


class WorkSharer:
    """Publishes one member's replies, tool work and step closes for its realm's room."""

    def __init__(self, *, publish: Publish, peer_id: str, persona: str, session_id: str) -> None:
        self._publish = publish
        self._peer_id = peer_id
        self._persona = persona
        self._session_id = session_id
        #: Tool names by tool_use id, so a result names the tool it answers.
        self._tools: dict[str, str] = {}

    async def cli_event(self, data: dict[str, Any]) -> None:
        """Share the tool calls and results a harness frame carries."""
        message = data.get("message")
        blocks = message.get("content") if isinstance(message, dict) else None
        if not isinstance(blocks, list):
            return
        for block in blocks:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "tool_use":
                name = str(block.get("name") or "tool")
                self._tools[str(block.get("id") or "")] = name
                tool_input = block.get("input")
                await self._share(
                    MeshEventType.TOOL_START,
                    {
                        "tool_name": name,
                        "input": tool_input if isinstance(tool_input, dict) else {},
                    },
                )
            elif kind == "tool_result":
                await self._share(
                    MeshEventType.TOOL_RESULT,
                    {
                        "tool_name": self._tools.pop(str(block.get("tool_use_id") or ""), "tool"),
                        "result": tool_result_text(block.get("content")),
                        "is_error": bool(block.get("is_error")),
                    },
                )

    async def reply(self, text: str) -> None:
        """Share the harness's reply: the member's message in the room."""
        if text.strip():
            await self._share(MeshEventType.RESPONSE, {"text": text})

    async def step_closed(self, step: Any, ending: dict[str, Any]) -> None:
        """Share a workflow step close: the member's verdict and note, as its outcome."""
        label = str(getattr(step, "node_label", "") or getattr(step, "node_id", "") or "")
        verdict = str(ending.get("verdict") or ending.get("kind") or "")
        fields: dict[str, Any] = {"step": label, "ending": str(ending.get("kind") or "")}
        outcome = ending.get("outcome")
        if isinstance(outcome, dict):
            fields.update(outcome)
        payload: dict[str, Any] = {
            "event_type": f"workflow.step.{ending.get('kind') or 'closed'}",
            "verdict": verdict,
            "summary": str(ending.get("note") or ""),
            "fields": fields,
        }
        await self._share(MeshEventType.OUTCOME, payload)

    async def _share(self, event_type: MeshEventType, payload: dict[str, Any]) -> None:
        event = MeshEvent(
            type=event_type,
            source=f"skuld:{self._peer_id}",
            payload={**payload, "persona": self._persona},
            timestamp=datetime.now(UTC),
            urgency=0.2,
            correlation_id=self._session_id,
            session_id=self._session_id,
        )
        try:
            await self._publish(event, activity_topic(self._peer_id))
        except Exception as exc:
            record_degraded(
                _DEGRADED_PATH,
                f"could not share {event_type} of session {self._session_id} with its run's "
                f"room ({type(exc).__name__}: {exc}); the room misses it, the member's own "
                "transcript has it",
            )
