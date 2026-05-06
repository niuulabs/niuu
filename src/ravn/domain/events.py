"""Domain events for Ravn — emitted to output channels."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum


class RavnEventType(StrEnum):
    THOUGHT = "thought"
    TOOL_START = "tool_start"
    TOOL_RESULT = "tool_result"
    RESPONSE = "response"
    ERROR = "error"
    DECISION = "decision"
    TASK_COMPLETE = "task_complete"
    TASK_STARTED = "task_started"  # emitted by DriveLoop when a task begins execution
    TASK_STUCK = "task_stuck"  # emitted by Watchdog on stuck detection (NIU-510)
    OUTCOME = "outcome"  # emitted when persona produces outcome (mesh event routing)
    HELP_NEEDED = "help_needed"  # emitted when persona needs human input to proceed


@dataclass(frozen=True)
class RavnEvent:
    """An event emitted by the Ravn agent to its output channel."""

    # THOUGHT, TOOL_START, TOOL_RESULT, RESPONSE, ERROR, DECISION, TASK_COMPLETE, TASK_STARTED
    type: RavnEventType
    source: str  # Agent instance ID
    payload: dict  # Event-specific data
    timestamp: datetime  # ISO8601 timestamp
    urgency: float  # 0.0-1.0 (hint for Valkyrie attention model)
    correlation_id: str  # Groups related events
    session_id: str
    task_id: str | None = None  # If from a sub-ravn
    root_correlation_id: str = ""  # Traces back to the original trigger across the event chain

    @classmethod
    def thought(
        cls,
        source: str,
        text: str,
        correlation_id: str,
        session_id: str,
        task_id: str | None = None,
    ) -> RavnEvent:
        return cls(
            type=RavnEventType.THOUGHT,
            source=source,
            payload={"text": text},
            timestamp=datetime.now(UTC),
            urgency=0.1,
            correlation_id=correlation_id,
            session_id=session_id,
            task_id=task_id,
        )

    @classmethod
    def thinking(
        cls,
        source: str,
        text: str,
        correlation_id: str,
        session_id: str,
        task_id: str | None = None,
    ) -> RavnEvent:
        """Emit an extended-thinking block — rendered dimmed/collapsed in CLI."""
        return cls(
            type=RavnEventType.THOUGHT,
            source=source,
            payload={"text": text, "thinking": True},
            timestamp=datetime.now(UTC),
            urgency=0.1,
            correlation_id=correlation_id,
            session_id=session_id,
            task_id=task_id,
        )

    @classmethod
    def tool_start(
        cls,
        source: str,
        tool_name: str,
        tool_input: dict,
        correlation_id: str,
        session_id: str,
        task_id: str | None = None,
        diff: str | None = None,
    ) -> RavnEvent:
        payload = {"tool_name": tool_name, "input": tool_input}
        if diff is not None:
            payload["diff"] = diff
        return cls(
            type=RavnEventType.TOOL_START,
            source=source,
            payload=payload,
            timestamp=datetime.now(UTC),
            urgency=0.3,
            correlation_id=correlation_id,
            session_id=session_id,
            task_id=task_id,
        )

    @classmethod
    def tool_result(
        cls,
        source: str,
        tool_name: str,
        result: str,
        correlation_id: str,
        session_id: str,
        task_id: str | None = None,
        is_error: bool = False,
    ) -> RavnEvent:
        return cls(
            type=RavnEventType.TOOL_RESULT,
            source=source,
            payload={"tool_name": tool_name, "result": result, "is_error": is_error},
            timestamp=datetime.now(UTC),
            urgency=0.6 if is_error else 0.3,
            correlation_id=correlation_id,
            session_id=session_id,
            task_id=task_id,
        )

    @classmethod
    def response(
        cls,
        source: str,
        text: str,
        correlation_id: str,
        session_id: str,
        task_id: str | None = None,
    ) -> RavnEvent:
        return cls(
            type=RavnEventType.RESPONSE,
            source=source,
            payload={"text": text},
            timestamp=datetime.now(UTC),
            urgency=0.2,
            correlation_id=correlation_id,
            session_id=session_id,
            task_id=task_id,
        )

    @classmethod
    def error(
        cls,
        source: str,
        message: str,
        correlation_id: str,
        session_id: str,
        task_id: str | None = None,
    ) -> RavnEvent:
        return cls(
            type=RavnEventType.ERROR,
            source=source,
            payload={"message": message},
            timestamp=datetime.now(UTC),
            urgency=0.6,
            correlation_id=correlation_id,
            session_id=session_id,
            task_id=task_id,
        )

    @classmethod
    def decision_required(
        cls,
        source: str,
        prompt: str,
        correlation_id: str,
        session_id: str,
        task_id: str | None = None,
    ) -> RavnEvent:
        return cls(
            type=RavnEventType.DECISION,
            source=source,
            payload={"prompt": prompt},
            timestamp=datetime.now(UTC),
            urgency=0.9,
            correlation_id=correlation_id,
            session_id=session_id,
            task_id=task_id,
        )

    @classmethod
    def task_started(
        cls, source: str, task_id: str, title: str, correlation_id: str, session_id: str
    ) -> RavnEvent:
        return cls(
            type=RavnEventType.TASK_STARTED,
            source=source,
            payload={"task_id": task_id, "title": title},
            timestamp=datetime.now(UTC),
            urgency=0.2,
            correlation_id=correlation_id,
            session_id=session_id,
            task_id=task_id,
        )

    @classmethod
    def task_complete(
        cls,
        source: str,
        success: bool,
        correlation_id: str,
        session_id: str,
        task_id: str | None = None,
    ) -> RavnEvent:
        return cls(
            type=RavnEventType.TASK_COMPLETE,
            source=source,
            payload={"success": success},
            timestamp=datetime.now(UTC),
            urgency=0.2 if success else 0.7,
            correlation_id=correlation_id,
            session_id=session_id,
            task_id=task_id,
        )

    @classmethod
    def help_needed(
        cls,
        source: str,
        persona: str,
        reason: str,
        summary: str,
        attempted: list[str],
        recommendation: str,
        correlation_id: str,
        session_id: str,
        task_id: str | None = None,
        context: dict | None = None,
    ) -> RavnEvent:
        """Emit when a persona needs human input to proceed.

        Parameters
        ----------
        persona:
            Name of the persona requesting help.
        reason:
            One of: blocked, uncertain, needs_context, scope_exceeded.
        summary:
            One-sentence description of what help is needed.
        attempted:
            List of approaches already tried (max 3 for brevity).
        recommendation:
            Suggested next step for the human.
        context:
            Optional additional context (file paths, error messages, etc.).
        """
        return cls(
            type=RavnEventType.HELP_NEEDED,
            source=source,
            payload={
                "persona": persona,
                "reason": reason,
                "summary": summary,
                "attempted": attempted[:3],  # Cap at 3 for brevity
                "recommendation": recommendation,
                "context": context or {},
            },
            timestamp=datetime.now(UTC),
            urgency=0.85,  # High priority, below decision_required (0.9)
            correlation_id=correlation_id,
            session_id=session_id,
            task_id=task_id,
        )
