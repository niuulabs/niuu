"""A workflow gate reaches its owner through the log projection, and its decision settles it."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from niuu.domain.models import Principal
from niuu.domain.notifications import NotificationKind, NotificationSeverity, NotificationSource
from niuu.domain.workflow_gate import WORKFLOW_GATE_FRAME
from tests.conftest import MockEventBroadcaster
from tests.support.notifications import InMemoryNotificationStore
from volundr.domain.models import EventType, Session, SessionLogEntry
from volundr.domain.notifications import (
    Notification,
    NotificationQuery,
    NotificationRuleMatch,
    NotificationRuleSpec,
    NotificationSinkInfo,
)
from volundr.domain.projects import SessionCoordination
from volundr.domain.services.notifications import NotificationService
from volundr.domain.workflow_gate import (
    WORKFLOW_GATE_INPUT_KIND,
    is_workflow_gate_frame,
    workflow_gate_from_log_payload,
)


@dataclass
class WorkflowGateState:
    """The gate payload of a ``workflow_gate`` session-log frame, field for field."""

    id: str
    node_id: str
    activation_id: str
    label: str
    condition: str
    status: str
    mode: str
    pending_behavior: str
    instructions: str
    auto_forward_after: str
    requested_at: str
    updated_at: str
    triggered_by_event_type: str
    approval_event_type: str
    changes_requested_event_type: str
    attempt: int = 1
    decision: str | None = None
    notes: str = ""
    source: str = "workflow"
    summary: str = ""
    decided_by: str = ""


OWNER = Principal(user_id="owner-a", email="", tenant_id="tenant-a", roles=["volundr:developer"])
GATE_ID = "result-review-gate:activation:1"
PROJECT_ID = uuid4()


def _session(**overrides) -> Session:
    values = {
        "name": "work-niu-1",
        "owner_id": "owner-a",
        "tenant_id": "tenant-a",
        "coordination": SessionCoordination(project_id=PROJECT_ID, role="coordinator"),
    }
    return Session(**{**values, **overrides})


def _service(store=None, *, broadcaster=None, reply_ready=False):
    store = store or InMemoryNotificationStore()
    service = NotificationService(
        store.feed,
        store.rule_repo,
        store.outbox,
        image_repository=store.images,
        image_scheduler=store.image_scheduler,
        broadcaster=broadcaster,
        reply_ready_enabled=reply_ready,
        reply_title_chars=40,
        reply_body_chars=60,
        sinks=[NotificationSinkInfo(name="push", label="Push")],
    )
    return service, store


def _gate(**overrides) -> WorkflowGateState:
    fields = {
        "id": GATE_ID,
        "node_id": "result-review-gate",
        "activation_id": "activation",
        "label": "Review the result",
        "condition": "The approved result finishes the ticket.",
        "status": "pending",
        "mode": "human_approval",
        "pending_behavior": "help_needed",
        "instructions": "Read the result against the ticket.",
        "auto_forward_after": "30m",
        "requested_at": "2026-11-02T10:00:00+00:00",
        "updated_at": "2026-11-02T10:00:00+00:00",
        "triggered_by_event_type": "ticket.work.finished",
        "approval_event_type": "ticket.work.completed",
        "changes_requested_event_type": "ticket.work.changes_requested",
        "summary": "The login fix is ready.",
    }
    return WorkflowGateState(**{**fields, **overrides})


def _decided(status: str = "approved", **overrides) -> WorkflowGateState:
    return _gate(status=status, summary="Review the result approved by Ada.", **overrides)


def _entry(session: Session, seq: int, gate: WorkflowGateState) -> SessionLogEntry:
    return _raw_entry(session, seq, {"type": WORKFLOW_GATE_FRAME, "gate": asdict(gate)})


def _raw_entry(session: Session, seq: int, payload: dict) -> SessionLogEntry:
    return SessionLogEntry(
        session_id=session.id,
        seq=seq,
        kind=WORKFLOW_GATE_FRAME,
        payload=payload,
        ts=datetime(2026, 11, 2, 10, tzinfo=UTC),
    )


def _notification_events(broadcaster: MockEventBroadcaster) -> list:
    return [e for e in broadcaster.events if e.type is EventType.SESSION_NOTIFICATION]


async def _open_items() -> tuple[NotificationService, InMemoryNotificationStore, Session]:
    service, store = _service()
    session = _session()
    await service.project_log_entries(session, [_entry(session, 1, _gate())])
    return service, store, session


async def _unread(service: NotificationService) -> list[Notification]:
    return (await service.list_feed(OWNER, NotificationQuery(limit=10, unread=True))).items


class TestGateOpens:
    async def test_raises_one_attention_item_the_client_can_route_to_the_gate(self):
        broadcaster = MockEventBroadcaster()
        service, _ = _service(broadcaster=broadcaster)
        session = _session()

        [item] = await service.project_log_entries(session, [_entry(session, 7, _gate())])

        assert item.kind is NotificationKind.ATTENTION
        assert item.severity is NotificationSeverity.WARNING
        assert item.source is NotificationSource.SYSTEM
        assert item.session_id == session.id
        assert item.session_seq == 7
        assert item.title == "work-niu-1: Review the result"
        assert item.body == "The login fix is ready."
        assert item.input_request == {"kind": WORKFLOW_GATE_INPUT_KIND, "request_id": GATE_ID}
        assert item.wire()["input_request"] == item.input_request
        [event] = _notification_events(broadcaster)
        assert event.data["id"] == str(item.id)

    async def test_carries_the_project_so_project_rules_and_the_feed_filter_find_it(self):
        service, store = _service()
        await service.create_rule(
            OWNER,
            NotificationRuleSpec(
                name="This project",
                match=NotificationRuleMatch(project_ids=[str(PROJECT_ID)]),
                sink="push",
            ),
        )
        session = _session()

        [item] = await service.project_log_entries(session, [_entry(session, 1, _gate())])

        assert item.project_id == str(PROJECT_ID)
        query = NotificationQuery(limit=10, project_id=str(PROJECT_ID))
        assert [n.id for n in (await service.list_feed(OWNER, query)).items] == [item.id]
        assert [d.sink for d in await service.list_deliveries(OWNER, item.id)] == ["push"]

    async def test_a_session_outside_a_project_still_raises_it(self):
        service, _ = _service()
        session = _session(coordination=None)

        [item] = await service.project_log_entries(session, [_entry(session, 1, _gate())])

        assert item.project_id is None

    async def test_reaches_the_owner_through_the_needs_input_push_rule(self):
        service, _ = _service()
        await service.ensure_attention_push_rule("owner-a", "push")
        session = _session()

        [item] = await service.project_log_entries(session, [_entry(session, 1, _gate())])

        assert [d.sink for d in await service.list_deliveries(OWNER, item.id)] == ["push"]

    @pytest.mark.parametrize(
        ("overrides", "body"),
        [
            ({"summary": ""}, "Read the result against the ticket."),
            ({"summary": " ", "instructions": ""}, "A review is waiting for your decision"),
        ],
    )
    async def test_a_gate_with_no_summary_still_says_something(self, overrides, body):
        service, _ = _service()
        session = _session()

        [item] = await service.project_log_entries(
            session, [_entry(session, 1, _gate(**overrides))]
        )

        assert item.body == body

    async def test_an_unlabelled_gate_is_named_by_its_node(self):
        service, _ = _service()
        session = _session()

        [item] = await service.project_log_entries(session, [_entry(session, 1, _gate(label=""))])

        assert item.title == "work-niu-1: result-review-gate"

    async def test_long_labels_and_summaries_are_bounded_like_any_attention_item(self):
        service, _ = _service()
        session = _session()
        gate = _gate(label="l" * 300, summary="s" * 5000)

        [item] = await service.project_log_entries(session, [_entry(session, 1, gate)])

        assert len(item.title) == 200 and len(item.body) == 4000

    @pytest.mark.parametrize(
        "overrides",
        [
            {"mode": "human_review"},
            {"pending_behavior": "notify_only"},
        ],
    )
    async def test_every_gate_a_person_is_told_about_raises_one(self, overrides):
        service, _ = _service()
        session = _session()

        created = await service.project_log_entries(
            session, [_entry(session, 1, _gate(**overrides))]
        )

        assert len(created) == 1

    @pytest.mark.parametrize(
        "overrides",
        [
            {"mode": "automated_approval"},
            {"pending_behavior": "silent"},
        ],
    )
    async def test_a_gate_no_person_is_waited_on_for_raises_nothing(self, overrides):
        broadcaster = MockEventBroadcaster()
        service, store = _service(broadcaster=broadcaster)
        session = _session()

        await service.project_log_entries(session, [_entry(session, 1, _gate(**overrides))])
        await service.project_log_entries(session, [_entry(session, 2, _decided(**overrides))])

        assert not store.notifications
        assert not _notification_events(broadcaster)

    async def test_is_projectable_whatever_the_reply_setting(self):
        service, _ = _service(reply_ready=False)

        assert service.is_projectable({"type": WORKFLOW_GATE_FRAME, "gate": {}})


class TestRestartsDoNotDuplicate:
    async def test_a_retried_frame_records_and_broadcasts_nothing_more(self):
        broadcaster = MockEventBroadcaster()
        service, store = _service(broadcaster=broadcaster)
        session = _session()
        frame = _entry(session, 1, _gate())

        await service.project_log_entries(session, [frame])
        assert await service.project_log_entries(session, [frame]) == []

        assert len(store.notifications) == 1
        assert len(_notification_events(broadcaster)) == 1

    async def test_a_restarted_forge_or_broker_replaying_the_gate_keeps_one_item(self):
        service, store = _service()
        session = _session()
        await service.project_log_entries(session, [_entry(session, 1, _gate())])

        restarted, _ = _service(store)

        # A new seq, as a broker re-logging the restored gate would use.
        assert await restarted.project_log_entries(session, [_entry(session, 9, _gate())]) == []
        assert len(store.notifications) == 1

    async def test_replaying_a_settled_gate_does_not_reopen_it(self):
        service, store, session = await _open_items()
        await service.project_log_entries(session, [_entry(session, 2, _decided())])

        await service.project_log_entries(session, [_entry(session, 1, _gate())])

        [item] = store.notifications.values()
        assert item.resolved

    async def test_the_same_gate_in_another_session_is_its_own_item(self):
        service, store = _service()
        first, second = _session(), _session(name="other")

        await service.project_log_entries(first, [_entry(first, 1, _gate())])
        await service.project_log_entries(second, [_entry(second, 1, _gate())])

        assert len(store.notifications) == 2


class TestDecidingSettlesIt:
    @pytest.mark.parametrize("status", ["approved", "changes_requested"])
    async def test_a_decision_resolves_and_reads_the_item(self, status):
        broadcaster = MockEventBroadcaster()
        service, store = _service(broadcaster=broadcaster)
        session = _session()
        [item] = await service.project_log_entries(session, [_entry(session, 1, _gate())])

        assert (
            await service.project_log_entries(session, [_entry(session, 2, _decided(status))]) == []
        )

        [settled] = store.notifications.values()
        assert settled.id == item.id
        assert settled.resolved
        assert settled.resolved_by_notification_id is None
        assert settled.resolution == "Review the result approved by Ada."
        assert await _unread(service) == []
        open_items = NotificationQuery(limit=10, unresolved=True)
        assert (await service.list_feed(OWNER, open_items)).items == []
        events = _notification_events(broadcaster)
        assert [event.data["id"] for event in events] == [str(item.id)] * 2
        assert events[-1].data["resolved"] is True

    async def test_a_retried_decision_changes_nothing_more(self):
        broadcaster = MockEventBroadcaster()
        service, store = _service(broadcaster=broadcaster)
        session = _session()
        await service.project_log_entries(session, [_entry(session, 1, _gate())])
        decision = _entry(session, 2, _decided())

        await service.project_log_entries(session, [decision])
        await service.project_log_entries(session, [decision])

        assert len(_notification_events(broadcaster)) == 2
        assert len(store.notifications) == 1

    async def test_a_gate_opened_and_decided_in_one_batch_leaves_one_settled_item(self):
        service, store = _service()
        session = _session()

        await service.project_log_entries(
            session, [_entry(session, 2, _decided()), _entry(session, 1, _gate())]
        )

        [item] = store.notifications.values()
        assert item.resolved

    async def test_a_gate_sent_back_and_opened_again_asks_again(self):
        service, store, session = await _open_items()
        again = "result-review-gate:activation:2"

        await service.project_log_entries(
            session, [_entry(session, 2, _decided("changes_requested"))]
        )
        [second] = await service.project_log_entries(
            session, [_entry(session, 3, _gate(id=again, attempt=2))]
        )

        assert second.input_request["request_id"] == again
        assert not second.resolved
        assert [n.id for n in await _unread(service)] == [second.id]
        assert sum(n.resolved for n in store.notifications.values()) == 1

    async def test_deciding_a_gate_settles_no_other_gate(self):
        service, store = _service()
        session = _session()
        other = _gate(id="work-blocked:activation:1", node_id="work-blocked")
        await service.project_log_entries(
            session, [_entry(session, 1, _gate()), _entry(session, 2, other)]
        )

        await service.project_log_entries(session, [_entry(session, 3, _decided())])

        [waiting] = await _unread(service)
        assert waiting.input_request["request_id"] == other.id
        assert not waiting.resolved

    async def test_a_decision_with_no_item_records_nothing(self):
        """A gate opened before Forge projected gates has no item to settle."""
        service, store = _service()
        session = _session()

        await service.project_log_entries(session, [_entry(session, 1, _decided())])

        assert not store.notifications

    async def test_an_item_resolved_by_hand_stays_as_it_was(self):
        service, store = _service()
        session = _session()
        [item] = await service.project_log_entries(session, [_entry(session, 1, _gate())])
        await service.resolve(OWNER, item.id, resolution="Handled elsewhere")

        await service.project_log_entries(session, [_entry(session, 2, _decided())])

        [settled] = store.notifications.values()
        assert settled.resolution == "Handled elsewhere"


class TestSessionTurnsLeaveItAlone:
    async def test_a_question_retiring_does_not_read_the_gate_still_waiting(self):
        service, _, session = await _open_items()
        question = await service.record_attention(
            session,
            state_since=datetime(2026, 11, 2, 11, tzinfo=UTC),
            kind="question",
            prompt="Which DB?",
            request_id="q1",
        )

        assert await service.retire_attention(session) == 1

        [waiting] = await _unread(service)
        assert waiting.id != question.id
        assert waiting.input_request["kind"] == WORKFLOW_GATE_INPUT_KIND


class TestUnreadableFrames:
    @pytest.mark.parametrize(
        "gate",
        [None, "not-a-gate", {"status": "pending"}, {"id": GATE_ID}, {"id": " ", "status": "x"}],
    )
    async def test_are_loud_and_never_hold_back_the_rest_of_the_batch(self, gate, caplog):
        service, store = _service()
        session = _session()
        observability = MagicMock()
        broken = _raw_entry(session, 1, {"type": WORKFLOW_GATE_FRAME, "gate": gate})

        with patch(
            "volundr.domain.services.notifications.get_observability",
            return_value=observability,
        ):
            created = await service.project_log_entries(
                session, [broken, _entry(session, 2, _gate())]
            )

        assert len(created) == 1
        assert len(store.notifications) == 1
        observability.count.assert_called_once_with(
            "volundr.notifications.workflow_gate_frame_malformed"
        )
        assert any(
            record.levelname == "ERROR" and "unreadable workflow gate" in record.getMessage()
            for record in caplog.records
        )

    def test_other_frames_are_not_gates(self):
        assert not is_workflow_gate_frame({"type": "room_outcome"})
        assert not is_workflow_gate_frame("workflow_gate")  # type: ignore[arg-type]


class TestReadsTheLoggedFrame:
    def test_a_logged_frame_parses_field_for_field(self):
        state = _gate(status="changes_requested")

        gate = workflow_gate_from_log_payload({"type": WORKFLOW_GATE_FRAME, "gate": asdict(state)})

        assert (gate.id, gate.status, gate.mode) == (state.id, state.status, state.mode)
        assert gate.node_id == state.node_id
        assert gate.activation_id == state.activation_id
        assert gate.pending_behavior == state.pending_behavior
        assert not gate.pending
