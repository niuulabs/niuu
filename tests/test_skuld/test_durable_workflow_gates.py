"""A workflow gate survives its session restarting."""

from dataclasses import asdict
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from niuu.domain.transcript_reducer import reduce_frames
from skuld.history_hydration import _HistoryFrame
from skuld.workflow_runtime import WORKFLOW_GATE_FRAME, WorkflowGateNode, WorkflowGateState
from tests.test_skuld.test_broker_review_regressions import _broker


def _state(**overrides) -> WorkflowGateState:
    fields = {
        "id": "review:activation:1",
        "node_id": "review",
        "activation_id": "activation",
        "label": "Review",
        "condition": "",
        "status": "pending",
        "mode": "human_approval",
        "pending_behavior": "help_needed",
        "instructions": "",
        "auto_forward_after": "",
        "requested_at": "2026-10-30T10:00:00+00:00",
        "updated_at": "2026-10-30T10:00:00+00:00",
        "triggered_by_event_type": "review.done",
        "approval_event_type": "review.approved",
        "changes_requested_event_type": "review.changes",
    }
    return WorkflowGateState(**{**fields, **overrides})


def _frame(state: WorkflowGateState) -> SimpleNamespace:
    return SimpleNamespace(
        kind=WORKFLOW_GATE_FRAME,
        payload={"type": WORKFLOW_GATE_FRAME, "gate": asdict(state)},
    )


def _restarted(tmp_path, *states: WorkflowGateState):
    broker = _broker(tmp_path, api_url="")
    broker._mesh_adapter = SimpleNamespace(publish=AsyncMock())
    broker.handle_human_room_message = AsyncMock()
    broker._finish_trace_span = AsyncMock()
    broker._restore_durable_runtime_state([_frame(state) for state in states])
    return broker


async def test_opening_and_deciding_a_gate_are_both_logged_durably(tmp_path):
    broker = _broker(tmp_path, api_url="")
    broker._mesh_adapter = SimpleNamespace(publish=AsyncMock())
    broker.handle_human_room_message = AsyncMock()
    broker._emit_broker_frame = AsyncMock()
    broker._emit_pipeline_event = AsyncMock()
    broker._start_trace_span = AsyncMock(return_value=None)
    broker._finish_trace_span = AsyncMock()
    broker._emit_workflow_gate_help_needed_sleipnir_event = AsyncMock()
    broker._report_activity_state = AsyncMock()
    node = WorkflowGateNode(
        node_id="review",
        label="Review",
        condition="",
        event_types=("review.done",),
        approval_event_type="review.approved",
        changes_requested_event_type="review.changes",
        mode="human_approval",
        pending_behavior="help_needed",
        instructions="",
        auto_forward_after="",
    )

    state = await broker._activate_workflow_gate(node, "activation", "review.done")
    await broker.resolve_workflow_gate(state.id, "APPROVE")

    logged = [call.args[0] for call in broker._emit_broker_frame.await_args_list]
    assert [frame["type"] for frame in logged] == [WORKFLOW_GATE_FRAME] * 2
    assert [frame["gate"]["status"] for frame in logged] == ["pending", "approved"]


@pytest.mark.parametrize("mode", ["human_approval", "human_review"])
async def test_an_agent_cannot_answer_a_gate_that_waits_for_a_person(tmp_path, mode):
    broker = _restarted(tmp_path, _state(mode=mode))

    with pytest.raises(PermissionError, match="waits for a person, so .*regin"):
        await broker.resolve_workflow_gate(
            "review:activation:1", "APPROVE", actor="system:serviceaccount:nats:regin"
        )

    assert [gate["status"] for gate in broker.list_workflow_gates()] == ["pending"]
    broker._mesh_adapter.publish.assert_not_awaited()


async def test_a_workflow_can_let_an_agent_answer_a_gate(tmp_path):
    broker = _restarted(tmp_path, _state(mode="automated_approval"))

    await broker.resolve_workflow_gate(
        "review:activation:1",
        "APPROVE",
        actor="system:serviceaccount:nats:regin",
        decided_by="regin",
    )

    topic = broker._mesh_adapter.publish.call_args.args[1]
    assert topic == "review.approved"


async def test_a_person_still_answers_a_gate_with_no_agent_involved(tmp_path):
    broker = _restarted(tmp_path, _state())

    await broker.resolve_workflow_gate("review:activation:1", "APPROVE", decided_by="a@b.c")

    broker._mesh_adapter.publish.assert_awaited_once()


async def test_a_restored_gate_is_listed_and_still_moves_the_workflow_on(tmp_path):
    broker = _restarted(tmp_path, _state())

    assert [gate["status"] for gate in broker.list_workflow_gates()] == ["pending"]

    await broker.resolve_workflow_gate("review:activation:1", "CHANGES_REQUESTED", notes="redo")

    event, topic = broker._mesh_adapter.publish.call_args.args
    assert topic == "review.changes"
    assert event.correlation_id == "activation"
    assert event.payload["fields"]["notes"] == "redo"


async def test_a_decision_records_who_made_it(tmp_path):
    broker = _restarted(tmp_path, _state())

    gate = await broker.resolve_workflow_gate(
        "review:activation:1", "APPROVE", source="ting.board", decided_by="jozef@niuu.world"
    )

    assert gate["decided_by"] == "jozef@niuu.world"
    assert "approved by jozef@niuu.world" in gate["summary"]
    event = broker._mesh_adapter.publish.call_args.args[0]
    assert event.payload["fields"]["decided_by"] == "jozef@niuu.world"
    assert broker.list_workflow_gates()[0]["decided_by"] == "jozef@niuu.world"


async def test_the_latest_logged_state_of_a_gate_wins(tmp_path):
    broker = _restarted(tmp_path, _state(), _state(status="approved", decision="APPROVE"))

    with pytest.raises(ValueError, match="already resolved"):
        await broker.resolve_workflow_gate("review:activation:1", "APPROVE")
    broker._mesh_adapter.publish.assert_not_awaited()


async def test_a_gate_reopened_after_a_restart_takes_the_next_attempt(tmp_path):
    broker = _restarted(tmp_path, _state(status="changes_requested", attempt=2))

    assert broker._workflow_gate_attempts[("review", "activation")] == 2


async def test_an_unreadable_gate_frame_refuses_to_restore(tmp_path):
    broker = _broker(tmp_path, api_url="")
    frame = SimpleNamespace(kind=WORKFLOW_GATE_FRAME, payload={"gate": {"unexpected": 1}})

    with pytest.raises(TypeError):
        broker._restore_durable_runtime_state([frame])


async def test_deciding_before_the_flock_is_back_fails_and_keeps_the_gate_pending(tmp_path):
    broker = _restarted(tmp_path, _state())
    broker._wait_for_event_consumers = AsyncMock(return_value=False)

    with pytest.raises(RuntimeError, match="workflow.gate_consumer_wait_s"):
        await broker.resolve_workflow_gate("review:activation:1", "APPROVE")

    assert broker.list_workflow_gates()[0]["status"] == "pending"
    broker._mesh_adapter.publish.assert_not_awaited()
    broker.handle_human_room_message.assert_not_awaited()


async def test_a_restored_gate_stops_the_kickoff_being_sent_again(tmp_path):
    broker = _restarted(tmp_path, _state())
    broker._publish_workflow_trigger = AsyncMock()

    await broker._run_workflow_trigger_task()

    broker._publish_workflow_trigger.assert_not_awaited()


def test_gate_frames_add_nothing_to_the_transcript():
    frame = _HistoryFrame(
        session_id="session",
        seq=1,
        kind=WORKFLOW_GATE_FRAME,
        payload={"type": WORKFLOW_GATE_FRAME, "gate": asdict(_state())},
        ts=datetime(2026, 10, 30),
    )

    assert reduce_frames([frame]).turns == []
