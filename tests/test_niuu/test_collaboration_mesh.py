"""Tests for collaboration events carried over the Niuu mesh."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from niuu.adapters.mesh.sleipnir_mesh import _ravn_to_sleipnir
from niuu.collaboration.mesh import (
    MeshCollaborationBridge,
    describe_workflow_event,
    mesh_peer_id,
    workflow_room_events,
)
from niuu.domain.workflow_step import (
    GateDecided,
    GateOpened,
    PersonaContract,
    PersonaPin,
    RunEnded,
    StepContract,
    StepOffered,
    StepOutcome,
    StepPassed,
    StepPickedUp,
    parse_workflow_event,
)
from niuu.mesh.events import MeshEvent
from sleipnir.domain.events import SleipnirEvent


class _Subscription:
    def __init__(self) -> None:
        self.unsubscribed = False

    async def unsubscribe(self) -> None:
        self.unsubscribed = True


class _Subscriber:
    def __init__(self) -> None:
        self.patterns = None
        self.handler = None
        self.subscription = _Subscription()

    async def subscribe(self, patterns, handler):
        self.patterns = list(patterns)
        self.handler = handler
        return self.subscription


def _event(
    events: list[dict],
    *,
    source: str = "ravn:peer-1",
    session_id: str = "session-1",
    ravn_event: dict | None = None,
) -> SleipnirEvent:
    return SleipnirEvent(
        event_type="mesh.activity.peer_1",
        source=source,
        payload={
            "collaboration_events": events,
            "ravn_event": ravn_event or {},
            "ravn_session_id": session_id,
        },
        summary="activity",
        urgency=0.2,
        domain="code",
        timestamp=datetime.now(UTC),
        correlation_id=session_id,
    )


@pytest.mark.asyncio
async def test_mesh_bridge_forwards_projected_events_without_ravn_interpretation() -> None:
    subscriber = _Subscriber()
    handle = AsyncMock()
    register = AsyncMock()
    bridge = MeshCollaborationBridge(
        subscriber,
        handle_frame=handle,
        register_peer=register,
        has_participant=lambda _peer: False,
        session_id="session-1",
        environment_id="cluster-a",
    )
    await bridge.start()

    projected = {
        "kind": "outcome",
        "persona": "Reviewer",
        "eventType": "review.completed",
        "fields": {"verdict": "approve"},
    }
    await subscriber.handler(_event([projected]))

    register.assert_awaited_once_with(peer_id="peer-1", persona="Reviewer", display_name="Reviewer")
    handle.assert_awaited_once_with(
        "peer-1", {"type": "collaboration.events", "events": [projected]}
    )


@pytest.mark.asyncio
async def test_mesh_bridge_filters_session_and_forwards_all_projected_events() -> None:
    subscriber = _Subscriber()
    handle = AsyncMock()
    bridge = MeshCollaborationBridge(
        subscriber,
        handle_frame=handle,
        register_peer=AsyncMock(),
        has_participant=lambda _peer: True,
        session_id="session-1",
    )
    await bridge.start()

    await subscriber.handler(
        _event([{"kind": "message", "content": "wrong"}], session_id="session-2")
    )
    await subscriber.handler(
        _event(
            [
                {"kind": "usage", "usage": {"model": "codex"}},
                {"kind": "activity", "activityType": "thinking"},
            ]
        )
    )

    handle.assert_awaited_once()
    assert handle.await_args.args[1]["events"] == [
        {"kind": "usage", "usage": {"model": "codex"}},
        {"kind": "activity", "activityType": "thinking"},
    ]


@pytest.mark.asyncio
async def test_mesh_bridge_ignores_unprojected_events_and_stops_cleanly() -> None:
    subscriber = _Subscriber()
    handle = AsyncMock()
    bridge = MeshCollaborationBridge(
        subscriber,
        handle_frame=handle,
        register_peer=AsyncMock(),
        has_participant=lambda _peer: True,
    )
    await bridge.start()
    event = _event([])
    event.payload.pop("collaboration_events")
    await subscriber.handler(event)
    handle.assert_not_awaited()

    await bridge.stop()
    assert subscriber.subscription.unsubscribed is True


@pytest.mark.asyncio
async def test_mesh_bridge_drops_collaboration_copy_already_delivered_directly() -> None:
    subscriber = _Subscriber()
    handle = AsyncMock()
    register = AsyncMock()
    bridge = MeshCollaborationBridge(
        subscriber,
        handle_frame=handle,
        register_peer=register,
        has_participant=lambda _peer: False,
        session_id="session-1",
    )
    await bridge.start()

    await subscriber.handler(
        _event(
            [{"kind": "outcome", "eventType": "review.completed"}],
            ravn_event={"collaboration_routing_only": True},
        )
    )

    register.assert_not_awaited()
    handle.assert_not_awaited()


def test_mesh_peer_identity_uses_outer_mesh_publisher() -> None:
    assert mesh_peer_id(_event([], source="ravn:stable-peer")) == "stable-peer"


@pytest.mark.asyncio
async def test_a_realm_room_shows_what_every_member_of_its_realm_publishes() -> None:
    """A workflow run's room (no session filter) renders each member session's own events."""
    subscriber = _Subscriber()
    handle = AsyncMock()
    bridge = MeshCollaborationBridge(
        subscriber,
        handle_frame=handle,
        register_peer=AsyncMock(),
        has_participant=lambda _peer: True,
        session_id=None,
        environment_id="workflow-run-1",
    )
    await bridge.start()

    outcome = {"kind": "outcome", "persona": "reviewer", "eventType": "code.changed"}
    await subscriber.handler(
        _event([outcome], source="ravn:reviewer-session", session_id="reviewer-session")
    )

    handle.assert_awaited_once_with(
        "reviewer-session", {"type": "collaboration.events", "events": [outcome]}
    )


# ---------------------------------------------------------------------------------------------
# `workflow` events in the run's room: one line each, unknown kinds included.
# ---------------------------------------------------------------------------------------------

RUN = "run-1"


def _offer(**overrides) -> StepOffered:
    values = {
        "run_id": RUN,
        "offer_id": "offer-1",
        "node_id": "implement",
        "node_label": "Implement",
        "topic": "code.requested",
        "instructions": "Do the work.",
        "contracts": [
            StepContract.from_persona(
                PersonaPin(alias="coder", id="coder", revision="1", digest="sha256:c0de"),
                PersonaContract(produces_event="code.changed"),
            )
        ],
    }
    values.update(overrides)
    return StepOffered(**values)


def _outcome(**overrides) -> StepOutcome:
    values = {
        "run_id": RUN,
        "offer_id": "offer-1",
        "node_id": "review",
        "node_label": "Review",
        "member": "reviewer",
        "session_id": "s-rev",
        "activation_id": "act-1",
        "ending": "verdict",
        "verdict": "changes_requested",
    }
    values.update(overrides)
    return StepOutcome(**values)


def _workflow_sleipnir_event(body) -> SleipnirEvent:
    mesh_event = MeshEvent.workflow(
        source="root-peer", body=body, session_id="root-session", task_id=None
    )
    return _ravn_to_sleipnir(mesh_event, "wf", "root-peer", "mesh", "env")


def _raw_workflow_event(payload: dict) -> SleipnirEvent:
    event = _workflow_sleipnir_event(RunEnded(run_id=RUN, status="completed"))
    event.payload["ravn_event"] = payload
    return event


LINES = [
    (
        _offer(),
        ("", "code.requested", "Offered Implement to anyone who does code.requested"),
    ),
    (
        _offer(attempt=2),
        ("", "code.requested", "Offered Implement (attempt 2) to anyone who does code.requested"),
    ),
    (
        StepPickedUp(
            run_id=RUN,
            offer_id="o",
            node_id="implement",
            member="coder",
            session_id="s",
            activation_id="a",
        ),
        ("coder", "step.picked_up", "coder picked up implement"),
    ),
    (
        StepPassed(
            run_id=RUN,
            offer_id="o",
            node_id="review",
            member="auditor",
            session_id="s",
            reason="path filter did not match",
        ),
        (
            "auditor",
            "step.passed",
            "auditor passed on review: path filter did not match",
        ),
    ),
    (
        _outcome(),
        (
            "reviewer",
            "step.outcome",
            "reviewer finished Review with verdict changes requested",
        ),
    ),
    (
        _outcome(note="two nits"),
        (
            "reviewer",
            "step.outcome",
            "reviewer finished Review with verdict changes requested: two nits",
        ),
    ),
    (
        _outcome(ending="done", verdict=None),
        ("reviewer", "step.outcome", "reviewer finished Review"),
    ),
    (
        _outcome(
            ending="waiting",
            verdict=None,
            waiting_on={"kind": "person", "ref": "jozef", "reason": "needs a call"},
        ),
        (
            "reviewer",
            "step.outcome",
            "reviewer is waiting on person jozef to finish Review: needs a call",
        ),
    ),
    (
        _outcome(ending="failed", verdict=None, failure_reason="tests would not run"),
        ("reviewer", "step.outcome", "reviewer failed Review: tests would not run"),
    ),
    (
        GateOpened(
            run_id=RUN, gate_id="g1", node_id="approve", node_label="Approve", summary="PR 12"
        ),
        ("", "gate.opened", "A person must decide Approve before the run goes on: PR 12"),
    ),
    (
        GateDecided(
            run_id=RUN, gate_id="g1", node_id="approve", decision="approved", decided_by="jozef"
        ),
        ("", "gate.decided", "jozef approved approve"),
    ),
    (
        GateDecided(
            run_id=RUN,
            gate_id="g1",
            node_id="approve",
            decision="changes_requested",
            decided_by="jozef",
            note="tighten the tests",
        ),
        ("", "gate.decided", "jozef asked for changes at approve: tighten the tests"),
    ),
    (
        RunEnded(run_id=RUN, status="completed"),
        ("", "run.ended", "The run completed"),
    ),
    (
        RunEnded(run_id=RUN, status="failed", reason="review step missed its deadline"),
        ("", "run.ended", "The run failed: review step missed its deadline"),
    ),
]


@pytest.mark.parametrize(("body", "expected"), LINES)
def test_every_known_workflow_kind_is_one_plain_line(body, expected) -> None:
    assert describe_workflow_event(body) == expected


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "step.rescheduled", "run_id": RUN, "reason": "host drained"},
        {"kind": "step.rescheduled", "run_id": RUN, "summary": "again", "reason": "ignored"},
        {"kind": "step.rescheduled", "run_id": RUN},
        {"kind": "step.rescheduled"},
    ],
)
def test_an_unknown_kind_is_shown_generically(payload) -> None:
    who, tag, line = describe_workflow_event(parse_workflow_event(payload))

    assert (who, tag) == ("", "step.rescheduled")
    assert line.startswith("Workflow event step.rescheduled")


def test_an_unknown_kind_names_what_it_says_for_itself() -> None:
    parsed = parse_workflow_event({"kind": "step.rescheduled", "run_id": RUN, "reason": "drained"})

    assert describe_workflow_event(parsed)[2] == "Workflow event step.rescheduled: drained"


@pytest.mark.asyncio
async def test_the_bridge_puts_a_workflow_event_in_the_room_as_one_delegation_line() -> None:
    subscriber = _Subscriber()
    handle = AsyncMock()
    register = AsyncMock()
    bridge = MeshCollaborationBridge(
        subscriber,
        handle_frame=handle,
        register_peer=register,
        has_participant=lambda _peer: False,
        session_id=None,
        environment_id="env",
    )
    await bridge.start()
    body = _outcome()
    sleipnir_event = _workflow_sleipnir_event(body)

    await subscriber.handler(sleipnir_event)

    register.assert_awaited_once_with(
        peer_id="root-peer", persona="reviewer", display_name="reviewer"
    )
    handle.assert_awaited_once()
    peer, frame = handle.await_args.args
    assert peer == "root-peer"
    assert frame["type"] == "collaboration.events"
    assert frame["events"] == [
        {
            "kind": "delegation",
            "sourceEventId": body.event_id,
            "sourceEventType": "workflow",
            "sessionId": "root-session",
            "correlationId": RUN,
            "rootCorrelationId": RUN,
            "fromPersona": "reviewer",
            "persona": "reviewer",
            "eventType": "step.outcome",
            "direction": "workflow",
            "preview": "reviewer finished Review with verdict changes requested",
        }
    ]


@pytest.mark.asyncio
async def test_the_bridge_shows_an_unknown_workflow_kind_instead_of_dropping_it() -> None:
    subscriber = _Subscriber()
    handle = AsyncMock()
    bridge = MeshCollaborationBridge(
        subscriber,
        handle_frame=handle,
        register_peer=AsyncMock(),
        has_participant=lambda _peer: True,
    )
    await bridge.start()

    await subscriber.handler(
        _raw_workflow_event({"kind": "step.rescheduled", "run_id": RUN, "reason": "host drained"})
    )

    (event,) = handle.await_args.args[1]["events"]
    assert event["kind"] == "delegation"
    assert event["eventType"] == "step.rescheduled"
    assert event["preview"] == "Workflow event step.rescheduled: host drained"


@pytest.mark.asyncio
async def test_the_bridge_shows_a_malformed_known_kind_and_logs_it_at_error(caplog) -> None:
    subscriber = _Subscriber()
    handle = AsyncMock()
    bridge = MeshCollaborationBridge(
        subscriber,
        handle_frame=handle,
        register_peer=AsyncMock(),
        has_participant=lambda _peer: True,
    )
    await bridge.start()

    with caplog.at_level("ERROR", logger="niuu.collaboration.mesh"):
        await subscriber.handler(_raw_workflow_event({"kind": "run.ended", "run_id": RUN}))

    (event,) = handle.await_args.args[1]["events"]
    assert event["eventType"] == "run.ended"
    assert event["preview"].startswith("Workflow event run.ended could not be read:")
    assert "workflow event could not be read" in caplog.text


def test_a_workflow_payload_with_no_kind_is_still_shown() -> None:
    (event,) = workflow_room_events(
        {"run_id": RUN}, source_event_id="e", session_id="s", correlation_id=RUN
    )

    assert event["eventType"] == "workflow"
    assert "needs a `kind`" in event["preview"]


@pytest.mark.asyncio
async def test_a_workflow_event_of_another_run_stays_out_of_a_session_room() -> None:
    subscriber = _Subscriber()
    handle = AsyncMock()
    bridge = MeshCollaborationBridge(
        subscriber,
        handle_frame=handle,
        register_peer=AsyncMock(),
        has_participant=lambda _peer: True,
        session_id="other-session",
    )
    await bridge.start()

    await subscriber.handler(_workflow_sleipnir_event(RunEnded(run_id=RUN, status="completed")))

    handle.assert_not_awaited()
