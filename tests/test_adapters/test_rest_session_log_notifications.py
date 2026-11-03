"""POST /sessions/{id}/log projects notifications from the STORED rows (contract §3)."""

from __future__ import annotations

from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from identity.adapters.identity import EnvoyHeaderAuthenticationAdapter
from niuu.domain.notifications import NotificationDraft, build_notification_turn
from tests.conftest import InMemorySessionRepository, MockEventBroadcaster, MockPodManager
from tests.support.notifications import InMemoryNotificationStore
from tests.test_adapters.test_rest_session_log import InMemoryLog
from volundr.adapters.inbound.rest_session_log import create_session_log_router
from volundr.domain.models import EventType, Session
from volundr.domain.notifications import NotificationStoreUnavailableError
from volundr.domain.services.notifications import NotificationService
from volundr.domain.services.session import SessionService

HEADERS = {"x-auth-user-id": "owner-a", "x-auth-tenant": "tenant-a"}


class CountingLog(InMemoryLog):
    def __init__(self) -> None:
        super().__init__()
        self.read_back: list[list[int]] = []

    async def read_seqs(self, session_id, seqs):
        self.read_back.append(sorted(seqs))
        return await super().read_seqs(session_id, seqs)


def _app(*, reply_ready: bool = True, with_notifications: bool = True):
    log = CountingLog()
    store = InMemoryNotificationStore()
    broadcaster = MockEventBroadcaster()
    sessions = InMemorySessionRepository()
    session_service = SessionService(sessions, MockPodManager(), broadcaster=broadcaster)
    service = NotificationService(
        store.feed,
        store.rule_repo,
        store.outbox,
        image_repository=store.images,
        image_scheduler=store.image_scheduler,
        broadcaster=broadcaster,
        reply_ready_enabled=reply_ready,
        reply_title_chars=80,
        reply_body_chars=100,
        sinks=[],
    )
    app = FastAPI()
    app.state.identity = EnvoyHeaderAuthenticationAdapter()
    app.include_router(
        create_session_log_router(
            log,
            session_service=session_service,
            notification_service=service if with_notifications else None,
        )
    )
    return TestClient(app), log, store, broadcaster, sessions


def _notification_frame(session_id, seq: int, turn_id: str, title: str) -> dict:
    turn = build_notification_turn(
        NotificationDraft(kind="milestone", title=title),
        turn_id=turn_id,
        session_id=session_id,
        created_at="2026-09-23T10:00:00Z",
    )
    return {
        "seq": seq,
        "kind": "conversation.turn",
        "role": "assistant",
        "payload": {"type": "conversation.turn", "turn": turn},
    }


def _final_frame(seq: int, turn_id: str, content: str) -> dict:
    return {
        "seq": seq,
        "kind": "conversation.turn",
        "payload": {
            "type": "conversation.turn",
            "turn": {
                "id": turn_id,
                "role": "assistant",
                "content": content,
                "metadata": {"final_output": True},
            },
        },
    }


def _chatter(seq: int) -> dict:
    return {"seq": seq, "kind": "content_block_delta", "payload": {"type": "delta", "n": seq}}


async def _session(sessions) -> Session:
    return await sessions.create(Session(name="s", owner_id="owner-a", tenant_id="tenant-a"))


def _notification_events(broadcaster) -> list:
    return [e for e in broadcaster.events if e.type is EventType.SESSION_NOTIFICATION]


async def test_projects_notification_turns_and_final_replies_once():
    client, log, store, broadcaster, sessions = _app()
    session = await _session(sessions)
    url = f"/api/v1/forge/sessions/{session.id}/log"
    batch = {
        "entries": [
            _chatter(1),
            _notification_frame(session.id, 2, "nt_a", "Tests green"),
            _final_frame(3, "final-1", "All done"),
        ]
    }

    assert client.post(url, json=batch, headers=HEADERS).status_code == 201
    assert client.post(url, json=batch, headers=HEADERS).status_code == 201  # producer retry

    kinds = sorted(n.kind.value for n in store.notifications.values())
    assert kinds == ["milestone", "reply_ready"]
    assert {n.session_seq for n in store.notifications.values()} == {2, 3}
    assert len(_notification_events(broadcaster)) == 2
    assert log.read_back == [[2, 3], [2, 3]]  # chatter is never read back


async def test_conflicting_seq_never_projects_the_colliding_payload():
    client, _, store, broadcaster, sessions = _app()
    session = await _session(sessions)
    url = f"/api/v1/forge/sessions/{session.id}/log"
    client.post(url, json={"entries": [_chatter(1)]}, headers=HEADERS)

    forged = _notification_frame(session.id, 1, "nt_forged", "Forged")
    response = client.post(url, json={"entries": [forged]}, headers=HEADERS)

    assert response.json()["conflicts"] == [1]
    assert store.notifications == {}
    assert _notification_events(broadcaster) == []


async def test_stored_rows_are_projected_not_the_request():
    """Even when detection misses a collision, only the stored copy is projected."""
    client, log, store, _, sessions = _app()
    session = await _session(sessions)
    url = f"/api/v1/forge/sessions/{session.id}/log"
    client.post(
        url,
        json={"entries": [_notification_frame(session.id, 4, "nt_x", "Stored title")]},
        headers=HEADERS,
    )
    store.notifications.clear()

    async def blind(entries):
        return []

    log.detect_conflicts = blind
    client.post(
        url,
        json={"entries": [_notification_frame(session.id, 4, "nt_y", "Request title")]},
        headers=HEADERS,
    )
    [projected] = store.notifications.values()
    assert projected.title == "Stored title" and projected.session_seq == 4


async def test_reply_ready_disabled_skips_final_outputs():
    client, log, store, _, sessions = _app(reply_ready=False)
    session = await _session(sessions)
    client.post(
        f"/api/v1/forge/sessions/{session.id}/log",
        json={"entries": [_final_frame(1, "f", "Done")]},
        headers=HEADERS,
    )
    assert store.notifications == {} and log.read_back == []


async def test_orphan_log_is_refused_before_anything_is_projected():
    client, log, store, _, _ = _app()
    sid = uuid4()
    response = client.post(
        f"/api/v1/forge/sessions/{sid}/log",
        json={"entries": [_notification_frame(sid, 1, "nt_o", "Orphan")]},
        headers=HEADERS,
    )
    assert response.status_code == 404
    assert store.notifications == {} and log.read_back == []


async def test_without_notification_service_nothing_is_projected():
    client, log, _, broadcaster, sessions = _app(with_notifications=False)
    session = await _session(sessions)
    client.post(
        f"/api/v1/forge/sessions/{session.id}/log",
        json={"entries": [_notification_frame(session.id, 1, "nt", "x")]},
        headers=HEADERS,
    )
    assert log.read_back == [] and _notification_events(broadcaster) == []


async def test_store_outage_asks_the_producer_to_retry_and_the_retry_projects():
    """A store outage answers 503; the frames stay stored and a retry projects them."""
    client, log, store, broadcaster, sessions = _app()
    session = await _session(sessions)
    url = f"/api/v1/forge/sessions/{session.id}/log"
    batch = {"entries": [_notification_frame(session.id, 1, "nt_out", "Outage")]}
    real_project = store.feed.project

    async def unavailable(candidates):
        raise NotificationStoreUnavailableError("connection refused")

    store.feed.project = unavailable
    response = client.post(url, json=batch, headers=HEADERS)
    assert response.status_code == 503
    assert await log.latest_seq(session.id) == 1  # the transcript frame is kept
    assert _notification_events(broadcaster) == []

    store.feed.project = real_project
    assert client.post(url, json=batch, headers=HEADERS).status_code == 201
    [projected] = store.notifications.values()
    assert projected.title == "Outage"
    assert len(_notification_events(broadcaster)) == 1


async def test_projection_defect_fails_the_append_and_the_retry_projects():
    """A projection defect is a 500, not an acknowledged append missing its notification."""
    client, log, store, broadcaster, sessions = _app()
    session = await _session(sessions)
    url = f"/api/v1/forge/sessions/{session.id}/log"
    batch = {"entries": [_notification_frame(session.id, 1, "nt_bug", "Bug")]}
    real_project = store.feed.project

    async def broken(candidates):
        raise RuntimeError("projection bug")

    store.feed.project = broken
    failing = TestClient(client.app, raise_server_exceptions=False)
    response = failing.post(url, json=batch, headers=HEADERS)
    assert response.status_code == 500
    assert await log.latest_seq(session.id) == 1  # the transcript frame is kept
    assert _notification_events(broadcaster) == []

    store.feed.project = real_project
    assert client.post(url, json=batch, headers=HEADERS).status_code == 201
    [projected] = store.notifications.values()
    assert projected.title == "Bug"
