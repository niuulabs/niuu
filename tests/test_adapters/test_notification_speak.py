"""Forge stores and serves a notification's ``speak`` intent (LEX-1536).

REST submit and the projected transcript turn both record it; ``GET /notifications``,
the per-session list and the ``session_notification`` live event return it. Forge
never speaks or delivers it.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from identity.adapters.identity import EnvoyHeaderAuthenticationAdapter
from niuu.domain.notifications import (
    NotificationDraft,
    NotificationSpeak,
    SpeakPriority,
    build_notification_turn,
)
from tests.conftest import InMemorySessionRepository, MockEventBroadcaster, MockPodManager
from tests.support.notifications import InMemoryNotificationStore
from tests.test_adapters.test_postgres_notifications import (
    _candidate,
    _notification_row,
    _pool,
)
from tests.test_adapters.test_rest_notifications import OwnerOnly, headers
from volundr.adapters.inbound.rest_notifications import create_notifications_router
from volundr.adapters.outbound.postgres_notifications import (
    INSERT_NOTIFICATION_SQL,
    PostgresNotificationRepository,
)
from volundr.domain.models import EventType, Session, SessionLogEntry
from volundr.domain.services.notifications import NotificationService
from volundr.domain.services.session import SessionService

PREFIX = "/api/v1/forge"
ROOT = Path(__file__).resolve().parents[2]
MIGRATION = "000097_forge_notification_speak"
# The SQL promoted to Thor and mini-m6; pinned so it cannot change after it is applied.
MIGRATION_SHA256 = "f29350b3570c90d4d55455951a563f998943ab86b1c7f2ed870dee6c3be4482c"


class Env:
    def __init__(self) -> None:
        self.store = InMemoryNotificationStore()
        self.sessions = InMemorySessionRepository()
        self.broadcaster = MockEventBroadcaster()
        session_service = SessionService(self.sessions, MockPodManager(), authorization=OwnerOnly())
        self.service = NotificationService(
            self.store.feed,
            self.store.rule_repo,
            self.store.outbox,
            image_repository=self.store.images,
            image_scheduler=self.store.image_scheduler,
            broadcaster=self.broadcaster,
            reply_ready_enabled=False,
            reply_title_chars=80,
            reply_body_chars=100,
            sinks=[],
        )
        app = FastAPI()
        app.state.identity = EnvoyHeaderAuthenticationAdapter()
        app.include_router(
            create_notifications_router(
                self.service,
                session_service,
                prefix=PREFIX,
                default_page_size=10,
                max_page_size=20,
                image_cache_max_age_seconds=60,
            )
        )
        self.client = TestClient(app)

    async def session(self) -> Session:
        session = Session(name="speaker", owner_id="owner-a", tenant_id="tenant-a")
        await self.sessions.create(session)
        return session

    async def project(self, session: Session, draft: NotificationDraft) -> str:
        turn = build_notification_turn(
            draft, turn_id="nt-speak", session_id=session.id, created_at="2026-10-27T10:00:00Z"
        )
        entry = SessionLogEntry(
            session_id=session.id,
            seq=1,
            kind="conversation.turn",
            payload={"type": "conversation.turn", "turn": turn},
            ts=datetime.now(UTC),
        )
        [created] = await self.service.project_log_entries(session, [entry])
        return str(created.id)

    def submit(self, session: Session, **body):
        return self.client.post(
            f"{PREFIX}/sessions/{session.id}/notifications",
            json={"kind": "milestone", "title": "Done", "idempotency_key": "k1", **body},
            headers=headers(),
        )


@pytest.fixture
def env() -> Env:
    return Env()


class TestRestRoundTrip:
    async def test_submit_stores_and_every_read_returns_it(self, env):
        session = await env.session()
        speak = {"text": "The build is green.", "priority": "next_window"}
        created = env.submit(session, speak={**speak, "text": "  The build is green. "})
        assert created.status_code == 201
        assert created.json()["speak"] == speak
        nid = created.json()["id"]

        [feed_item] = env.client.get(f"{PREFIX}/notifications", headers=headers()).json()["items"]
        assert (feed_item["id"], feed_item["speak"]) == (nid, speak)
        [session_item] = env.client.get(
            f"{PREFIX}/sessions/{session.id}/notifications", headers=headers()
        ).json()
        assert session_item["speak"] == speak
        replay = env.submit(session, speak=speak)
        assert (replay.status_code, replay.json()["speak"]) == (200, speak)

    async def test_without_speak_the_field_is_null(self, env):
        session = await env.session()
        assert env.submit(session).json()["speak"] is None

    @pytest.mark.parametrize(
        "speak",
        [
            {"text": "   "},
            {"text": "a" * 601},
            {"text": "hi", "priority": "whenever"},
            {"text": "hi", "voice": "alto"},
        ],
    )
    async def test_invalid_speak_is_422_and_records_nothing(self, env, speak):
        session = await env.session()
        assert env.submit(session, speak=speak).status_code == 422
        assert env.store.notifications == {}

    async def test_reply_ready_may_not_carry_speak(self, env):
        session = await env.session()
        response = env.submit(session, kind="reply_ready", speak={"text": "hi"})
        assert response.status_code == 422
        assert "speak is not allowed on reply_ready" in response.text

    async def test_projected_turn_carries_it_to_the_feed(self, env):
        session = await env.session()
        nid = await env.project(
            session, NotificationDraft(kind="decision", title="Picked", speak={"text": "Picked."})
        )
        [item] = env.client.get(f"{PREFIX}/notifications", headers=headers()).json()["items"]
        assert item["id"] == nid
        assert item["speak"] == {"text": "Picked.", "priority": "now"}


class TestStream:
    async def test_live_event_carries_the_intent(self, env):
        session = await env.session()
        nid = await env.project(
            session,
            NotificationDraft(kind="attention", title="Blocked", speak={"text": "I'm blocked."}),
        )
        [event] = env.broadcaster.events
        assert event.type is EventType.SESSION_NOTIFICATION
        assert event.data["id"] == nid
        assert event.data["speak"] == {"text": "I'm blocked.", "priority": "now"}

    async def test_live_event_without_speak_has_a_null_field(self, env):
        session = await env.session()
        await env.project(session, NotificationDraft(kind="info", title="FYI"))
        [event] = env.broadcaster.events
        assert "speak" in event.data and event.data["speak"] is None


class TestPostgres:
    async def test_insert_binds_both_columns_and_reads_them_back(self):
        pool, conn = _pool()
        candidate = _candidate("a", speak={"text": "hi", "priority": "next_window"})
        conn.fetchrow.side_effect = [_notification_row(candidate, 1)]
        [created] = await PostgresNotificationRepository(pool).project([candidate])
        insert = conn.fetchrow.await_args_list[0].args
        assert insert[0] == INSERT_NOTIFICATION_SQL
        assert "speak_text, speak_priority" in INSERT_NOTIFICATION_SQL
        assert insert[19:21] == ("hi", "next_window")
        assert created.speak == NotificationSpeak(text="hi", priority=SpeakPriority.NEXT_WINDOW)

    async def test_no_speak_binds_nulls(self):
        pool, conn = _pool()
        candidate = _candidate("a")
        conn.fetchrow.side_effect = [_notification_row(candidate, 1)]
        [created] = await PostgresNotificationRepository(pool).project([candidate])
        assert conn.fetchrow.await_args_list[0].args[19:21] == (None, None)
        assert created.speak is None


class TestMigration:
    def test_is_additive_idempotent_and_constrained(self):
        up = (ROOT / "migrations" / f"{MIGRATION}.up.sql").read_text()
        assert "ADD COLUMN IF NOT EXISTS speak_text TEXT NULL" in up
        assert "ADD COLUMN IF NOT EXISTS speak_priority VARCHAR(16) NULL" in up
        assert "WHERE conname = 'forge_notifications_speak_check'" in up
        assert "char_length(speak_text) BETWEEN 1 AND 600" in up
        assert "speak_priority IN ('now', 'next_window')" in up
        assert "kind <> 'reply_ready'" in up
        assert "speak_text IS NOT NULL\n                    AND speak_priority IS NOT NULL" in up
        assert not re.search(r"\b(UPDATE|DELETE|DROP)\b", up)
        down = (ROOT / "migrations" / f"{MIGRATION}.down.sql").read_text()
        assert "DROP CONSTRAINT IF EXISTS forge_notifications_speak_check" in down
        assert down.count("DROP COLUMN IF EXISTS") == 2

    def test_sql_is_the_promoted_file(self):
        up = (ROOT / "migrations" / f"{MIGRATION}.up.sql").read_bytes()
        assert hashlib.sha256(up).hexdigest() == MIGRATION_SHA256
