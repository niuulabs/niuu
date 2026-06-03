"""Reusable Valkyrie Environment event-chain examples.

These examples are deterministic fixtures for demos, documentation, and tests.
They deliberately use the standard SleipnirEvent catalog factories instead of a
separate Valkyrie-specific envelope.
"""

from __future__ import annotations

from sleipnir.domain.catalog import (
    attention_decided,
    environment_state_changed,
    feedback_recorded,
    learning_promoted,
    participant_joined,
    room_opened,
    signal_received,
    valkyrie_action_completed,
    valkyrie_action_requested,
    valkyrie_judgment_recorded,
    valkyrie_state_changed,
)
from sleipnir.domain.events import SleipnirEvent


def k8s_event_chain_example() -> list[SleipnirEvent]:
    """Return a deterministic k8s resident Valkyrie signal-to-learning chain."""
    environment_id = "cluster-prod-a"
    correlation_id = "corr-k8s-pod-restart"
    signal = signal_received(
        environment_id=environment_id,
        environment_type="k8s",
        signal_source="kubernetes.events",
        signal_kind="kubernetes",
        severity="warning",
        data={"kind": "Pod", "name": "api-7d9", "reason": "Restarted"},
        source="adapter:kubernetes",
        correlation_id=correlation_id,
    )
    state = environment_state_changed(
        environment_id=environment_id,
        environment_type="k8s",
        previous_state="nominal",
        new_state="investigating",
        severity="warning",
        evidence=[{"event_id": signal.event_id, "reason": "pod restarted"}],
        source="valkyrie:k8s",
        correlation_id=correlation_id,
        causation_id=signal.event_id,
    )
    judgment = valkyrie_judgment_recorded(
        environment_id=environment_id,
        valkyrie_id="k8s-valkyrie",
        attention_tier="watch",
        recommended_action="inspect_pod",
        authority_boundary="autonomous",
        confidence=0.84,
        evidence=[{"event_id": state.event_id, "state": "investigating"}],
        source="valkyrie:k8s",
        correlation_id=correlation_id,
        causation_id=state.event_id,
    )
    action = valkyrie_action_requested(
        environment_id=environment_id,
        valkyrie_id="k8s-valkyrie",
        action_id="action-inspect-api-7d9",
        capability="k8s.inspect_pod",
        authority_boundary="autonomous",
        target={"namespace": "default", "pod": "api-7d9"},
        dry_run=False,
        source="valkyrie:k8s",
        correlation_id=correlation_id,
        causation_id=judgment.event_id,
    )
    result = valkyrie_action_completed(
        environment_id=environment_id,
        valkyrie_id="k8s-valkyrie",
        action_id="action-inspect-api-7d9",
        capability="k8s.inspect_pod",
        outcome="success",
        evidence=[{"restart_count": 1, "last_state": "oom_killed"}],
        source="valkyrie:k8s",
        correlation_id=correlation_id,
        causation_id=action.event_id,
    )
    learning = learning_promoted(
        environment_id=environment_id,
        learning_id="learn-k8s-oom-restart-watch",
        from_scope="environment",
        to_scope="flock",
        summary="Single OOM restart with recovery should be watched before escalation.",
        confidence=0.78,
        source="valkyrie:k8s",
        correlation_id=correlation_id,
        causation_id=result.event_id,
    )
    return [signal, state, judgment, action, result, learning]


def inbox_event_chain_example() -> list[SleipnirEvent]:
    """Return a deterministic inbox resident Valkyrie triage chain."""
    environment_id = "host-jozef-mail"
    correlation_id = "corr-inbox-important"
    signal = signal_received(
        environment_id=environment_id,
        environment_type="host",
        signal_source="gmail.inbox",
        signal_kind="inbox",
        severity="info",
        data={"from": "customer@example.com", "subject": "Renewal question"},
        source="adapter:gmail",
        correlation_id=correlation_id,
    )
    judgment = valkyrie_judgment_recorded(
        environment_id=environment_id,
        valkyrie_id="inbox-valkyrie",
        attention_tier="review",
        recommended_action="draft_reply",
        authority_boundary="human_review",
        confidence=0.89,
        evidence=[{"event_id": signal.event_id, "reason": "customer renewal"}],
        source="valkyrie:inbox",
        correlation_id=correlation_id,
        causation_id=signal.event_id,
    )
    attention = attention_decided(
        environment_id=environment_id,
        target_event_id=judgment.event_id,
        attention_tier="review",
        route="inbox.review_queue",
        reason="Important customer thread needs a draft before sending.",
        source="odin:court",
        correlation_id=correlation_id,
        causation_id=judgment.event_id,
    )
    room = room_opened(
        environment_id=environment_id,
        room_id="room-inbox-renewal",
        purpose="Review customer renewal draft",
        participants=["human:jozef", "valkyrie:inbox"],
        source="skuld:room",
        correlation_id=correlation_id,
        causation_id=attention.event_id,
    )
    feedback = feedback_recorded(
        environment_id=environment_id,
        target_event_id=judgment.event_id,
        feedback_type="attention",
        rating="useful",
        notes="Correctly surfaced renewal thread for review.",
        source="human:jozef",
        correlation_id=correlation_id,
        causation_id=room.event_id,
    )
    return [signal, judgment, attention, room, feedback]


def printer_event_chain_example() -> list[SleipnirEvent]:
    """Return a deterministic printer/Pi resident Valkyrie chain."""
    environment_id = "printer-cell-alpha"
    correlation_id = "corr-printer-resin-low"
    joined = participant_joined(
        environment_id=environment_id,
        participant_id="surface:workbench-display",
        participant_type="surface",
        display_name="Workbench display",
        capabilities=["attention.display", "operator.ack"],
        source="skuld:presence",
        correlation_id=correlation_id,
    )
    state = valkyrie_state_changed(
        environment_id=environment_id,
        valkyrie_id="printer-valkyrie",
        previous_state="watching",
        new_state="wakeful",
        reason="resin telemetry crossed low threshold",
        source="valkyrie:printer",
        correlation_id=correlation_id,
        causation_id=joined.event_id,
    )
    signal = signal_received(
        environment_id=environment_id,
        environment_type="printer_pi",
        signal_source="moonraker.telemetry",
        signal_kind="printer",
        severity="warning",
        data={"printer": "saturn-4", "resin_percent": 8},
        source="adapter:printer-pi",
        correlation_id=correlation_id,
        causation_id=state.event_id,
    )
    judgment = valkyrie_judgment_recorded(
        environment_id=environment_id,
        valkyrie_id="printer-valkyrie",
        attention_tier="urgent",
        recommended_action="notify_operator",
        authority_boundary="human_review",
        confidence=0.93,
        evidence=[{"event_id": signal.event_id, "resin_percent": 8}],
        source="valkyrie:printer",
        correlation_id=correlation_id,
        causation_id=signal.event_id,
    )
    attention = attention_decided(
        environment_id=environment_id,
        target_event_id=judgment.event_id,
        attention_tier="urgent",
        route="surface:workbench-display",
        reason="Resin is too low to safely continue the next print.",
        source="odin:court",
        correlation_id=correlation_id,
        causation_id=judgment.event_id,
    )
    return [joined, state, signal, judgment, attention]
