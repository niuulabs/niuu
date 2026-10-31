"""NotificationService: projection, dedupe, feed scoping, watermarks, rules and sinks."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from niuu.domain.models import IntegrationConnection, IntegrationType, Principal
from niuu.domain.notifications import (
    NotificationDraft,
    NotificationKind,
    build_notification_turn,
    notification_id,
    turn_dedupe_key,
)
from tests.conftest import MockEventBroadcaster
from tests.support.notifications import InMemoryNotificationStore
from volundr.domain.models import EventType, Session, SessionLogEntry
from volundr.domain.notifications import (
    NotificationNotFoundError,
    NotificationQuery,
    NotificationReadStateConflictError,
    NotificationRuleMatch,
    NotificationRuleSpec,
    NotificationSinkInfo,
    NotificationValidationError,
)
from volundr.domain.ports import IntegrationRepository
from volundr.domain.projects import SessionCoordination
from volundr.domain.services.notifications import NotificationService

OWNER = Principal(user_id="owner-a", email="", tenant_id="tenant-a", roles=["volundr:developer"])
OTHER = Principal(user_id="owner-b", email="", tenant_id="tenant-a", roles=["volundr:developer"])
ADMIN = Principal(user_id="admin", email="", tenant_id="tenant-a", roles=["volundr:admin"])


class FakeIntegrations(IntegrationRepository):
    def __init__(self, connections: list[IntegrationConnection]) -> None:
        self.connections = connections

    async def list_connections(self, owner_id, integration_type=None):
        return [
            c
            for c in self.connections
            if c.owner_id == owner_id
            and (integration_type is None or c.integration_type == integration_type)
        ]

    async def list_connections_global(
        self, integration_type=None, *, slug=None, enabled_only=False
    ):
        return list(self.connections)

    async def get_connection(self, connection_id):
        return next((c for c in self.connections if c.id == connection_id), None)

    async def save_connection(self, connection):
        return connection

    async def delete_connection(self, connection_id):
        return None


def _connection(conn_id: str, *, owner="owner-a", kind=IntegrationType.MESSAGING, enabled=True):
    now = datetime.now(UTC)
    return IntegrationConnection(
        id=conn_id,
        owner_id=owner,
        integration_type=kind,
        adapter="x.Y",
        credential_name="c",
        config={},
        enabled=enabled,
        created_at=now,
        updated_at=now,
    )


def _session(**overrides) -> Session:
    values = {
        "name": "fix-auth",
        "model": "claude-opus-5",
        "owner_id": "owner-a",
        "tenant_id": "tenant-a",
        "session_definition": "skuldClaude",
    }
    values.update(overrides)
    return Session(**values)


def _service(store=None, *, broadcaster=None, reply_ready=True, integrations=None):
    store = store or InMemoryNotificationStore()
    service = NotificationService(
        store.feed,
        store.rule_repo,
        store.outbox,
        broadcaster=broadcaster,
        reply_ready_enabled=reply_ready,
        reply_title_chars=40,
        reply_body_chars=60,
        sinks=[NotificationSinkInfo(name="ops", label="Ops")],
        engine_resolver=lambda session: "claude",
        integration_repository=integrations,
    )
    return service, store


def _turn_entry(session: Session, seq: int, turn_id: str, **draft) -> SessionLogEntry:
    values = {"kind": "milestone", "title": "Tests green", "body": "All 42 pass"}
    values.update(draft)
    turn = build_notification_turn(
        NotificationDraft(**values),
        turn_id=turn_id,
        session_id=session.id,
        created_at="2026-09-23T10:00:00Z",
    )
    return SessionLogEntry(
        session_id=session.id,
        seq=seq,
        kind="conversation.turn",
        payload={"type": "conversation.turn", "turn": turn},
        ts=datetime.now(UTC),
    )


def _final_entry(session: Session, seq: int, turn_id: str, content: str) -> SessionLogEntry:
    turn = {
        "id": turn_id,
        "role": "assistant",
        "content": content,
        "metadata": {"final_output": True},
    }
    return SessionLogEntry(
        session_id=session.id,
        seq=seq,
        kind="conversation.turn",
        payload={"type": "conversation.turn", "turn": turn},
        ts=datetime.now(UTC),
    )


class TestProjection:
    async def test_agent_turn_becomes_owned_notification_with_shared_id(self):
        broadcaster = MockEventBroadcaster()
        service, store = _service(broadcaster=broadcaster)
        project = uuid4()
        session = _session(coordination=SessionCoordination(project_id=project))
        entry = _turn_entry(session, 7, "nt_" + "a" * 32, links=[{"label": "PR", "url": "u"}])

        [created] = await service.project_log_entries(session, [entry])

        payload_id = entry.payload["turn"]["metadata"]["notification"]["notification_id"]
        assert str(created.id) == payload_id
        assert created.dedupe_key == turn_dedupe_key(session.id, "nt_" + "a" * 32, "milestone")
        assert (created.source, created.session_seq, created.session_name) == (
            "agent",
            7,
            "fix-auth",
        )
        assert (created.owner_id, created.tenant_id, created.project_id) == (
            "owner-a",
            "tenant-a",
            str(project),
        )
        assert (created.engine, created.model) == ("claude", "claude-opus-5")
        assert created.links[0].label == "PR"
        [event] = broadcaster.events
        assert event.type is EventType.SESSION_NOTIFICATION
        assert event.data == created.wire()

    async def test_replayed_batch_creates_nothing_and_broadcasts_nothing(self):
        broadcaster = MockEventBroadcaster()
        service, store = _service(broadcaster=broadcaster)
        session = _session()
        await store.rule_repo.create(_rule_for("owner-a"))
        entries = [_turn_entry(session, 1, "t1"), _final_entry(session, 2, "t2", "Done")]

        first = await service.project_log_entries(session, entries)
        again = await service.project_log_entries(session, entries)

        assert len(first) == 2 and again == []
        assert len(store.notifications) == 2
        assert len(store.deliveries) == 2
        assert len(broadcaster.events) == 2

    async def test_reply_ready_summarizes_final_output(self):
        service, _ = _service()
        session = _session()
        content = "## All done\n\n" + "x" * 200
        [reply] = await service.project_log_entries(
            session, [_final_entry(session, 3, "turn-final", content)]
        )
        assert reply.kind is NotificationKind.REPLY_READY
        assert (reply.severity, reply.source, reply.session_seq) == ("success", "system", 3)
        assert reply.title == "All done"
        assert len(reply.body) == 60 and reply.body.endswith("…")
        assert reply.id == notification_id(turn_dedupe_key(session.id, "turn-final", "reply_ready"))

    async def test_reply_ready_can_be_disabled(self):
        service, _ = _service(reply_ready=False)
        session = _session()
        final = _final_entry(session, 3, "f", "Done")
        assert not service.is_projectable(final.payload)
        assert await service.project_log_entries(session, [final]) == []

    async def test_non_notification_frames_are_ignored(self):
        service, store = _service()
        session = _session()
        chatter = SessionLogEntry(
            session_id=session.id,
            seq=1,
            kind="assistant",
            payload={"type": "assistant"},
            ts=datetime.now(UTC),
        )
        assert not service.is_projectable(chatter.payload)
        assert await service.project_log_entries(session, [chatter]) == []
        assert store.project_calls == 0

    async def test_rows_from_another_session_are_rejected(self):
        service, _ = _service()
        with pytest.raises(ValueError, match="belong"):
            await service.project_log_entries(_session(), [_turn_entry(_session(), 1, "t")])

    async def test_unowned_session_projects_to_empty_owner(self):
        service, _ = _service()
        session = _session(owner_id=None, tenant_id=None)
        [created] = await service.project_log_entries(session, [_turn_entry(session, 1, "t")])
        assert created.owner_id == "" and created.engine == "claude"

    async def test_rules_schedule_deliveries_for_new_rows_only(self):
        service, store = _service()
        session = _session()
        match = await store.rule_repo.create(_rule_for("owner-a", kinds=["milestone"]))
        await store.rule_repo.create(_rule_for("owner-a", kinds=["error"]))
        await store.rule_repo.create(_rule_for("owner-b"))
        [created] = await service.project_log_entries(session, [_turn_entry(session, 1, "t")])
        deliveries = await store.outbox.list_for_notification(created.id)
        assert [d.rule_id for d in deliveries] == [match.id]


def _rule_for(owner: str, **match):
    from volundr.domain.notifications import NotificationRule

    now = datetime.now(UTC)
    return NotificationRule(
        id=uuid4(),
        owner_id=owner,
        name="r",
        sink="ops",
        match=NotificationRuleMatch(**match),
        created_at=now,
        updated_at=now,
    )


class TestAttention:
    async def test_attention_is_recorded_once_per_request(self):
        broadcaster = MockEventBroadcaster()
        service, store = _service(broadcaster=broadcaster)
        session = _session()
        since = datetime(2026, 9, 23, 10, tzinfo=UTC)

        first = await service.record_attention(
            session, state_since=since, kind="question", prompt=" Which DB? ", request_id="q1"
        )
        repeat = await service.record_attention(
            session, state_since=since, kind="question", prompt="Which DB?", request_id="q1"
        )
        second = await service.record_attention(
            session, state_since=since, kind="permission", prompt="", request_id="q2"
        )

        assert repeat is None and second is not None
        assert (first.kind, first.severity, first.source) == ("attention", "warning", "system")
        assert first.title == "fix-auth needs your input" and first.body == "Which DB?"
        assert first.metadata == {"input_kind": "question", "request_id": "q1"}
        assert first.session_seq is None
        assert second.body == "The agent needs permission to continue"  # no prompt
        assert len(broadcaster.events) == 2

    async def test_long_names_and_prompts_are_bounded(self):
        service, _ = _service()
        session = _session(name="n" * 255)
        created = await service.record_attention(
            session,
            state_since=datetime.now(UTC),
            kind="question",
            prompt="p" * 5000,
            request_id="",
        )
        assert len(created.title) == 200 and len(created.body) == 4000


class TestAttentionPushRule:
    """push.enabled: the needs-input push is an owner rule the outbox delivers."""

    async def test_registering_owner_gets_one_rule_for_system_attention(self):
        service, store = _service()
        created = await service.ensure_attention_push_rule("owner-a", sink="ops")
        assert created.name == "Needs-input pushes" and created.sink == "ops"
        assert created.enabled and created.owner_id == "owner-a"
        assert created.match.kinds == ["attention"] and created.match.sources == ["system"]
        assert await service.ensure_attention_push_rule("owner-a", sink="ops") is None
        assert list(store.rules) == [created.id]

        other = await service.ensure_attention_push_rule("owner-b", sink="ops")
        assert other.id != created.id

    async def test_an_owner_who_already_routes_to_the_sink_keeps_their_rules(self):
        service, store = _service()
        own = await service.create_rule(
            OWNER, NotificationRuleSpec(name="mine", sink="ops", enabled=False)
        )
        assert await service.ensure_attention_push_rule("owner-a", sink="ops") is None
        assert list(store.rules) == [own.id]

    async def test_concurrent_registrations_create_one_rule(self):
        service, store = _service()
        rule = await service.ensure_attention_push_rule("owner-a", sink="ops")
        # A second registration that raced past the existence check.
        assert await store.rule_repo.create_if_absent(rule) is None
        assert len(store.rules) == 1

    async def test_unconfigured_sink_is_refused(self):
        service, _ = _service()
        with pytest.raises(NotificationValidationError, match="'push' is not configured"):
            await service.ensure_attention_push_rule("owner-a", sink="push")

    async def test_needs_input_is_scheduled_for_delivery_through_the_rule(self):
        service, store = _service()
        rule = await service.ensure_attention_push_rule("owner-a", sink="ops")
        session = _session()
        attention = await service.record_attention(
            session,
            state_since=datetime.now(UTC),
            kind="question",
            prompt="Which DB?",
            request_id="q1",
        )
        [delivery] = store.deliveries.values()
        assert (delivery.notification_id, delivery.rule_id) == (attention.id, rule.id)
        assert delivery.sink == "ops"


class TestAttentionRetirement:
    async def test_settled_questions_retire_only_their_own_unread_attention(self):
        """Regression (gtc-web-sdk): "needs your input" stayed unread after answers."""
        service, _ = _service()
        session, other = _session(), _session(name="other")
        since = datetime(2026, 9, 30, 12, tzinfo=UTC)
        await service.record_attention(
            session, state_since=since, kind="question", prompt="A?", request_id="q1"
        )
        [reply] = await service.project_log_entries(session, [_turn_entry(session, 1, "t1")])
        q2 = await service.record_attention(
            session, state_since=since, kind="permission", prompt="B?", request_id="q2"
        )
        elsewhere = await service.record_attention(
            other, state_since=since, kind="question", prompt="C?", request_id="q3"
        )
        await service.mark_read(OWNER, q2.id)

        assert await service.retire_attention(session) == 1  # q1; q2 was already read
        feed = await service.list_feed(OWNER, NotificationQuery(limit=10, unread=True))
        assert {n.id for n in feed.items} == {reply.id, elsewhere.id}
        assert await service.retire_attention(session) == 0

    async def test_retirement_pages_through_many_items(self):
        store = InMemoryNotificationStore()
        service = NotificationService(
            store.feed,
            store.rule_repo,
            store.outbox,
            reply_ready_enabled=True,
            reply_title_chars=40,
            reply_body_chars=60,
            sinks=[],
            attention_retire_page_size=2,
        )
        session = _session()
        for i in range(5):
            await service.record_attention(
                session,
                state_since=datetime(2026, 9, 30, 12, tzinfo=UTC),
                kind="question",
                prompt="Q?",
                request_id=f"q{i}",
            )
        assert await service.retire_attention(session) == 5
        with pytest.raises(ValueError, match="positive"):
            NotificationService(
                store.feed,
                store.rule_repo,
                store.outbox,
                reply_ready_enabled=True,
                reply_title_chars=40,
                reply_body_chars=60,
                sinks=[],
                attention_retire_page_size=0,
            )


class TestSubmit:
    async def test_submit_is_idempotent_per_principal_and_key(self):
        service, _ = _service()
        session = _session()
        draft = NotificationDraft(kind="decision", title="Pick A", severity="warning")

        created, new = await service.submit(session, OWNER, draft, idempotency_key="k1")
        again, again_new = await service.submit(session, OWNER, draft, idempotency_key="k1")
        other, other_new = await service.submit(session, OTHER, draft, idempotency_key="k1")

        assert new and not again_new and other_new
        assert again.id == created.id and other.id != created.id
        assert (created.source, created.session_seq) == ("operator", None)
        assert created.metadata == {"submitted_by": "owner-a"}

    async def test_reply_ready_cannot_be_submitted(self):
        service, _ = _service()
        with pytest.raises(NotificationValidationError, match="derived"):
            await service.submit(
                _session(),
                OWNER,
                NotificationDraft(kind="reply_ready", title="x"),
                idempotency_key="k",
            )

    async def test_unreadable_dedupe_is_an_error(self):
        service, store = _service()

        async def nothing(candidates):
            return []

        store.feed.project = nothing
        with pytest.raises(RuntimeError, match="not readable"):
            await service.submit(
                _session(), OWNER, NotificationDraft(kind="info", title="x"), idempotency_key="k"
            )


class TestFeedAndReadState:
    async def _seed(self, service):
        mine, theirs = _session(), _session(owner_id="owner-b")
        await service.project_log_entries(
            mine, [_turn_entry(mine, 1, "a"), _turn_entry(mine, 2, "b")]
        )
        await service.project_log_entries(theirs, [_turn_entry(theirs, 1, "c")])
        return mine

    async def test_owner_isolation_and_admin_scope(self):
        service, _ = _service()
        await self._seed(service)
        mine = await service.list_feed(OWNER, NotificationQuery(limit=10))
        admin = await service.list_feed(ADMIN, NotificationQuery(limit=10))
        assert {n.owner_id for n in mine.items} == {"owner-a"}
        assert len(admin.items) == 3
        assert (mine.head_seq, mine.unread_count, mine.read_through_seq) == (2, 2, 0)

    async def test_watermark_moves_forward_and_counts_unread(self):
        service, _ = _service()
        await self._seed(service)
        state = await service.update_read_state(OWNER, read_through_seq=1, expected_revision=0)
        assert (state.read_through_seq, state.revision, state.unread_count, state.head_seq) == (
            1,
            1,
            1,
            2,
        )
        back = await service.update_read_state(OWNER, read_through_seq=0, expected_revision=1)
        assert back.read_through_seq == 1 and back.revision == 2
        assert (await service.get_read_state(OWNER)).revision == 2
        assert await service.read_through_seq(OWNER) == 1
        unread = await service.list_feed(OWNER, NotificationQuery(limit=10, unread=True))
        assert [n.seq for n in unread.items] == [2]

    async def test_future_and_stale_watermarks_are_rejected(self):
        service, _ = _service()
        await self._seed(service)
        with pytest.raises(NotificationValidationError, match="unseen"):
            await service.update_read_state(OWNER, read_through_seq=3, expected_revision=0)
        await service.update_read_state(OWNER, read_through_seq=2, expected_revision=0)
        with pytest.raises(NotificationReadStateConflictError):
            await service.update_read_state(OWNER, read_through_seq=2, expected_revision=0)

    async def test_session_listing_and_deliveries_visibility(self):
        service, store = _service()
        mine = await self._seed(service)
        items, read_through = await service.list_for_session(OWNER, mine.id, after=1, limit=5)
        assert [n.session_seq for n in items] == [2] and read_through == 0
        target = items[0]
        assert await service.list_deliveries(OWNER, target.id) == []
        assert await service.list_deliveries(ADMIN, target.id) == []
        with pytest.raises(NotificationNotFoundError):
            await service.list_deliveries(OTHER, target.id)
        with pytest.raises(NotificationNotFoundError):
            await service.list_deliveries(OWNER, uuid4())


class TestRulesAndSinks:
    async def test_sinks_include_integration_only_with_enabled_messaging(self):
        integrations = FakeIntegrations(
            [
                _connection("tg", owner="owner-b"),
                _connection("off", enabled=False),
                _connection("jira", kind=IntegrationType.ISSUE_TRACKER),
            ]
        )
        service, _ = _service(integrations=integrations)
        assert [s.name for s in await service.list_sinks(OWNER)] == ["ops"]
        assert [s.name for s in await service.list_sinks(OTHER)] == ["ops", "integration"]
        no_repo, _ = _service()
        assert [s.name for s in await no_repo.list_sinks(OTHER)] == ["ops"]

    async def test_rule_crud_is_owner_scoped(self):
        service, _ = _service()
        rule = await service.create_rule(OWNER, NotificationRuleSpec(name="ops", sink="ops"))
        assert rule.owner_id == "owner-a" and rule.created_at == rule.updated_at
        assert [r.id for r in await service.list_rules(OWNER)] == [rule.id]
        assert await service.list_rules(OTHER) == []

        updated = await service.update_rule(
            OWNER, rule.id, NotificationRuleSpec(name="renamed", sink="ops", enabled=False)
        )
        assert updated.name == "renamed" and updated.created_at == rule.created_at
        with pytest.raises(NotificationNotFoundError):
            await service.update_rule(OTHER, rule.id, NotificationRuleSpec(name="x", sink="ops"))
        with pytest.raises(NotificationNotFoundError):
            await service.delete_rule(OTHER, rule.id)
        await service.delete_rule(OWNER, rule.id)
        assert await service.list_rules(OWNER) == []

    async def test_update_race_with_delete_is_not_found(self):
        service, store = _service()
        rule = await service.create_rule(OWNER, NotificationRuleSpec(name="ops", sink="ops"))

        async def vanished(_rule):
            return None

        store.rule_repo.update = vanished
        with pytest.raises(NotificationNotFoundError):
            await service.update_rule(OWNER, rule.id, NotificationRuleSpec(name="x", sink="ops"))

    @pytest.mark.parametrize(
        ("spec", "message"),
        [
            ({"sink": "telegram"}, "Unknown sink"),
            ({"sink": "integration"}, "needs an integration_connection_id"),
            ({"sink": "integration", "integration_connection_id": "tg-b"}, "one of your"),
            ({"sink": "integration", "integration_connection_id": "off"}, "one of your"),
            ({"sink": "integration", "integration_connection_id": "jira"}, "one of your"),
        ],
    )
    async def test_rule_validation(self, spec, message):
        integrations = FakeIntegrations(
            [
                _connection("tg-b", owner="owner-b"),
                _connection("off", enabled=False),
                _connection("jira", kind=IntegrationType.ISSUE_TRACKER),
            ]
        )
        service, _ = _service(integrations=integrations)
        with pytest.raises(NotificationValidationError, match=message):
            await service.create_rule(OWNER, NotificationRuleSpec(name="r", **spec))

    async def test_integration_rule_accepts_owned_messaging_connection(self):
        service, _ = _service(integrations=FakeIntegrations([_connection("tg")]))
        rule = await service.create_rule(
            OWNER,
            NotificationRuleSpec(name="tg", sink="telegram", integration_connection_id="tg"),
        )
        assert rule.integration_connection_id == "tg"


class TestIndividualReads:
    async def test_only_opened_card_clears_and_retries_do_not_advance_revision(self):
        service, store = _service()
        session = _session()
        older, opened, newest = await service.project_log_entries(
            session, [_turn_entry(session, i, f"turn-{i}") for i in range(1, 4)]
        )
        state = await service.mark_read(OWNER, opened.id)
        assert (state.read_through_seq, state.revision, state.unread_count) == (0, 1, 2)
        assert await service.mark_read(OWNER, opened.id) == state
        feed = await service.list_feed(OWNER, NotificationQuery(limit=10))
        assert feed.read_ids == {opened.id} and feed.revision == 1
        unread = await service.list_feed(OWNER, NotificationQuery(limit=10, unread=True))
        assert [n.id for n in unread.items] == [newest.id, older.id]
        read = await service.list_feed(OWNER, NotificationQuery(limit=10, unread=False))
        assert [n.id for n in read.items] == [opened.id]
        assert (await service.get_read_state(ADMIN)).unread_count == 3
        await service.mark_read(ADMIN, older.id)
        assert (await service.get_read_state(OWNER)).unread_count == 2
        # Legacy mark-all remains CAS guarded against concurrent individual acknowledgement.
        with pytest.raises(NotificationReadStateConflictError):
            await service.update_read_state(OWNER, read_through_seq=3, expected_revision=0)
        state = await service.update_read_state(OWNER, read_through_seq=3, expected_revision=1)
        assert state.unread_count == 0 and state.read_through_seq == 3
        assert (await service.get_read_state(ADMIN)).unread_count == 2

    async def test_missing_foreign_owner_and_foreign_tenant_are_not_acknowledged(self):
        service, store = _service()
        foreign = _session(owner_id="owner-b", tenant_id="tenant-b")
        [item] = await service.project_log_entries(foreign, [_turn_entry(foreign, 1, "foreign")])
        for reader in [OWNER, ADMIN]:
            for nid in [uuid4(), item.id]:
                with pytest.raises(NotificationNotFoundError):
                    await service.mark_read(reader, nid)
        assert not store.reads
