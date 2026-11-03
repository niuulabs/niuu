"""Structured asks and runtime requests on the notification feed (LEX-1486).

Two kinds of "needs you" items share the feed and stay distinct:

- an agent's notice with ``question``/``ask`` (F11b, LEX-1412): answered by a message
  to the session; the ask must reach the read wire and the live event intact;
- Forge's system ``attention`` item for a runtime request (AskUserQuestion,
  confirmation, permission): ``input_request`` names the pending request, answered on
  the session socket where the transcript's card answers it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from niuu.domain.guild_membership import GuildInstance
from niuu.domain.notifications import NotificationDraft
from niuu.domain.resident_notifications import ResidentNotificationEntry
from tests.conftest import MockEventBroadcaster
from tests.support.notifications import InMemoryNotificationStore
from tests.test_domain.test_notification_service import OWNER, _session, _turn_entry
from volundr.domain.models import EventType
from volundr.domain.notifications import NotificationSinkInfo
from volundr.domain.services.notifications import NotificationService

ASK = {
    "items": [
        {
            "id": "database",
            "kind": "question",
            "text": "Which database?",
            "header": "Storage",
            "options": [
                {"label": "Postgres", "description": "the shared cluster"},
                {"label": "SQLite"},
            ],
            "multi_select": False,
        },
        {"id": "access", "kind": "action", "text": "Grant the test account access."},
    ]
}

GUILD = GuildInstance(id="i-thor", name="Thor", slug="thor")


def _service(broadcaster: MockEventBroadcaster) -> NotificationService:
    store = InMemoryNotificationStore()
    return NotificationService(
        store.feed,
        store.rule_repo,
        store.outbox,
        image_repository=store.images,
        image_scheduler=store.image_scheduler,
        broadcaster=broadcaster,
        reply_ready_enabled=False,
        reply_title_chars=40,
        reply_body_chars=60,
        sinks=[NotificationSinkInfo(name="ops", label="Ops")],
        engine_resolver=lambda session: "claude",
        guild_instance=GUILD,
    )


def _live_event(broadcaster: MockEventBroadcaster) -> dict:
    [event] = [e for e in broadcaster.events if e.type is EventType.SESSION_NOTIFICATION]
    return event.data


class TestAgentAsk:
    async def test_projected_ask_reaches_the_wire_and_the_live_event(self):
        broadcaster = MockEventBroadcaster()
        service = _service(broadcaster)
        session = _session()

        [created] = await service.project_log_entries(
            session,
            [
                _turn_entry(
                    session,
                    7,
                    "t-ask",
                    kind="attention",
                    title="Pick the storage",
                    question=True,
                    ask=ASK,
                )
            ],
        )

        wire = created.wire()
        assert wire["question"] is True
        assert wire["ask"] == ASK
        assert wire["about_question"] is None
        assert wire["input_request"] is None
        event = _live_event(broadcaster)
        assert event["ask"] == ASK and event["question"] is True
        assert {key: event[key] for key in GUILD.fields()} == GUILD.fields()

    async def test_submitted_ask_keeps_its_items_and_omitted_defaults(self):
        broadcaster = MockEventBroadcaster()
        service = _service(broadcaster)
        draft = NotificationDraft(
            kind="attention",
            title="Two things before I deploy",
            question=True,
            ask={"items": [{"id": "go", "kind": "question", "text": "Deploy now?"}]},
        )

        created, _ = await service.submit(_session(), OWNER, draft, idempotency_key="k-ask")

        # Omitted flags stay omitted: the client defaults (multi_select=false,
        # allow_text=true) apply, and nothing reads as an explicit null.
        assert created.wire()["ask"] == {
            "items": [{"id": "go", "kind": "question", "text": "Deploy now?"}]
        }
        assert _live_event(broadcaster)["ask"] == created.wire()["ask"]

    async def test_relay_signal_is_served_as_sent(self):
        service = _service(MockEventBroadcaster())
        ref = f"question:thor:{uuid4()}"
        draft = NotificationDraft(
            kind="attention",
            title="Worker asks: which database?",
            about_question={"id": ref, "action": "relay"},
        )

        created, _ = await service.submit(_session(), OWNER, draft, idempotency_key="k-relay")

        assert created.wire()["about_question"] == {"id": ref, "action": "relay"}
        assert created.wire()["question"] is None and created.wire()["ask"] is None


class TestRuntimeRequest:
    async def test_attention_item_names_the_pending_request(self):
        broadcaster = MockEventBroadcaster()
        service = _service(broadcaster)

        created = await service.record_attention(
            _session(),
            state_since=datetime(2026, 10, 26, 9, tzinfo=UTC),
            kind="question",
            prompt="Which DB?",
            request_id="toolu_01",
        )

        assert created.wire()["input_request"] == {"kind": "question", "request_id": "toolu_01"}
        # A runtime request is not an F11b notice question: its card answers it.
        assert created.wire()["question"] is None and created.wire()["ask"] is None
        event = _live_event(broadcaster)
        assert event["input_request"] == {"kind": "question", "request_id": "toolu_01"}
        assert {key: event[key] for key in GUILD.fields()} == GUILD.fields()

    async def test_a_request_without_id_is_served_with_a_null_id(self):
        service = _service(MockEventBroadcaster())

        created = await service.record_attention(
            _session(),
            state_since=datetime(2026, 10, 26, 9, tzinfo=UTC),
            kind="permission",
            prompt="",
            request_id="",
        )

        assert created.wire()["input_request"] == {"kind": "permission", "request_id": None}

    async def test_an_agent_attention_notice_has_no_input_request(self):
        service = _service(MockEventBroadcaster())
        draft = NotificationDraft(kind="attention", title="Blocked on CI")

        created, _ = await service.submit(_session(), OWNER, draft, idempotency_key="k-block")

        assert created.wire()["input_request"] is None


class TestResidentOutbox:
    def test_a_queued_ask_survives_the_outbox_round_trip(self):
        draft = NotificationDraft(kind="attention", title="Pick one", question=True, ask=ASK)
        entry = ResidentNotificationEntry(
            seq=1,
            op="notify",
            draft=draft,
            idempotency_key="k1",
            created_at=datetime(2026, 10, 26, tzinfo=UTC),
        )

        # The Ravn outbox stores the entry as JSON; Forge's relay parses it back.
        restored = ResidentNotificationEntry.model_validate_json(entry.model_dump_json())

        assert restored.draft.question is True
        assert restored.draft.question_metadata()["ask"] == ASK
