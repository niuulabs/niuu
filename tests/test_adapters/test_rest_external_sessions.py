"""Tests for external session discovery/import REST endpoints and resume alias."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from volundr.adapters.inbound.rest import create_router
from volundr.domain.models import (
    ExternalSessionRecord,
    LocalMountSource,
    Session,
    SessionStatus,
)
from volundr.domain.ports import ExternalSessionProvider
from volundr.domain.services import ExternalSessionService, SessionService


class FakeProvider(ExternalSessionProvider):
    """In-memory external session provider for tests."""

    def __init__(self, name: str, harness: str, records: list[ExternalSessionRecord]):
        self._name = name
        self._harness = harness
        self._records = records

    @property
    def name(self) -> str:
        return self._name

    @property
    def harness(self) -> str:
        return self._harness

    async def list_sessions(self) -> list[ExternalSessionRecord]:
        return list(self._records)

    async def get_session(self, external_id: str) -> ExternalSessionRecord | None:
        for record in self._records:
            if record.external_id == external_id:
                return record
        return None


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    path = tmp_path / "workspace"
    path.mkdir()
    return path


@pytest.fixture
def claude_record(workspace: Path) -> ExternalSessionRecord:
    return ExternalSessionRecord(
        provider="claude-code",
        harness="claude",
        external_id="2e877b9f-4b8a-4d46-8f00-03f6163addd5",
        workspace_path=str(workspace),
        title="Fix the login bug",
        model="claude-sonnet-4-6",
        created_at=datetime(2026, 6, 1, 10, 0, tzinfo=UTC),
        updated_at=datetime(2026, 6, 1, 11, 0, tzinfo=UTC),
        live=False,
        workspace_exists=True,
    )


@pytest.fixture
def session_service(repository, pod_manager) -> SessionService:
    return SessionService(repository=repository, pod_manager=pod_manager)


@pytest.fixture
def app(repository, session_service, claude_record) -> FastAPI:
    provider = FakeProvider("claude-code", "claude", [claude_record])
    external_service = ExternalSessionService([provider], repository, session_service)

    app = FastAPI()
    router = create_router(
        session_service=session_service,
        external_session_service=external_service,
    )
    app.include_router(router)
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


class TestListExternalSessions:
    def test_lists_discoverable_sessions(self, client, claude_record) -> None:
        response = client.get("/api/v1/forge/external-sessions")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["provider"] == "claude-code"
        assert data[0]["harness"] == "claude"
        assert data[0]["external_id"] == claude_record.external_id
        assert data[0]["title"] == "Fix the login bug"
        assert data[0]["imported_session_id"] is None

    def test_unknown_provider_returns_404(self, client) -> None:
        response = client.get("/api/v1/forge/external-sessions", params={"provider": "nope"})
        assert response.status_code == 404

    def test_unavailable_without_service(self, session_service) -> None:
        app = FastAPI()
        app.include_router(create_router(session_service=session_service))
        client = TestClient(app)

        response = client.get("/api/v1/forge/external-sessions")
        assert response.status_code == 503

        response = client.post(
            "/api/v1/forge/sessions/import",
            json={"provider": "claude-code", "external_id": "x"},
        )
        assert response.status_code == 503


class TestImportSession:
    def test_imports_external_session(self, client, claude_record, workspace) -> None:
        response = client.post(
            "/api/v1/forge/sessions/import",
            json={
                "provider": "claude-code",
                "external_id": claude_record.external_id,
                "name": "imported-login-fix",
            },
        )

        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "imported-login-fix"
        assert data["origin"] == "claude"
        assert data["external_session_id"] == claude_record.external_id
        assert data["source"]["type"] == "local_mount"
        assert data["source"]["local_path"] == str(workspace)

    def test_imported_session_appears_in_listing(self, client, claude_record) -> None:
        client.post(
            "/api/v1/forge/sessions/import",
            json={"provider": "claude-code", "external_id": claude_record.external_id},
        )

        sessions = client.get("/api/v1/forge/sessions").json()
        assert len(sessions) == 1
        assert sessions[0]["origin"] == "claude"

        external = client.get("/api/v1/forge/external-sessions").json()
        assert external[0]["imported_session_id"] == sessions[0]["id"]

    def test_unknown_external_session_returns_404(self, client) -> None:
        response = client.post(
            "/api/v1/forge/sessions/import",
            json={"provider": "claude-code", "external_id": "missing"},
        )
        assert response.status_code == 404

    def test_duplicate_import_returns_409(self, client, claude_record) -> None:
        body = {"provider": "claude-code", "external_id": claude_record.external_id}
        assert client.post("/api/v1/forge/sessions/import", json=body).status_code == 201

        response = client.post("/api/v1/forge/sessions/import", json=body)
        assert response.status_code == 409

    def test_missing_workspace_returns_422(self, repository, session_service, tmp_path) -> None:
        record = ExternalSessionRecord(
            provider="claude-code",
            harness="claude",
            external_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            workspace_path=str(tmp_path / "gone"),
            workspace_exists=False,
        )
        provider = FakeProvider("claude-code", "claude", [record])
        external_service = ExternalSessionService([provider], repository, session_service)
        app = FastAPI()
        app.include_router(
            create_router(
                session_service=session_service,
                external_session_service=external_service,
            )
        )
        client = TestClient(app)

        response = client.post(
            "/api/v1/forge/sessions/import",
            json={"provider": "claude-code", "external_id": record.external_id},
        )
        assert response.status_code == 422


class TestResumeAlias:
    async def test_resume_starts_stopped_session(self, client, repository) -> None:
        session = Session(
            name="stopped-session",
            status=SessionStatus.STOPPED,
            source=LocalMountSource(local_path="/tmp/ws"),
        )
        await repository.create(session)

        response = client.post(f"/api/v1/forge/sessions/{session.id}/resume")

        assert response.status_code == 200
        assert response.json()["status"] == "starting"

    async def test_resume_matches_start_for_running_session(self, client, repository) -> None:
        session = Session(
            name="running-session",
            status=SessionStatus.RUNNING,
            source=LocalMountSource(local_path="/tmp/ws"),
        )
        await repository.create(session)

        response = client.post(f"/api/v1/forge/sessions/{session.id}/resume")

        assert response.status_code == 409

    def test_resume_unknown_session_returns_404(self, client) -> None:
        response = client.post("/api/v1/forge/sessions/00000000-0000-0000-0000-000000000000/resume")
        assert response.status_code == 404
