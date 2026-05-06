"""RavnEventTranslator — translates :class:`~skuld.ravn_events.RavnEvent` to
:class:`~sleipnir.domain.events.SleipnirEvent`.

Translation rules
-----------------
- ``RavnEvent.type``           → ``event_type`` (via ``RavnEventType.value``)
- ``RavnEvent.source``         → ``source``
- ``RavnEvent.payload``        → ``payload``
- ``RavnEvent.urgency``        → ``urgency``
- ``RavnEvent.correlation_id`` → ``correlation_id``

Auto-generated fields
---------------------
- ``summary``   — derived from event type and payload via :data:`_SUMMARY_BUILDERS`
- ``domain``    — always ``"code"`` for Ravn events
- ``timestamp`` — current UTC datetime
"""

from __future__ import annotations

from collections.abc import Callable

from skuld.ravn_events import RavnEvent, RavnEventType
from sleipnir.domain.events import SleipnirEvent

# ---------------------------------------------------------------------------
# Summary builders
# ---------------------------------------------------------------------------

_SummaryFn = Callable[[RavnEvent], str]


def _sid(e: RavnEvent) -> str:
    return e.payload.get("session_id", e.correlation_id or "unknown")


def _tid(e: RavnEvent) -> str:
    return e.payload.get("task_id", e.correlation_id or "unknown")


def _tool(e: RavnEvent) -> str:
    return e.payload.get("tool", e.payload.get("tool_use_id", "unknown"))


_SUMMARY_BUILDERS: dict[RavnEventType, _SummaryFn] = {
    RavnEventType.TURN_START: lambda e: f"Task turn started: {_tid(e)}",
    RavnEventType.TOOL_START: lambda e: f"Tool started: {_tool(e)}",
    RavnEventType.TOOL_COMPLETE: lambda e: f"Tool completed: {_tool(e)}",
    RavnEventType.TOOL_ERROR: lambda e: f"Tool failed: {_tool(e)}",
    RavnEventType.TASK_COMPLETE: lambda e: f"Task complete: {_tid(e)}",
    RavnEventType.DECISION_REQUIRED: lambda e: (
        f"Human decision required: {e.payload.get('question', 'awaiting input')}"
    ),
    RavnEventType.HELP_NEEDED: lambda e: (
        f"Help needed ({e.payload.get('persona', 'agent')}): "
        f"{e.payload.get('summary', 'awaiting input')}"
    ),
    RavnEventType.TOOL_CALL: lambda e: f"Tool dispatched: {_tool(e)}",
    RavnEventType.STEP_START: lambda e: f"Agent step started for session {_sid(e)}",
    RavnEventType.STEP_COMPLETE: lambda e: f"Agent step complete for session {_sid(e)}",
    RavnEventType.SESSION_START: lambda e: f"Agent session started: {_sid(e)}",
    RavnEventType.SESSION_END: lambda e: f"Agent session ended: {_sid(e)}",
    RavnEventType.RESPONSE_COMPLETE: lambda e: f"Agent response complete for session {_sid(e)}",
    RavnEventType.INTERRUPT: lambda e: f"Agent session interrupted: {_sid(e)}",
}

_DOMAIN = "code"


def _default_summary(e: RavnEvent) -> str:
    return f"Ravn event: {e.type.value}"


class RavnEventTranslator:
    """Translates :class:`~skuld.ravn_events.RavnEvent` instances to
    :class:`~sleipnir.domain.events.SleipnirEvent` instances.

    The translator is stateless and thread-safe; a single instance can be
    shared across multiple publishers and tasks.

    Example::

        translator = RavnEventTranslator()
        ravn_event = RavnEvent(
            type=RavnEventType.TOOL_COMPLETE,
            source="ravn:sess-001",
            payload={"tool": "bash", "exit_code": 0},
            urgency=0.4,
            correlation_id="sess-001",
        )
        sleipnir_event = translator.translate(ravn_event)
        await publisher.publish(sleipnir_event)
    """

    def translate(self, event: RavnEvent) -> SleipnirEvent:
        """Translate *event* to a :class:`~sleipnir.domain.events.SleipnirEvent`.

        :param event: The Ravn event to translate.
        :returns: A fully-constructed :class:`~sleipnir.domain.events.SleipnirEvent`.
        """
        summary_fn: _SummaryFn = _SUMMARY_BUILDERS.get(event.type, _default_summary)
        return SleipnirEvent(
            event_type=event.type.value,
            source=event.source,
            payload=event.payload,
            urgency=event.urgency,
            correlation_id=event.correlation_id,
            summary=summary_fn(event),
            domain=_DOMAIN,
            timestamp=SleipnirEvent.now(),
        )
