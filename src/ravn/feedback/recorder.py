"""Record resident Valkyrie feedback events as episodic memory."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from ravn.domain.models import Episode, Outcome
from ravn.ports.memory import MemoryPort
from sleipnir.domain import registry
from sleipnir.domain.catalog import feedback_preference_updated
from sleipnir.domain.events import SleipnirEvent
from sleipnir.ports.events import SleipnirPublisher, SleipnirSubscriber, Subscription

#: Full resident feedback vocabulary (NIU-1022). Positive types confirm a
#: judgment/action/draft; failure types teach a correction; delivery types
#: change future routing without mutating history.
_POSITIVE_FEEDBACK = {"useful", "good_action", "draft_accepted"}
_FAILURE_FEEDBACK = {
    "bad_action",
    "draft_rejected",
    "draft_edited",
    "dismissed",
    "wrong_tier",
    "wrong_state",
    "should_have_shown",
    "too_late",
}
_DELIVERY_FEEDBACK = {"snooze", "escalate"}
KNOWN_FEEDBACK_TYPES = _POSITIVE_FEEDBACK | _FAILURE_FEEDBACK | _DELIVERY_FEEDBACK


@dataclass(frozen=True)
class DeliveryFeedbackState:
    """Future delivery state derived from immutable feedback history."""

    environment_id: str
    target_event_id: str
    feedback_type: str
    delivery_state: str
    surface_id: str
    user_id: str
    effective_until: str
    source_feedback_id: str
    updated_at: datetime


class EnvironmentFeedbackRecorder:
    """Subscribe to feedback events and persist them through ``MemoryPort``."""

    def __init__(
        self,
        *,
        subscriber: SleipnirSubscriber,
        memory: MemoryPort,
        publisher: SleipnirPublisher | None = None,
        source: str = "ravn:feedback-recorder",
    ) -> None:
        self._subscriber = subscriber
        self._publisher = publisher
        self._memory = memory
        self._source = source
        self._subscription: Subscription | None = None
        self._delivery_state: dict[tuple[str, str, str], DeliveryFeedbackState] = {}

    @property
    def is_running(self) -> bool:
        return self._subscription is not None

    async def start(self) -> None:
        """Subscribe to resident feedback events."""
        if self._subscription is not None:
            return
        self._subscription = await self._subscriber.subscribe(
            [registry.FEEDBACK_RECORDED],
            self._handle_event,
        )

    async def stop(self) -> None:
        """Stop consuming feedback events."""
        if self._subscription is not None:
            await self._subscription.unsubscribe()
            self._subscription = None

    def current_delivery_state(
        self,
        *,
        environment_id: str,
        target_event_id: str,
        surface_id: str = "",
    ) -> DeliveryFeedbackState | None:
        return self._delivery_state.get((environment_id, target_event_id, surface_id))

    async def _handle_event(self, event: SleipnirEvent) -> None:
        episode = feedback_event_to_episode(event)
        await self._memory.record_episode(episode)
        await self._apply_delivery_effect(event)

    async def _apply_delivery_effect(self, event: SleipnirEvent) -> None:
        payload = event.payload
        feedback_type = _text(payload.get("feedback_type"))
        if feedback_type not in _DELIVERY_FEEDBACK:
            return

        delivery_effect = payload.get("delivery_effect")
        if not isinstance(delivery_effect, dict):
            delivery_effect = {}
        environment_id = _text(payload.get("environment_id"))
        target_event_id = _text(payload.get("target_event_id")) or event.event_id
        surface_id = _text(payload.get("surface_id"))
        state = "escalated" if feedback_type == "escalate" else "snoozed"
        effective_until = _text(delivery_effect.get("effective_until"))
        user_id = _text(payload.get("user_id"))

        self._delivery_state[(environment_id, target_event_id, surface_id)] = DeliveryFeedbackState(
            environment_id=environment_id,
            target_event_id=target_event_id,
            feedback_type=feedback_type,
            delivery_state=state,
            surface_id=surface_id,
            user_id=user_id,
            effective_until=effective_until,
            source_feedback_id=event.event_id,
            updated_at=datetime.now(UTC),
        )

        if self._publisher is not None:
            await self._publisher.publish(
                feedback_preference_updated(
                    environment_id=environment_id,
                    target_event_id=target_event_id,
                    feedback_type=feedback_type,
                    delivery_state=state,
                    surface_id=surface_id,
                    user_id=user_id,
                    effective_until=effective_until,
                    reason=_text(payload.get("notes")),
                    source_feedback_id=event.event_id,
                    source=self._source,
                    correlation_id=event.correlation_id,
                    causation_id=event.event_id,
                    tenant_id=event.tenant_id,
                )
            )


def feedback_event_to_episode(event: SleipnirEvent) -> Episode:
    """Convert a resident feedback Sleipnir event into an episodic memory record."""
    payload = event.payload
    feedback_type = _text(payload.get("feedback_type"))
    environment_id = _text(payload.get("environment_id")) or "environment:unknown"
    target_event_id = _text(payload.get("target_event_id")) or event.event_id
    notes = _text(payload.get("notes"))
    rating = _text(payload.get("rating"))
    valkyrie_id = _text(payload.get("responsible_valkyrie_id"))
    signal_source = _text(payload.get("signal_source"))
    domain_scope = _text(payload.get("domain_scope"))
    action_id = _text(payload.get("action_id"))
    court_decision_id = _text(payload.get("court_decision_id"))

    tags = [
        "valkyrie-feedback",
        f"environment:{environment_id}",
        f"feedback:{feedback_type}",
    ]
    _append_tag(tags, "valkyrie", valkyrie_id)
    _append_tag(tags, "signal_source", signal_source)
    _append_tag(tags, "domain", domain_scope)
    _append_tag(tags, "action", action_id)
    _append_tag(tags, "court_decision", court_decision_id)

    structured = {
        "kind": "environment_feedback",
        "source_event_id": event.event_id,
        "environment_id": environment_id,
        "environment_type": _text(payload.get("environment_type")),
        "target_event_id": target_event_id,
        "feedback_type": feedback_type,
        "rating": rating,
        "notes": notes,
        "signal_refs": _list_of_text(payload.get("signal_refs")),
        "judgment_refs": _list_of_text(payload.get("judgment_refs")),
        "court_decision_id": court_decision_id,
        "action_id": action_id,
        "surface_id": _text(payload.get("surface_id")),
        "user_id": _text(payload.get("user_id")),
        "occurred_at": _text(payload.get("occurred_at")) or event.timestamp.isoformat(),
        "correction": (
            payload.get("correction") if isinstance(payload.get("correction"), dict) else {}
        ),
        "delivery_effect": (
            payload.get("delivery_effect")
            if isinstance(payload.get("delivery_effect"), dict)
            else {}
        ),
        "responsible_valkyrie_id": valkyrie_id,
        "signal_source": signal_source,
        "domain_scope": domain_scope,
        "root_correlation_id": _text(payload.get("root_correlation_id"))
        or _text(event.correlation_id),
    }

    summary = (
        f"Valkyrie feedback {feedback_type} for {target_event_id} "
        f"in {environment_id}: {notes or rating}"
    )
    task_description = (
        f"Capture resident Valkyrie feedback for {environment_id} targeting {target_event_id}"
    )
    return Episode(
        episode_id=f"feedback:{event.event_id}",
        session_id=_feedback_session_id(environment_id, valkyrie_id),
        timestamp=event.timestamp,
        summary=summary,
        task_description=task_description,
        tools_used=_tools_used_for(feedback_type, action_id),
        outcome=_outcome_for(feedback_type),
        tags=tags,
        reflection=notes or None,
        errors=_errors_for(feedback_type, notes),
        structured_outcome=structured,
        outcome_valid=True,
    )


def _outcome_for(feedback_type: str) -> Outcome:
    if feedback_type in _POSITIVE_FEEDBACK:
        return Outcome.SUCCESS
    if feedback_type in _FAILURE_FEEDBACK:
        return Outcome.FAILURE
    return Outcome.PARTIAL


def _tools_used_for(feedback_type: str, action_id: str) -> list[str]:
    tools = ["feedback.capture"]
    if action_id:
        tools.append("feedback.action")
    if feedback_type in _DELIVERY_FEEDBACK:
        tools.append("feedback.delivery_state")
    return tools


def _errors_for(feedback_type: str, notes: str) -> list[str]:
    if _outcome_for(feedback_type) == Outcome.SUCCESS:
        return []
    if not notes:
        return [f"{feedback_type} feedback"]
    return [f"{feedback_type} feedback: {notes}"]


def _feedback_session_id(environment_id: str, valkyrie_id: str) -> str:
    owner = valkyrie_id or "environment"
    return _safe_id(f"feedback:{environment_id}:{owner}")


def _append_tag(tags: list[str], key: str, value: str) -> None:
    if value:
        tags.append(f"{key}:{value}")


def _list_of_text(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_text(item) for item in value if _text(item)]


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _safe_id(value: str) -> str:
    return "".join(char if char.isalnum() or char in {":", "-", "_"} else "-" for char in value)
