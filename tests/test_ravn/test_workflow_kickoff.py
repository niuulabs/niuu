"""Tests for the flock-side workflow kickoff acknowledgement handshake.

Skuld republishes an unacknowledged kickoff until a persona acks it over the
mesh. These tests prove the persona side of the contract:

- the ack is published the moment the kickoff is consumed (before any task
  reaches the queue),
- redelivered kickoffs are re-acked but never enqueued twice,
- a fresh kickoff id (deliberate operator resend) is executed again.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from niuu.domain.workflow_kickoff import (
    WORKFLOW_KICKOFF_ACK_EVENT_TYPE,
    WORKFLOW_KICKOFF_ID_KEY,
    WORKFLOW_KICKOFF_REDELIVERY_KEY,
    WORKFLOW_TRIGGER_NODE_ID_KEY,
    is_workflow_kickoff_ack_payload,
    is_workflow_kickoff_payload,
)
from ravn.adapters.personas.loader import PersonaConfig, PersonaConsumes
from ravn.config import Settings
from ravn.domain.events import RavnEvent, RavnEventType
from ravn.workflow_kickoff import WorkflowKickoffAcknowledger
from tests.test_ravn.conftest import _make_drive_loop


def _kickoff_event(
    *,
    kickoff_id: str = "kick-1",
    redelivery: int = 0,
    correlation_id: str = "session-123",
    with_kickoff_id: bool = True,
) -> RavnEvent:
    payload: dict = {
        "event_type": "code.requested",
        "persona": "skuld",
        "prompt": "Implement the requested change",
        WORKFLOW_TRIGGER_NODE_ID_KEY: "trigger-1",
        WORKFLOW_KICKOFF_REDELIVERY_KEY: redelivery,
    }
    if with_kickoff_id:
        payload[WORKFLOW_KICKOFF_ID_KEY] = kickoff_id
    return RavnEvent(
        type=RavnEventType.OUTCOME,
        source="skuld",
        payload=payload,
        timestamp=datetime.now(UTC),
        urgency=0.8,
        correlation_id=correlation_id,
        session_id=correlation_id,
        root_correlation_id=correlation_id,
    )


# ---------------------------------------------------------------------------
# Shared contract helpers (niuu.domain.workflow_kickoff)
# ---------------------------------------------------------------------------


class TestKickoffPayloadHelpers:
    def test_kickoff_payload_detection(self):
        assert is_workflow_kickoff_payload({WORKFLOW_TRIGGER_NODE_ID_KEY: "trigger-1"})
        assert not is_workflow_kickoff_payload({WORKFLOW_TRIGGER_NODE_ID_KEY: "  "})
        assert not is_workflow_kickoff_payload({"event_type": "code.requested"})
        assert not is_workflow_kickoff_payload(None)

    def test_ack_payload_detection(self):
        assert is_workflow_kickoff_ack_payload({"event_type": WORKFLOW_KICKOFF_ACK_EVENT_TYPE})
        assert not is_workflow_kickoff_ack_payload({"event_type": "code.changed"})
        assert not is_workflow_kickoff_ack_payload({})
        assert not is_workflow_kickoff_ack_payload(None)


# ---------------------------------------------------------------------------
# WorkflowKickoffAcknowledger
# ---------------------------------------------------------------------------


class TestWorkflowKickoffAcknowledger:
    @pytest.mark.asyncio
    async def test_first_delivery_publishes_ack_and_returns_true(self):
        mesh = MagicMock(publish=AsyncMock())
        acknowledger = WorkflowKickoffAcknowledger(
            mesh=mesh, peer_id="flock-coder", persona="coder"
        )
        event = _kickoff_event()

        assert acknowledger.is_kickoff(event)
        assert await acknowledger.acknowledge(event) is True

        mesh.publish.assert_awaited_once()
        ack = mesh.publish.await_args.args[0]
        assert mesh.publish.await_args.kwargs["topic"] == WORKFLOW_KICKOFF_ACK_EVENT_TYPE
        assert ack.type == RavnEventType.OUTCOME
        assert ack.correlation_id == "session-123"
        assert ack.root_correlation_id == "session-123"
        assert ack.payload["event_type"] == WORKFLOW_KICKOFF_ACK_EVENT_TYPE
        assert ack.payload[WORKFLOW_KICKOFF_ID_KEY] == "kick-1"
        assert ack.payload[WORKFLOW_TRIGGER_NODE_ID_KEY] == "trigger-1"
        assert ack.payload["peer_id"] == "flock-coder"
        assert ack.payload["persona"] == "coder"
        assert ack.payload["duplicate"] is False
        assert ack.payload["routing_only"] is True

    @pytest.mark.asyncio
    async def test_redelivery_is_reacked_but_flagged_duplicate(self):
        mesh = MagicMock(publish=AsyncMock())
        acknowledger = WorkflowKickoffAcknowledger(
            mesh=mesh, peer_id="flock-coder", persona="coder"
        )

        assert await acknowledger.acknowledge(_kickoff_event(redelivery=0)) is True
        assert await acknowledger.acknowledge(_kickoff_event(redelivery=1)) is False

        assert mesh.publish.await_count == 2
        duplicate_ack = mesh.publish.await_args.args[0]
        assert duplicate_ack.payload["duplicate"] is True
        assert duplicate_ack.payload[WORKFLOW_KICKOFF_REDELIVERY_KEY] == 1

    @pytest.mark.asyncio
    async def test_fresh_kickoff_id_is_treated_as_new_dispatch(self):
        mesh = MagicMock(publish=AsyncMock())
        acknowledger = WorkflowKickoffAcknowledger(
            mesh=mesh, peer_id="flock-coder", persona="coder"
        )

        assert await acknowledger.acknowledge(_kickoff_event(kickoff_id="kick-1")) is True
        assert await acknowledger.acknowledge(_kickoff_event(kickoff_id="kick-2")) is True

    @pytest.mark.asyncio
    async def test_falls_back_to_correlation_id_for_legacy_kickoffs(self):
        mesh = MagicMock(publish=AsyncMock())
        acknowledger = WorkflowKickoffAcknowledger(
            mesh=mesh, peer_id="flock-coder", persona="coder"
        )

        first = _kickoff_event(with_kickoff_id=False)
        assert await acknowledger.acknowledge(first) is True
        assert await acknowledger.acknowledge(first) is False
        other_session = _kickoff_event(with_kickoff_id=False, correlation_id="session-456")
        assert await acknowledger.acknowledge(other_session) is True


# ---------------------------------------------------------------------------
# Integration through the production mesh outcome handler (_wire_cascade)
# ---------------------------------------------------------------------------


def _wire_kickoff_handler(mesh: MagicMock):
    """Wire a coder persona through _wire_cascade and return (drive_loop, handler)."""
    dl = _make_drive_loop()
    settings = Settings()
    settings.mesh.enabled = True
    settings.mesh.own_peer_id = "flock-coder"
    settings.discovery.enabled = False
    persona = PersonaConfig(
        name="coder",
        consumes=PersonaConsumes(event_types=["code.requested"]),
    )

    from ravn.cli.commands import _wire_cascade  # type: ignore[attr-defined]

    with patch("ravn.cli.commands._build_mesh", return_value=mesh):
        _wire_cascade(dl, settings, persona)

    topic, handler = mesh._pending_outcome_subscriptions[0]
    assert topic == "code.requested"
    return dl, handler


@pytest.mark.asyncio
async def test_kickoff_consumption_acks_before_enqueue():
    mesh = MagicMock(publish=AsyncMock())
    dl, handler = _wire_kickoff_handler(mesh)

    await handler(_kickoff_event())

    assert len(dl.queued_task_ids()) == 1
    mesh.publish.assert_awaited_once()
    ack = mesh.publish.await_args.args[0]
    assert ack.payload["event_type"] == WORKFLOW_KICKOFF_ACK_EVENT_TYPE
    assert ack.payload["peer_id"] == "flock-coder"
    assert ack.payload["persona"] == "coder"


@pytest.mark.asyncio
async def test_redelivered_kickoff_is_reacked_without_second_enqueue():
    mesh = MagicMock(publish=AsyncMock())
    dl, handler = _wire_kickoff_handler(mesh)

    kickoff = _kickoff_event()
    await handler(kickoff)
    await handler(replace(kickoff, payload={**kickoff.payload, WORKFLOW_KICKOFF_REDELIVERY_KEY: 1}))

    assert len(dl.queued_task_ids()) == 1
    assert mesh.publish.await_count == 2
    assert mesh.publish.await_args.args[0].payload["duplicate"] is True


@pytest.mark.asyncio
async def test_non_kickoff_outcome_is_not_acked():
    mesh = MagicMock(publish=AsyncMock())
    dl, handler = _wire_kickoff_handler(mesh)

    await handler(
        RavnEvent(
            type=RavnEventType.OUTCOME,
            source="skuld",
            payload={"event_type": "code.requested", "persona": "skuld"},
            timestamp=datetime.now(UTC),
            urgency=0.5,
            correlation_id="session-123",
            session_id="session-123",
            root_correlation_id="session-123",
        )
    )

    assert len(dl.queued_task_ids()) == 1
    mesh.publish.assert_not_awaited()
