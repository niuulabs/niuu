"""Tests for Environment/Valkyrie/ODIN Sleipnir taxonomy."""

from __future__ import annotations

import logging

import pytest

from sleipnir.adapters.serialization import deserialize, serialize
from sleipnir.domain import registry
from sleipnir.domain.catalog import (
    attention_decided,
    attention_decision_made,
    environment_state_changed,
    feedback_recorded,
    learning_promoted,
    odin_court_decided,
    participant_joined,
    room_opened,
    signal_received,
    valkyrie_action_completed,
    valkyrie_action_executed,
    valkyrie_action_proposed,
    valkyrie_action_requested,
    valkyrie_judgment_proposed,
    valkyrie_judgment_recorded,
    valkyrie_judgment_rejected,
    valkyrie_state_changed,
    valkyrie_state_updated,
)
from sleipnir.domain.events import EVENT_NAMESPACES, SleipnirEvent, validate_event_type
from sleipnir.domain.valkyrie_examples import (
    inbox_event_chain_example,
    k8s_event_chain_example,
    printer_event_chain_example,
)

TAXONOMY_NAMESPACES = [
    "environment",
    "signal",
    "valkyrie",
    "odin",
    "attention",
    "feedback",
    "learning",
    "participant",
    "room",
]


TAXONOMY_CONSTANTS = [
    registry.ENVIRONMENT_STATE_CHANGED,
    registry.SIGNAL_KUBERNETES_EVENT,
    registry.SIGNAL_INBOX_MESSAGE,
    registry.SIGNAL_PRINTER_EVENT,
    registry.VALKYRIE_STATE_CHANGED,
    registry.VALKYRIE_STATE_UPDATED,
    registry.VALKYRIE_JUDGMENT_RECORDED,
    registry.VALKYRIE_JUDGMENT_PROPOSED,
    registry.VALKYRIE_JUDGMENT_REJECTED,
    registry.VALKYRIE_ACTION_PROPOSED,
    registry.VALKYRIE_ACTION_REQUESTED,
    registry.VALKYRIE_ACTION_COMPLETED,
    registry.VALKYRIE_ACTION_EXECUTED,
    registry.ODIN_COURT_DECIDED,
    registry.ATTENTION_DECISION_MADE,
    registry.ATTENTION_ESCALATED,
    registry.FEEDBACK_RECORDED,
    registry.LEARNING_PROMOTED,
    registry.PARTICIPANT_JOINED,
    registry.ROOM_OPENED,
]


def test_taxonomy_namespaces_registered() -> None:
    for namespace in TAXONOMY_NAMESPACES:
        assert namespace in EVENT_NAMESPACES


def test_taxonomy_constants_validate_without_unknown_namespace_warning(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="sleipnir.domain.events"):
        for event_type in TAXONOMY_CONSTANTS:
            validate_event_type(event_type)

    assert not caplog.records


def test_catalog_factories_create_representative_taxonomy_events() -> None:
    signal = signal_received(
        environment_id="cluster-a",
        environment_type="k8s",
        signal_source="kubernetes.events",
        signal_kind="kubernetes",
        severity="warning",
        data={"pod": "api-7d9"},
        source="adapter:kubernetes",
        correlation_id="corr-1",
    )
    state = environment_state_changed(
        environment_id="cluster-a",
        environment_type="k8s",
        previous_state="nominal",
        new_state="investigating",
        severity="warning",
        evidence=[{"event_id": signal.event_id}],
        source="valkyrie:k8s",
        correlation_id="corr-1",
        causation_id=signal.event_id,
    )
    valkyrie_state = valkyrie_state_changed(
        environment_id="cluster-a",
        valkyrie_id="k8s-valkyrie",
        previous_state="watching",
        new_state="wakeful",
        reason="signal requires inspection",
        source="valkyrie:k8s",
        correlation_id="corr-1",
        causation_id=state.event_id,
    )
    state_update = valkyrie_state_updated(
        environment_id="cluster-a",
        valkyrie_id="k8s-valkyrie",
        previous_state="watching",
        new_state="wakeful",
        reason="published current resident state",
        source="valkyrie:k8s",
        correlation_id="corr-1",
        causation_id=valkyrie_state.event_id,
    )
    judgment = valkyrie_judgment_recorded(
        environment_id="cluster-a",
        valkyrie_id="k8s-valkyrie",
        attention_tier="watch",
        recommended_action="inspect_pod",
        authority_boundary="autonomous",
        confidence=0.84,
        evidence=[{"event_id": state.event_id}],
        source="valkyrie:k8s",
        correlation_id="corr-1",
        causation_id=state_update.event_id,
    )
    proposed_judgment = valkyrie_judgment_proposed(
        environment_id="cluster-a",
        valkyrie_id="k8s-valkyrie",
        attention_tier="ambient",
        recommended_action="inspect_pod",
        authority_boundary="autonomous",
        confidence=0.84,
        operational_state="investigating",
        rationale="pod restart signal needs inspection",
        signal_refs=[signal.event_id],
        evidence=[{"event_id": state_update.event_id}],
        target_surfaces=["surface:ops"],
        expires_at="",
        dissent_refs=[],
        correlation_ids={"root": "corr-1"},
        source="valkyrie:k8s",
        correlation_id="corr-1",
        causation_id=judgment.event_id,
    )
    rejected_judgment = valkyrie_judgment_rejected(
        environment_id="cluster-a",
        valkyrie_id="k8s-valkyrie",
        canonical_event_type=registry.VALKYRIE_JUDGMENT_PROPOSED,
        errors=["tier invalid"],
        rejected_fields={"tier": "watch"},
        source="ravn:drive_loop",
        correlation_id="corr-1",
        causation_id=proposed_judgment.event_id,
    )
    action_proposed = valkyrie_action_proposed(
        environment_id="cluster-a",
        valkyrie_id="k8s-valkyrie",
        action_id="action-1",
        capability="k8s.inspect_pod",
        action_authority="autonomous",
        target={"pod": "api-7d9"},
        rationale="inspect before changing cluster state",
        evidence=[{"event_id": proposed_judgment.event_id}],
        target_surfaces=["surface:ops"],
        dry_run=False,
        source="valkyrie:k8s",
        correlation_id="corr-1",
        causation_id=proposed_judgment.event_id,
    )
    action = valkyrie_action_requested(
        environment_id="cluster-a",
        valkyrie_id="k8s-valkyrie",
        action_id="action-1",
        capability="k8s.inspect_pod",
        authority_boundary="autonomous",
        target={"pod": "api-7d9"},
        dry_run=False,
        source="valkyrie:k8s",
        correlation_id="corr-1",
        causation_id=action_proposed.event_id,
    )
    action_executed = valkyrie_action_executed(
        environment_id="cluster-a",
        valkyrie_id="k8s-valkyrie",
        action_id="action-1",
        capability="k8s.inspect_pod",
        outcome="success",
        source="valkyrie:k8s",
        evidence=[{"restart_count": 1}],
        correlation_id="corr-1",
        causation_id=action.event_id,
    )
    action_done = valkyrie_action_completed(
        environment_id="cluster-a",
        valkyrie_id="k8s-valkyrie",
        action_id="action-1",
        capability="k8s.inspect_pod",
        outcome="success",
        source="valkyrie:k8s",
        evidence=[{"restart_count": 1}],
        correlation_id="corr-1",
        causation_id=action_executed.event_id,
    )
    court = odin_court_decided(
        environment_id="cluster-a",
        court_id="court-1",
        decision="record_and_watch",
        authority_boundary="autonomous",
        dissent=[],
        source="odin:court",
        correlation_id="corr-1",
        causation_id=action_done.event_id,
    )
    attention = attention_decided(
        environment_id="cluster-a",
        target_event_id=court.event_id,
        attention_tier="record",
        route="mimir",
        reason="No human attention needed yet.",
        source="odin:court",
        correlation_id="corr-1",
        causation_id=court.event_id,
    )
    decision = attention_decision_made(
        environment_id="cluster-a",
        root_correlation_id="corr-1",
        decision="record_only",
        tier="ambient",
        action_authorization="none",
        escalation_path="mimir",
        huddle_id="",
        judgment_refs=[proposed_judgment.event_id],
        action_refs=[action_proposed.event_id],
        dissent=[],
        evidence=[{"event_id": proposed_judgment.event_id}],
        rationale="ambient signal can be recorded",
        audit_ref="odin-court:cluster-a:corr-1",
        source="odin:court",
        correlation_id="corr-1",
        causation_id=attention.event_id,
    )
    feedback = feedback_recorded(
        environment_id="cluster-a",
        target_event_id=decision.event_id,
        feedback_type="attention",
        rating="correct",
        notes="Good suppression.",
        source="human:operator",
        correlation_id="corr-1",
        causation_id=decision.event_id,
    )
    learning = learning_promoted(
        environment_id="cluster-a",
        learning_id="learn-1",
        from_scope="environment",
        to_scope="flock",
        summary="Recovered pod restarts can be watched.",
        confidence=0.8,
        source="valkyrie:k8s",
        correlation_id="corr-1",
        causation_id=feedback.event_id,
    )
    participant = participant_joined(
        environment_id="cluster-a",
        participant_id="human:operator",
        participant_type="human",
        display_name="Operator",
        capabilities=["approve", "teach"],
        source="skuld:presence",
        correlation_id="corr-1",
        causation_id=learning.event_id,
    )
    room = room_opened(
        environment_id="cluster-a",
        room_id="room-1",
        purpose="Review cluster state",
        participants=["human:operator", "valkyrie:k8s"],
        source="skuld:room",
        correlation_id="corr-1",
        causation_id=participant.event_id,
    )

    events = [
        signal,
        state,
        valkyrie_state,
        state_update,
        judgment,
        proposed_judgment,
        rejected_judgment,
        action_proposed,
        action,
        action_executed,
        action_done,
        court,
        attention,
        decision,
        feedback,
        learning,
        participant,
        room,
    ]
    assert all(isinstance(event, SleipnirEvent) for event in events)
    assert [event.correlation_id for event in events] == ["corr-1"] * len(events)
    assert state.causation_id == signal.event_id
    assert state_update.event_type == registry.VALKYRIE_STATE_UPDATED
    assert proposed_judgment.event_type == registry.VALKYRIE_JUDGMENT_PROPOSED
    assert proposed_judgment.payload["tier"] == "ambient"
    assert proposed_judgment.payload["signal_refs"] == [signal.event_id]
    assert rejected_judgment.event_type == registry.VALKYRIE_JUDGMENT_REJECTED
    assert action_proposed.event_type == registry.VALKYRIE_ACTION_PROPOSED
    assert action_executed.event_type == registry.VALKYRIE_ACTION_EXECUTED
    assert action.payload["authority_boundary"] == "autonomous"
    assert decision.event_type == registry.ATTENTION_DECISION_MADE
    assert decision.payload["decision"] == "record_only"
    assert room.payload["participants"] == ["human:operator", "valkyrie:k8s"]


@pytest.mark.parametrize(
    ("factory", "expected_event_type"),
    [
        (
            lambda: signal_received(
                environment_id="host-mail",
                environment_type="host",
                signal_source="gmail",
                signal_kind="inbox",
                severity="info",
                data={"subject": "Renewal"},
                source="adapter:gmail",
            ),
            registry.SIGNAL_INBOX_MESSAGE,
        ),
        (
            lambda: signal_received(
                environment_id="printer-cell-a",
                environment_type="printer_pi",
                signal_source="moonraker",
                signal_kind="printer",
                severity="warning",
                data={"resin_percent": 8},
                source="adapter:printer",
            ),
            registry.SIGNAL_PRINTER_EVENT,
        ),
        (
            lambda: attention_decided(
                environment_id="printer-cell-a",
                target_event_id="evt-1",
                attention_tier="urgent",
                route="surface:workbench",
                reason="Resin low",
                source="odin:court",
            ),
            registry.ATTENTION_ESCALATED,
        ),
    ],
)
def test_representative_events_round_trip_through_serialization(
    factory,
    expected_event_type: str,
) -> None:
    event = factory()
    restored = deserialize(serialize(event))

    assert restored.event_type == expected_event_type
    assert restored.payload == event.payload
    assert restored.correlation_id == event.correlation_id


@pytest.mark.parametrize(
    "chain_factory",
    [k8s_event_chain_example, inbox_event_chain_example, printer_event_chain_example],
)
def test_demo_event_chain_examples_round_trip_and_preserve_causation(chain_factory) -> None:
    chain = chain_factory()
    restored = [deserialize(serialize(event)) for event in chain]

    assert [event.event_type for event in restored] == [event.event_type for event in chain]
    assert len({event.correlation_id for event in restored}) == 1
    assert restored[0].causation_id is None
    for previous, current in zip(restored[:-1], restored[1:], strict=True):
        assert current.causation_id == previous.event_id
