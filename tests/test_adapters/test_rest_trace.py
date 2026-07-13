"""Trace API authorization for Forge sessions and resident runtimes."""

from __future__ import annotations

from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from volundr.adapters.inbound.rest_trace import create_trace_router
from volundr.domain.services.resident_runtime import ResidentRuntimeNotFoundError


class _SpanRepository:
    def __init__(self) -> None:
        self.spans = []

    async def upsert_span(self, span):
        self.spans.append(span)
        return span

    async def finish_span(self, span_id, ended_at, status, attributes=None):
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
