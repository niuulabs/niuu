"""Tests for Ting audit compatibility endpoints."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ting.adapters.memory_event_bus import InMemoryEventBus
from ting.api.audit import create_audit_router
from ting.api.dispatcher import resolve_event_bus
from ting.ports.event_bus import TingEvent


def _auth_headers(user_id: str = "user-1") -> dict[str, str]:
    return {"x-auth-user-id": user_id}


def _client(event_bus: InMemoryEventBus) -> TestClient:
    app = FastAPI()
    app.include_router(create_audit_router())
    app.dependency_overrides[resolve_event_bus] = lambda: event_bus
    return TestClient(app)


class TestAuditAPI:
    def test_returns_event_log_entries(self) -> None:
        event_bus = InMemoryEventBus(max_clients=5, log_size=20)
        event_bus._log.extend(  # noqa: SLF001
            [
                TingEvent(
                    event="dispatch.started",
                    data={"run_id": "r-1"},
                    owner_id="user-1",
                    id="evt-1",
                    timestamp=datetime(2026, 1, 1, tzinfo=UTC),
                )
            ]
        )
        client = _client(event_bus)

        response = client.get("/api/v1/ting/audit", headers=_auth_headers())

        assert response.status_code == 200
        assert response.headers["Deprecation"] == "true"
        assert response.headers["X-Niuu-Canonical-Route"] == "/api/v1/audit"
        assert response.json() == [
            {
                "id": "evt-1",
                "kind": "dispatch.started",
                "summary": "dispatch started",
                "actor": "user-1",
                "payload": {"run_id": "r-1"},
                "created_at": "2026-01-01T00:00:00Z",
            }
        ]

    def test_filters_by_kind_and_actor(self) -> None:
        event_bus = InMemoryEventBus(max_clients=5, log_size=20)
        event_bus._log.extend(  # noqa: SLF001
            [
                TingEvent(event="dispatch.started", data={}, owner_id="user-1", id="evt-1"),
                TingEvent(event="saga.created", data={}, owner_id="user-2", id="evt-2"),
            ]
        )
        client = _client(event_bus)

        response = client.get(
            "/api/v1/ting/audit?kinds=dispatch.started&actor=user-1",
            headers=_auth_headers(),
        )

        assert response.status_code == 200
        assert response.headers["X-Niuu-Canonical-Route"] == "/api/v1/audit"
        assert [entry["id"] for entry in response.json()] == ["evt-1"]

    def test_rejects_invalid_timestamp_filter(self) -> None:
        client = _client(InMemoryEventBus(max_clients=5, log_size=20))

        response = client.get(
            "/api/v1/ting/audit?since=not-a-date",
            headers=_auth_headers(),
        )

        assert response.status_code == 422
