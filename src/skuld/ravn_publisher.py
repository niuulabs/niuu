"""Ravn event publisher — maps CLI NDJSON stream events to Sleipnir events.

The Skuld broker receives raw NDJSON lines from the Claude Code CLI.
:class:`RavnPublisher` inspects each line and emits structured
:data:`ravn.*` events onto the Sleipnir bus so that downstream services
(Ting activity tracking, Volundr token accounting, analytics) can react
without parsing raw NDJSON.

Supported mappings
------------------
NDJSON type             → Sleipnir event type
-------------------------------------------
``tool_use``            → ``ravn.tool.call``
``tool_result``         → ``ravn.tool.complete``  /  ``ravn.tool.error``
``message`` (user)      → ``ravn.step.start``
``message`` (assistant) → ``ravn.step.complete``
``system`` (init)       → ``ravn.session.start``
session_end sentinel    → ``ravn.session.end``
"""

from __future__ import annotations

import logging

from sleipnir.domain.events import SleipnirEvent
from sleipnir.domain.registry import (
    RAVN_INTERRUPT,
    RAVN_RESPONSE_COMPLETE,
    RAVN_SESSION_END,
    RAVN_SESSION_START,
    RAVN_STEP_COMPLETE,
    RAVN_STEP_START,
    RAVN_TOOL_CALL,
    RAVN_TOOL_COMPLETE,
    RAVN_TOOL_ERROR,
)
from sleipnir.ports.events import SleipnirPublisher

logger = logging.getLogger(__name__)

_ROLE_USER = "user"
_ROLE_ASSISTANT = "assistant"


class RavnPublisher:
    """Maps raw CLI NDJSON events to Sleipnir ``ravn.*`` events.

    Args:
        publisher: The Sleipnir publisher to emit events on.
        session_id: The Skuld session identifier (used as ``correlation_id``).
        source: Publisher identity string (defaults to ``ravn:<session_id>``).
    """

    def __init__(
        self,
        publisher: SleipnirPublisher,
        session_id: str,
        source: str | None = None,
    ) -> None:
        self._publisher = publisher
        self._session_id = session_id
        self._source = source or f"ravn:{session_id}"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def on_session_start(self, model: str = "", **extra: object) -> None:
        """Publish ``ravn.session.start`` when the CLI connects."""
        await self._publish(
            RAVN_SESSION_START,
            payload={"session_id": self._session_id, "model": model, **extra},
            summary=f"Agent session started: {self._session_id}",
            urgency=0.7,
        )

    async def on_session_end(self, reason: str = "completed", **extra: object) -> None:
        """Publish ``ravn.session.end`` when the CLI disconnects."""
        await self._publish(
            RAVN_SESSION_END,
            payload={"session_id": self._session_id, "reason": reason, **extra},
            summary=f"Agent session ended: {self._session_id} ({reason})",
            urgency=0.7,
        )

    async def on_ndjson_line(self, line: dict) -> None:
        """Inspect a raw NDJSON line and publish the appropriate ravn event."""
        msg_type = line.get("type", "")

        if msg_type == "tool_use":
            await self._on_tool_use(line)
        elif msg_type == "tool_result":
            await self._on_tool_result(line)
        elif msg_type == "message":
            await self._on_message(line)
        elif msg_type == "system" and line.get("subtype") == "init":
            model = line.get("session", {}).get("model", "")
            await self.on_session_start(model=model)

    async def on_interrupt(self, **extra: object) -> None:
        """Publish ``ravn.interrupt`` when a user interrupt is received."""
        await self._publish(
            RAVN_INTERRUPT,
            payload={"session_id": self._session_id, **extra},
            summary=f"Agent session interrupted: {self._session_id}",
            urgency=0.9,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _on_tool_use(self, line: dict) -> None:
        tool_name = line.get("name", "")
        await self._publish(
            RAVN_TOOL_CALL,
            payload={
                "session_id": self._session_id,
                "tool": tool_name,
                "tool_use_id": line.get("id", ""),
                "input_preview": str(line.get("input", ""))[:200],
            },
            summary=f"Tool dispatched: {tool_name}",
            urgency=0.5,
        )

    async def _on_tool_result(self, line: dict) -> None:
        is_error = bool(line.get("is_error", False))
        event_type = RAVN_TOOL_ERROR if is_error else RAVN_TOOL_COMPLETE
        tool_use_id = line.get("tool_use_id", "")
        await self._publish(
            event_type,
            payload={
                "session_id": self._session_id,
                "tool_use_id": tool_use_id,
                "is_error": is_error,
            },
            summary=f"Tool {'failed' if is_error else 'completed'}: {tool_use_id}",
            urgency=0.4,
        )

    async def _on_message(self, line: dict) -> None:
        role = line.get("role", "")
        if role == _ROLE_USER:
            await self._publish(
                RAVN_STEP_START,
                payload={"session_id": self._session_id, "role": role},
                summary=f"Agent step started for session {self._session_id}",
                urgency=0.3,
            )
        elif role == _ROLE_ASSISTANT:
            stop_reason = line.get("stop_reason", "")
            if stop_reason == "end_turn":
                await self._publish(
                    RAVN_RESPONSE_COMPLETE,
                    payload={"session_id": self._session_id, "stop_reason": stop_reason},
                    summary=f"Agent response complete for session {self._session_id}",
                    urgency=0.5,
                )
            else:
                await self._publish(
                    RAVN_STEP_COMPLETE,
                    payload={"session_id": self._session_id, "stop_reason": stop_reason},
                    summary=f"Agent step complete for session {self._session_id}",
                    urgency=0.3,
                )

    async def _publish(
        self,
        event_type: str,
        *,
        payload: dict,
        summary: str,
        urgency: float,
    ) -> None:
        event = SleipnirEvent(
            event_type=event_type,
            source=self._source,
            payload=payload,
            summary=summary,
            urgency=urgency,
            domain="code",
            timestamp=SleipnirEvent.now(),
            correlation_id=self._session_id,
        )
        try:
            await self._publisher.publish(event)
        except Exception:
            logger.error(
                "RavnPublisher: failed to publish %s for session %s",
                event_type,
                self._session_id,
                exc_info=True,
            )
