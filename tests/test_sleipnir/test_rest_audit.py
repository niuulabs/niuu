"""Tests for the audit log REST router (GET /audit/events)."""

from __future__ import annotations

import fnmatch
from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sleipnir.ports.audit import AuditQuery, AuditRepository
from tests.helpers.http_contracts import RouteCallSpec, assert_route_equivalence
from tests.test_sleipnir.conftest import DEFAULT_TIMESTAMP, make_event
from volundr.adapters.inbound.rest_audit import (
    AuditEventResponse,
    create_audit_router,
    create_canonical_audit_router,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _InMemoryAuditRepository(AuditRepository):
    def __init__(self) -> None:
        self._events: list = []

    async def append(self, event) -> None:
        self._events.append(event)

    async def query(self, q: AuditQuery) -> list:
        events = list(self._events)
        if q.event_type_pattern and q.event_type_pattern != "*":
            events = [e for e in events if fnmatch.fnmatch(e.event_type, q.event_type_pattern)]
        if q.correlation_id:
            events = [e for e in events if e.correlation_id == q.correlation_id]
        if q.source:
            events = [e for e in events if e.source == q.source]
        return events[: q.limit]

    async def purge_expired(self) -> int:
        return 0


@pytest.fixture
def repo() -> _InMemoryAuditRepository:
    return _InMemoryAuditRepository()


@pytest.fixture
def client(repo) -> TestClient:
    app = FastAPI()
    app.include_router(create_canonical_audit_router(repo))
    app.include_router(create_audit_router(repo))
    return TestClient(app)


# ---------------------------------------------------------------------------
# GET /audit/events — basic
# ---------------------------------------------------------------------------


def test_get_events_empty(client):
    response = client.get("/audit/events")
    assert response.status_code == 200
    assert response.json() == []
    assert response.headers["Deprecation"] == "true"
    assert response.headers["X-Niuu-Canonical-Route"] == "/api/v1/audit/events"


async def test_get_events_returns_events(repo, client):
    evt = make_event()
    await repo.append(evt)

    response = client.get("/audit/events")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["event_id"] == evt.event_id
    assert data[0]["event_type"] == evt.event_type
    assert data[0]["service"] == "ravn"


async def test_canonical_audit_route_matches_legacy(repo, client):
    evt = make_event()
    await repo.append(evt)

    assert_route_equivalence(
        client,
        legacy=RouteCallSpec(path="/audit/events"),
        canonical=RouteCallSpec(path="/api/v1/audit/events"),
    )


async def test_get_events_response_schema(repo, client):
    evt = make_event(
        event_id="test-id",
        event_type="ravn.tool.complete",
        source="ravn:agent",
        summary="Test event",
        urgency=0.5,
        domain="code",
        correlation_id="corr-1",
        causation_id="cause-1",
        tenant_id="tenant-1",
        payload={"key": "value"},
        ttl=300,
    )
    await repo.append(evt)

    response = client.get("/audit/events")
    data = response.json()[0]

    assert data["event_id"] == "test-id"
    assert data["event_type"] == "ravn.tool.complete"
    assert data["source"] == "ravn:agent"
    assert data["service"] == "ravn"
    assert data["summary"] == "Test event"
    assert data["urgency"] == 0.5
    assert data["domain"] == "code"
    assert data["correlation_id"] == "corr-1"
    assert data["causation_id"] == "cause-1"
    assert data["tenant_id"] == "tenant-1"
    assert data["payload"] == {"key": "value"}
    assert data["ttl"] == 300
    assert "timestamp" in data


# ---------------------------------------------------------------------------
# Query parameter filtering
# ---------------------------------------------------------------------------


async def test_get_events_filter_event_type(repo, client):
    await repo.append(make_event(event_id="a", event_type="ravn.tool.complete"))
    await repo.append(make_event(event_id="b", event_type="tyr.task.started"))

    response = client.get("/audit/events", params={"event_type": "ravn.*"})
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["event_id"] == "a"


async def test_get_events_filter_correlation_id(repo, client):
    await repo.append(make_event(event_id="a", correlation_id="corr-A"))
    await repo.append(make_event(event_id="b", correlation_id="corr-B"))

    response = client.get("/audit/events", params={"correlation_id": "corr-A"})
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["event_id"] == "a"


async def test_get_events_limit_param(repo, client):
    for i in range(10):
        await repo.append(make_event(event_id=f"evt-{i}"))

    response = client.get("/audit/events", params={"limit": 3})
    assert response.status_code == 200
    assert len(response.json()) == 3


def test_get_events_limit_too_large_returns_422(client):
    response = client.get("/audit/events", params={"limit": 9999})
    assert response.status_code == 422


def test_get_events_limit_zero_returns_422(client):
    response = client.get("/audit/events", params={"limit": 0})
    assert response.status_code == 422


async def test_get_events_from_param(repo, client):
    await repo.append(make_event(event_id="old", timestamp=DEFAULT_TIMESTAMP))
    ts = (DEFAULT_TIMESTAMP + timedelta(hours=1)).isoformat()
    response = client.get("/audit/events", params={"from": ts})
    assert response.status_code == 200


async def test_get_events_to_param(repo, client):
    await repo.append(make_event(event_id="evt", timestamp=DEFAULT_TIMESTAMP))
    ts = DEFAULT_TIMESTAMP.isoformat()
    response = client.get("/audit/events", params={"to": ts})
    assert response.status_code == 200


async def test_get_events_filter_source(repo, client):
    await repo.append(make_event(event_id="a", source="ravn:agent-1"))
    await repo.append(make_event(event_id="b", source="tyr:dispatcher"))

    response = client.get("/audit/events", params={"source": "ravn:agent-1"})
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["event_id"] == "a"


async def test_get_events_filter_service(repo, client):
    await repo.append(make_event(event_id="a", source="ravn:agent-1"))
    await repo.append(make_event(event_id="b", source="tyr:dispatcher"))
    await repo.append(make_event(event_id="c", source="ravn:agent-2"))

    response = client.get("/api/v1/audit/events", params={"service": "ravn", "limit": 10})
    assert response.status_code == 200
    data = response.json()
    assert [item["event_id"] for item in data] == ["a", "c"]


# ---------------------------------------------------------------------------
# AuditEventResponse
# ---------------------------------------------------------------------------


def test_audit_event_response_from_event():
    evt = make_event(
        event_id="abc",
        correlation_id="corr",
        causation_id="cause",
        tenant_id="t1",
        ttl=60,
    )
    resp = AuditEventResponse.from_event(evt)

    assert resp.event_id == "abc"
    assert resp.correlation_id == "corr"
    assert resp.causation_id == "cause"
    assert resp.tenant_id == "t1"
    assert resp.ttl == 60
    assert "T" in resp.timestamp  # ISO 8601 timestamp


def test_audit_event_response_nullable_fields():
    evt = make_event(correlation_id=None, causation_id=None, tenant_id=None, ttl=None)
    resp = AuditEventResponse.from_event(evt)

    assert resp.correlation_id is None
    assert resp.causation_id is None
    assert resp.tenant_id is None
    assert resp.ttl is None
