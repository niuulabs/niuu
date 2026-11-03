"""Residents' notifications: the same feed, dedupe, delivery rules and live events as a
session's, anchored to the resident and its channel instead of a Forge session."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from niuu.domain.models import Principal
from niuu.domain.notifications import NotificationDraft, NotificationKind, notification_id
from tests.conftest import MockEventBroadcaster
from tests.support.notifications import InMemoryNotificationStore
from volundr.domain.models import (
    EventType,
    ResidentBackend,
    ResidentEngine,
    ResidentRuntime,
)
from volundr.domain.notifications import (
    NotificationQuery,
    NotificationSinkInfo,
    NotificationValidationError,
)
from volundr.domain.services.notifications import (
    NotificationService,
    resident_attention_dedupe_key,
    resident_notify_dedupe_key,
)

OWNER = Principal(user_id="owner-a", email="", tenant_id="tenant-a", roles=["volundr:developer"])
OTHER = Principal(user_id="owner-b", email="", tenant_id="tenant-a", roles=["volundr:developer"])
CHANNEL = UUID("6f1c2b3a-4d5e-4f60-8a9b-0c1d2e3f4a5b")


def _resident(**update) -> ResidentRuntime:
    resident = ResidentRuntime(
        owner_id="owner-a",
        tenant_id="tenant-a",
        name="Muninn",
        model="gpt-5.6-sol",
        backend=ResidentBackend.HELMRELEASE,
        engine=ResidentEngine.RAVN,
        profile_id="ravn-helm",
    )
    return resident.model_copy(update=update)


def _service(*, broadcaster=None) -> tuple[NotificationService, InMemoryNotificationStore]:
    store = InMemoryNotificationStore()
    service = NotificationService(
        store.feed,
        store.rule_repo,
        store.outbox,
        image_repository=store.images,
        image_scheduler=store.image_scheduler,
        broadcaster=broadcaster,
        reply_ready_enabled=False,
        reply_title_chars=40,
        reply_body_chars=60,
        sinks=[NotificationSinkInfo(name="push", label="Push")],
    )
    return service, store


class TestResidentAttention:
    async def test_a_waiting_resident_raises_one_needs_input_item_per_request(self):
        broadcaster = MockEventBroadcaster()
        service, _ = _service(broadcaster=broadcaster)
        resident = _resident()

        first = await service.record_resident_attention(
            resident,
            resident_session_id=None,
            kind="question",
            prompt=" Which DB? ",
            request_id="q1",
        )
        repeat = await service.record_resident_attention(
            resident, resident_session_id=None, kind="question", prompt="Which DB?", request_id="q1"
        )
        other = await service.record_resident_attention(
            resident, resident_session_id=CHANNEL, kind="permission", prompt="", request_id="q1"
        )

        assert repeat is None, "a re-published question is the same notification"
        assert other is not None, "the same request id in another channel is another wait"
        assert (first.kind, first.severity, first.source) == ("attention", "warning", "system")
        assert first.title == "Muninn needs your input" and first.body == "Which DB?"
        assert first.id == notification_id(resident_attention_dedupe_key(resident.id, None, "q1"))
        assert (first.owner_id, first.tenant_id) == ("owner-a", "tenant-a")
        assert first.session_id is None and first.session_name == "Muninn"
        assert (first.engine, first.model) == ("ravn", "gpt-5.6-sol")
        assert other.body == "The agent needs permission to continue"
        assert len(broadcaster.events) == 2
        assert broadcaster.events[0].type is EventType.SESSION_NOTIFICATION

    async def test_the_wire_anchors_it_to_the_resident_and_its_channel(self):
        service, _ = _service()
        resident = _resident()
        room = await service.record_resident_attention(
            resident, resident_session_id=None, kind="question", prompt="A?", request_id="q1"
        )
        channel = await service.record_resident_attention(
            resident, resident_session_id=CHANNEL, kind="question", prompt="B?", request_id="q2"
        )

        assert room.wire()["resident_id"] == str(resident.id)
        assert room.wire()["resident_session_id"] is None
        assert room.wire()["session_id"] is None
        assert channel.wire()["resident_session_id"] == str(CHANNEL)
        assert "metadata" not in channel.wire()

    async def test_the_owners_needs_input_push_rule_delivers_it(self):
        service, store = _service()
        await service.ensure_attention_push_rule("owner-a", sink="push")
        await service.ensure_attention_push_rule("owner-b", sink="push")

        created = await service.record_resident_attention(
            _resident(), resident_session_id=None, kind="question", prompt="A?", request_id="q1"
        )

        [delivery] = store.deliveries.values()
        assert delivery.notification_id == created.id and delivery.sink == "push"

    async def test_only_the_owner_sees_it(self):
        service, _ = _service()
        await service.record_resident_attention(
            _resident(), resident_session_id=None, kind="question", prompt="A?", request_id="q1"
        )
        mine = await service.list_feed(OWNER, NotificationQuery(limit=10))
        theirs = await service.list_feed(OTHER, NotificationQuery(limit=10))
        assert len(mine.items) == 1 and theirs.items == []

    async def test_an_answer_retires_exactly_its_requests(self):
        service, _ = _service()
        resident = _resident()
        q1 = await service.record_resident_attention(
            resident, resident_session_id=None, kind="question", prompt="A?", request_id="q1"
        )
        q2 = await service.record_resident_attention(
            resident, resident_session_id=None, kind="question", prompt="B?", request_id="q2"
        )
        elsewhere = await service.record_resident_attention(
            _resident(), resident_session_id=None, kind="question", prompt="C?", request_id="q1"
        )

        retired = await service.retire_resident_attention(
            resident, resident_session_id=None, request_ids=["q1", "never-raised"]
        )

        assert retired == 1
        unread = await service.list_feed(OWNER, NotificationQuery(limit=10, unread=True))
        assert {item.id for item in unread.items} == {q2.id, elsewhere.id}
        assert q1.id not in {item.id for item in unread.items}
        again = await service.retire_resident_attention(
            resident, resident_session_id=None, request_ids=["q1"]
        )
        assert again == 0, "retiring is idempotent"

    async def test_retiring_in_another_channel_leaves_the_room_alone(self):
        service, _ = _service()
        resident = _resident()
        await service.record_resident_attention(
            resident, resident_session_id=None, kind="question", prompt="A?", request_id="q1"
        )
        assert (
            await service.retire_resident_attention(
                resident, resident_session_id=CHANNEL, request_ids=["q1"]
            )
            == 0
        )

    def test_a_request_id_is_required(self):
        with pytest.raises(ValueError, match="request_id"):
            resident_attention_dedupe_key(uuid4(), None, "")


class TestResidentNotify:
    async def test_the_agent_notifies_as_the_resident(self):
        service, _ = _service()
        resident = _resident()
        draft = NotificationDraft(kind=NotificationKind.MILESTONE, title="Report ready")

        created, is_new = await service.submit_for_resident(
            resident, draft, resident_session_id=CHANNEL, idempotency_key="k1"
        )
        again, again_new = await service.submit_for_resident(
            resident, draft, resident_session_id=CHANNEL, idempotency_key="k1"
        )

        assert is_new and not again_new and again.id == created.id
        assert created.id == notification_id(resident_notify_dedupe_key(resident.id, CHANNEL, "k1"))
        assert (created.kind, created.source) == ("milestone", "agent")
        assert created.wire()["resident_id"] == str(resident.id)
        assert created.wire()["resident_session_id"] == str(CHANNEL)
        assert created.metadata["submitted_by"] == f"resident:{resident.id}"

    async def test_kinds_forge_derives_are_refused(self):
        service, _ = _service()
        draft = NotificationDraft(kind=NotificationKind.REPLY_READY, title="Done")
        with pytest.raises(NotificationValidationError, match="derived by Forge"):
            await service.submit_for_resident(
                _resident(), draft, resident_session_id=None, idempotency_key="k"
            )

    def test_an_idempotency_key_is_required(self):
        with pytest.raises(ValueError, match="idempotency_key"):
            resident_notify_dedupe_key(uuid4(), None, "")


class TestSessionNotificationsAreUnchanged:
    async def test_a_sessions_notification_has_no_resident_fields(self):
        from volundr.domain.models import Session

        service, _ = _service()
        session = Session(name="fix-auth", owner_id="owner-a", tenant_id="tenant-a")
        created = await service.record_attention(
            session,
            state_since=datetime(2026, 10, 1, tzinfo=UTC),
            kind="question",
            prompt="A?",
            request_id="q1",
        )
        wire = created.wire()
        assert wire["session_id"] == str(session.id)
        assert wire["resident_id"] is None and wire["resident_session_id"] is None
        assert created.metadata == {"input_kind": "question", "request_id": "q1"}
