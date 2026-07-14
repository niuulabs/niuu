"""Trace API authorization for Forge sessions and resident runtimes."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from volundr.adapters.inbound.rest_trace import create_trace_router
from volundr.domain.models import SessionSpanStatus
from volundr.domain.services.resident_runtime import ResidentRuntimeNotFoundError


class _SpanRepository:
    def __init__(self) -> None:
        self.spans = []

    async def upsert_span(self, span):
        self.spans.append(span)
        return span

    async def finish_span(self, span_id, ended_at, status, attributes=None):
        for index, span in enumerate(self.spans):
            if span.id == span_id:
                finished = replace(
                    span,
                    ended_at=ended_at,
                    duration_ms=max(
                        0,
                        int((ended_at - span.started_at).total_seconds() * 1000),
                    ),
                    status=SessionSpanStatus(status),
                    attributes={**span.attributes, **(attributes or {})},
                )
                self.spans[index] = finished
                return finished
        return None

    async def list_spans(self, session_id):
        return [span for span in self.spans if span.session_id == session_id]

    async def delete_by_session(self, session_id):
        return 0


class _SessionService:
    def __init__(self, session_id: UUID | None = None) -> None:
        self.session_id = session_id
        self.access_checks = []

    async def get_session(self, session_id):
        return object() if session_id == self.session_id else None

    async def _check_access(self, session, principal, action):
        self.access_checks.append((principal, action))


class _ResidentRuntimeService:
    def __init__(self, runtime_id: UUID) -> None:
        self.runtime_id = runtime_id
        self.principals = []

    async def get(self, principal, runtime_id):
        self.principals.append(principal)
        if runtime_id != self.runtime_id:
            raise ResidentRuntimeNotFoundError(f"Resident runtime not found: {runtime_id}")
        return object()


def _client(
    runtime_id: UUID,
    *,
    session_id: UUID | None = None,
) -> tuple[TestClient, _SpanRepository, _ResidentRuntimeService, _SessionService]:
    repository = _SpanRepository()
    residents = _ResidentRuntimeService(runtime_id)
    sessions = _SessionService(session_id)
    app = FastAPI()
    app.state.identity = object()
    app.include_router(
        create_trace_router(
            repository,
            session_service=sessions,
            resident_runtime_service=residents,
        )
    )
    return TestClient(app), repository, residents, sessions


def _headers() -> dict[str, str]:
    return {
        "x-auth-user-id": "user-a",
        "x-auth-tenant": "tenant-a",
        "x-auth-roles": "volundr:developer",
    }


def test_resident_runtime_can_emit_and_read_existing_session_trace() -> None:
    runtime_id = uuid4()
    span_id = uuid4()
    client, repository, residents, _ = _client(runtime_id)

    response = client.post(
        "/api/v1/forge/spans/start",
        headers=_headers(),
        json={
            "id": str(span_id),
            "session_id": str(runtime_id),
            "trace_id": str(runtime_id),
            "kind": "turn.peer",
            "name": "Hermes reaction",
            "source_service": "skuld",
        },
    )

    assert response.status_code == 201
    assert repository.spans[0].session_id == runtime_id
    trace = client.get(f"/api/v1/forge/sessions/{runtime_id}/trace", headers=_headers())
    assert trace.status_code == 200
    assert trace.json()["spans"][0]["id"] == str(span_id)
    assert residents.principals[-1].user_id == "user-a"


def test_unknown_trace_subject_is_rejected() -> None:
    runtime_id = uuid4()
    client, repository, _, _ = _client(runtime_id)

    response = client.post(
        "/api/v1/forge/spans/start",
        headers=_headers(),
        json={
            "id": str(uuid4()),
            "session_id": str(uuid4()),
            "trace_id": str(uuid4()),
            "kind": "turn.peer",
            "name": "Unknown",
            "source_service": "skuld",
        },
    )

    assert response.status_code == 404
    assert repository.spans == []


def test_forge_session_still_uses_existing_trace_authorization() -> None:
    session_id = uuid4()
    client, repository, residents, sessions = _client(uuid4(), session_id=session_id)

    response = client.post(
        "/api/v1/forge/spans/start",
        headers=_headers(),
        json={
            "id": str(uuid4()),
            "session_id": str(session_id),
            "trace_id": str(session_id),
            "kind": "turn.local",
            "name": "Codex turn",
            "source_service": "skuld",
        },
    )

    assert response.status_code == 201
    assert repository.spans[0].session_id == session_id
    assert sessions.access_checks[0][1] == "emit_trace"
    assert residents.principals == []


def test_resident_trace_lifecycle_lanes_and_summary() -> None:
    runtime_id = uuid4()
    trace_id = uuid4()
    client, _, _, _ = _client(runtime_id)
    headers = _headers()
    started_at = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)

    empty = client.get(f"/api/v1/forge/sessions/{runtime_id}/trace", headers=headers)
    assert empty.status_code == 200
    assert empty.json()["trace_id"] == str(runtime_id)

    spans = [
        ("session.lifecycle", "session", 1000, {}),
        ("session.provisioning", "provision", 100, {}),
        ("session.setup", "setup", 50, {}),
        ("session.workflow", "publish result", 300, {}),
        (
            "turn.peer",
            "Hermes reaction",
            200,
            {"actor_type": "agent", "actor_id": "hermes-1", "actor_label": "Hermes"},
        ),
        ("tool.call", "search", 25, {}),
        ("wait.peer", "wait", 75, {}),
        ("session.cleanup", "cleanup", 40, {}),
    ]
    for kind, name, duration_ms, actor in spans:
        response = client.post(
            "/api/v1/forge/spans/complete",
            headers=headers,
            json={
                "id": str(uuid4()),
                "session_id": str(runtime_id),
                "trace_id": str(trace_id),
                "kind": kind,
                "name": name,
                "source_service": "skuld",
                "started_at": started_at.isoformat(),
                "duration_ms": duration_ms,
                **actor,
            },
        )
        assert response.status_code == 201

    running_id = uuid4()
    running = client.post(
        "/api/v1/forge/spans/start",
        headers=headers,
        json={
            "id": str(running_id),
            "session_id": str(runtime_id),
            "trace_id": str(trace_id),
            "kind": "turn.local",
            "name": "Ravn turn",
            "source_service": "skuld",
            "started_at": started_at.isoformat(),
        },
    )
    assert running.status_code == 201
    finished = client.post(
        f"/api/v1/forge/spans/{running_id}/finish",
        headers=headers,
        json={
            "session_id": str(runtime_id),
            "ended_at": (started_at + timedelta(milliseconds=125)).isoformat(),
            "status": "completed",
            "attributes": {"result": "ok"},
        },
    )
    assert finished.status_code == 200
    assert finished.json()["duration_ms"] == 125
    assert finished.json()["attributes"] == {"result": "ok"}

    missing = client.post(
        f"/api/v1/forge/spans/{uuid4()}/finish",
        headers=headers,
        json={"session_id": str(runtime_id)},
    )
    assert missing.status_code == 404

    trace = client.get(f"/api/v1/forge/sessions/{runtime_id}/trace", headers=headers)
    assert trace.status_code == 200
    assert {lane["kind"] for lane in trace.json()["lanes"]} == {
        "system",
        "workflow",
        "agent",
    }

    summary = client.get(
        f"/api/v1/forge/sessions/{runtime_id}/trace/summary",
        headers=headers,
    )
    assert summary.status_code == 200
    expected_summary = {
        "total_duration_ms": 1000,
        "provisioning_duration_ms": 100,
        "setup_duration_ms": 50,
        "workflow_duration_ms": 300,
        "publish_duration_ms": 300,
        "cleanup_duration_ms": 40,
        "active_execution_duration_ms": 325,
        "waiting_duration_ms": 75,
        "turn_count": 2,
        "tool_call_count": 1,
    }
    assert {key: summary.json()[key] for key in expected_summary} == expected_summary


def test_complete_span_derives_missing_duration_and_trace_bounds() -> None:
    runtime_id = uuid4()
    trace_id = uuid4()
    client, _, _, _ = _client(runtime_id)
    started_at = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    ended_at = started_at + timedelta(milliseconds=250)

    explicit_end = client.post(
        "/api/v1/forge/spans/complete",
        headers=_headers(),
        json={
            "id": str(uuid4()),
            "session_id": str(runtime_id),
            "trace_id": str(trace_id),
            "kind": "turn.peer",
            "name": "Hermes reaction",
            "source_service": "skuld",
            "started_at": started_at.isoformat(),
            "ended_at": ended_at.isoformat(),
        },
    )
    implicit_end = client.post(
        "/api/v1/forge/spans/complete",
        headers=_headers(),
        json={
            "id": str(uuid4()),
            "session_id": str(runtime_id),
            "trace_id": str(trace_id),
            "kind": "tool.call",
            "name": "Inspect",
            "source_service": "skuld",
            "started_at": ended_at.isoformat(),
        },
    )

    assert explicit_end.status_code == 201
    assert explicit_end.json()["duration_ms"] == 250
    assert implicit_end.status_code == 201
    assert implicit_end.json()["duration_ms"] >= 0
    summary = client.get(
        f"/api/v1/forge/sessions/{runtime_id}/trace/summary",
        headers=_headers(),
    )
    assert summary.status_code == 200
    assert summary.json()["total_duration_ms"] >= 250
