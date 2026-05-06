"""RavnEvent — typed intermediate event emitted by the Ravn agent system.

:class:`RavnEvent` is the structured event that Ravn (the Claude Code CLI
agent system) emits.  It acts as a transport-agnostic intermediate
representation that is then translated to a :class:`~sleipnir.domain.events.SleipnirEvent`
by :class:`~skuld.ravn_translator.RavnEventTranslator` before being published
to the Sleipnir event bus.

Translation mapping
-------------------
- :attr:`RavnEvent.type`           → ``event_type`` (via :attr:`RavnEventType.value`)
- :attr:`RavnEvent.source`         → ``source``
- :attr:`RavnEvent.payload`        → ``payload``
- :attr:`RavnEvent.urgency`        → ``urgency``
- :attr:`RavnEvent.correlation_id` → ``correlation_id``
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class RavnEventType(StrEnum):
    """All event types that Ravn can emit.

    The string value of each member is the canonical Sleipnir event type
    string (e.g. ``"ravn.turn.start"``).
    """

    #: A new task turn started — agent received a user request.
    TURN_START = "ravn.turn.start"

    #: A tool call was dispatched to an executor (execution beginning).
    TOOL_START = "ravn.tool.start"

    #: A tool call completed successfully.
    TOOL_COMPLETE = "ravn.tool.complete"

    #: A tool call failed with an error.
    TOOL_ERROR = "ravn.tool.error"

    #: The agent completed the overall task.
    TASK_COMPLETE = "ravn.task.complete"

    #: The agent cannot proceed without a human decision.
    DECISION_REQUIRED = "ravn.decision.required"

    #: The agent needs human help to proceed (less urgent than DECISION_REQUIRED).
    HELP_NEEDED = "ravn.help.needed"

    # --- Lower-level / legacy types (kept for compatibility) -----------------

    #: A tool call was dispatched (legacy name; prefer TOOL_START for new code).
    TOOL_CALL = "ravn.tool.call"

    #: A reasoning step started within an agent loop.
    STEP_START = "ravn.step.start"

    #: A reasoning step completed within an agent loop.
    STEP_COMPLETE = "ravn.step.complete"

    #: An agent session started.
    SESSION_START = "ravn.session.start"

    #: An agent session ended (gracefully or via interrupt).
    SESSION_END = "ravn.session.end"

    #: The agent produced a final response for the current turn.
    RESPONSE_COMPLETE = "ravn.response.complete"

    #: An interrupt signal was received by the agent.
    INTERRUPT = "ravn.interrupt"


@dataclass(kw_only=True)
class RavnEvent:
    """A structured event emitted by the Ravn agent system.

    :param type: The semantic type of the event.
    :param source: Publisher identity (e.g. ``"ravn:session-abc"``).
    :param payload: Event-specific data.
    :param urgency: Priority hint in the range ``0.0`` (lowest) to ``1.0`` (highest).
    :param correlation_id: Groups all events from one task (typically the session ID).
    """

    type: RavnEventType
    source: str
    payload: dict = field(default_factory=dict)
    urgency: float = 0.5
    correlation_id: str | None = None

    def __post_init__(self) -> None:
        if not 0.0 <= self.urgency <= 1.0:
            raise ValueError(f"urgency must be between 0.0 and 1.0, got {self.urgency}")
