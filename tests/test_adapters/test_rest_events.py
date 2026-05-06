"""Tests for the event pipeline REST endpoints."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from volundr.adapters.inbound.rest_events import create_events_router
from volundr.domain.models import SessionEvent, SessionEventType
from volundr.domain.ports import EventSink, SessionEventRepository
from volundr.domain.services.event_ingestion import EventIngestionService


class InMemoryEventSink(EventSink, SessionEventRepository):
    """In-memory sink + repository for testing REST endpoints."""

    def __init__(self):
        self._events: list[SessionEvent] = []

    async def emit(self, event: SessionEvent) -> None:
        self._events.append(event)

    async def emit_batch(self, events: list[SessionEvent]) -> None:
        self._events.extend(events)

    async def flush(self) -> None:
        pass

    async def close(self) -> None:
        pass

    @property
    def sink_name(self) -> str:
        return "test"

    @property
    def healthy(self) -> bool:
        return True

    async def get_events(
        self,
        session_id,
        event_types=None,
        after=None,
        before=None,
        limit=1000,
        offset=0,
    ) -> list[SessionEvent]:
        results = [e for e in self._events if e.session_id == session_id]
        if event_types:
            results = [e for e in results if e.event_type in event_types]
        return results[offset : offset + limit]

    async def get_event_counts(self, session_id) -> dict[str, int]:
        counts: dict[str, int] = {}
        for e in self._events:
            if e.session_id == session_id:
                key = e.event_type.value
                counts[key] = counts.get(key, 0) + 1
        return counts

    async def get_token_timeline(self, session_id, bucket_seconds=300) -> list[dict]:
        return [{"bucket": 0, "tokens_in": 100, "tokens_out": 50, "cost": 0.01}]

    async def delete_by_session(self, session_id) -> int:
        before = len(self._events)
        self._events = [e for e in self._events if e.session_id != session_id]
        return before - len(self._events)


@pytest.fixture
def event_app():
    sink = InMemoryEventSink()
    service = EventIngestionService(sinks=[sink])
    app = FastAPI()
    router = create_events_router(service, sink)
    app.include_router(router)
    return app, sink


@pytest.fixture
def client(event_app):
    app, _ = event_app
    return TestClient(app)


@pytest.fixture
def sink(event_app):
    _, sink = event_app
    return sink


class TestIngestEvent:
    """Tests for POST /events."""

    def test_ingest_single_event(self, client, sink):
        session_id = str(uuid4())
        resp = client.post(
            "/api/v1/volundr/events",
            json={
                "session_id": session_id,
                "event_type": "message_assistant",
                "timestamp": datetime.now(UTC).isoformat(),
                "data": {"content_preview": "hello"},
                "sequence": 0,
                "tokens_in": 100,
                "tokens_out": 50,
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["event_type"] == "message_assistant"
        assert body["session_id"] == session_id
        assert body["tokens_in"] == 100
        assert len(sink._events) == 1

    def test_ingest_invalid_event_type(self, client):
        resp = client.post(
            "/api/v1/volundr/events",
            json={
                "session_id": str(uuid4()),
                "event_type": "invalid_type",
                "timestamp": datetime.now(UTC).isoformat(),
                "data": {},
                "sequence": 0,
            },
        )
        assert resp.status_code == 422

    def test_ingest_all_event_types(self, client, sink):
        for et in SessionEventType:
            resp = client.post(
                "/api/v1/volundr/events",
                json={
                    "session_id": str(uuid4()),
                    "event_type": et.value,
                    "timestamp": datetime.now(UTC).isoformat(),
                    "data": {},
                    "sequence": 0,
                },
            )
            assert resp.status_code == 201

    def test_ingest_with_cost_and_model(self, client, sink):
        resp = client.post(
            "/api/v1/volundr/events",
            json={
                "session_id": str(uuid4()),
                "event_type": "token_usage",
                "timestamp": datetime.now(UTC).isoformat(),
                "data": {"provider": "cloud", "tokens_in": 200},
                "sequence": 1,
                "cost": 0.005,
                "model": "claude-sonnet-4-20250514",
            },
        )
        assert resp.status_code == 201
        assert resp.json()["cost"] == 0.005
        assert resp.json()["model"] == "claude-sonnet-4-20250514"


class TestIngestBatch:
    """Tests for POST /events/batch."""

    def test_ingest_batch(self, client, sink):
        session_id = str(uuid4())
        resp = client.post(
            "/api/v1/volundr/events/batch",
            json={
                "events": [
                    {
                        "session_id": session_id,
                        "event_type": "file_modified",
                        "timestamp": datetime.now(UTC).isoformat(),
                        "data": {"path": "/src/main.py"},
                        "sequence": i,
                    }
                    for i in range(3)
                ]
            },
        )
        assert resp.status_code == 201
        assert len(resp.json()) == 3
        assert len(sink._events) == 3

    def test_ingest_batch_rejects_invalid_type(self, client):
        resp = client.post(
            "/api/v1/volundr/events/batch",
            json={
                "events": [
                    {
                        "session_id": str(uuid4()),
                        "event_type": "bogus",
                        "timestamp": datetime.now(UTC).isoformat(),
                        "data": {},
                        "sequence": 0,
                    }
                ]
            },
        )
        assert resp.status_code == 422

    def test_ingest_batch_rejects_empty(self, client):
        resp = client.post(
            "/api/v1/volundr/events/batch",
            json={"events": []},
        )
        assert resp.status_code == 422


class TestQueryEvents:
    """Tests for GET /sessions/{id}/events."""

    def test_get_session_events(self, client, sink):
        session_id = uuid4()
        # Seed events directly
        for i in range(3):
            sink._events.append(
                SessionEvent(
                    id=uuid4(),
                    session_id=session_id,
                    event_type=SessionEventType.FILE_MODIFIED,
                    timestamp=datetime.now(UTC),
                    data={"path": f"/file{i}.py"},
                    sequence=i,
                )
            )
        resp = client.get(f"/api/v1/volundr/sessions/{session_id}/events")
        assert resp.status_code == 200
        assert len(resp.json()) == 3

    def test_get_session_events_with_type_filter(self, client, sink):
        session_id = uuid4()
        sink._events.append(
            SessionEvent(
                id=uuid4(),
                session_id=session_id,
                event_type=SessionEventType.FILE_MODIFIED,
                timestamp=datetime.now(UTC),
                data={},
                sequence=0,
            )
        )
        sink._events.append(
            SessionEvent(
                id=uuid4(),
                session_id=session_id,
                event_type=SessionEventType.GIT_COMMIT,
                timestamp=datetime.now(UTC),
                data={},
                sequence=1,
            )
        )
        resp = client.get(
            f"/api/v1/volundr/sessions/{session_id}/events",
            params={"event_type": "file_modified"},
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_get_session_events_invalid_type_filter(self, client):
        resp = client.get(
            f"/api/v1/volundr/sessions/{uuid4()}/events",
            params={"event_type": "nonexistent"},
        )
        assert resp.status_code == 422

    def test_get_event_counts(self, client, sink):
        session_id = uuid4()
        types = [
            SessionEventType.FILE_MODIFIED,
            SessionEventType.FILE_MODIFIED,
            SessionEventType.GIT_COMMIT,
        ]
        for et in types:
            sink._events.append(
                SessionEvent(
                    id=uuid4(),
                    session_id=session_id,
                    event_type=et,
                    timestamp=datetime.now(UTC),
                    data={},
                    sequence=0,
                )
            )
        resp = client.get(f"/api/v1/volundr/sessions/{session_id}/events/counts")
        assert resp.status_code == 200
        body = resp.json()
        assert body["file_modified"] == 2
        assert body["git_commit"] == 1

    def test_get_token_timeline(self, client):
        resp = client.get(f"/api/v1/volundr/sessions/{uuid4()}/events/tokens")
        assert resp.status_code == 200
        assert len(resp.json()) >= 1


class TestSinkHealth:
    """Tests for GET /events/health."""

    def test_get_sink_health(self, client):
        resp = client.get("/api/v1/volundr/events/health")
        assert resp.status_code == 200
        body = resp.json()
        assert "sinks" in body
        assert body["sinks"]["test"] is True
