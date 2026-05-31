"""Tests for session archive and restore functionality."""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from tests.conftest import (
    InMemorySessionRepository,
    MockEventBroadcaster,
    MockPodManager,
)
from volundr.domain.models import Session, SessionStatus
from volundr.domain.services.session import (
    SessionNotFoundError,
    SessionService,
    SessionStateError,
)


@pytest.fixture
def broadcaster() -> MockEventBroadcaster:
    return MockEventBroadcaster()


@pytest.fixture
def repository() -> InMemorySessionRepository:
    return InMemorySessionRepository()


@pytest.fixture
def pod_manager() -> MockPodManager:
    return MockPodManager()


@pytest.fixture
def service(
    repository: InMemorySessionRepository,
    pod_manager: MockPodManager,
    broadcaster: MockEventBroadcaster,
) -> SessionService:
    return SessionService(
        repository=repository,
        pod_manager=pod_manager,
        broadcaster=broadcaster,
        validate_repos=False,
    )


def _make_session(status: SessionStatus = SessionStatus.STOPPED, **kwargs) -> Session:
    """Create a session with the given status."""
    return Session(name="test-session", status=status, **kwargs)


# --- archive_session ---


@pytest.mark.asyncio
async def test_archive_from_stopped(
    service: SessionService,
    repository: InMemorySessionRepository,
) -> None:
    """Archive a stopped session."""
    session = _make_session(SessionStatus.STOPPED)
    await repository.create(session)

    archived = await service.archive_session(session.id)

    assert archived.status == SessionStatus.ARCHIVED
    assert archived.archived_at is not None
    assert archived.pod_name is None
    assert archived.chat_endpoint is None
    assert archived.code_endpoint is None


@pytest.mark.asyncio
async def test_archive_from_created(
    service: SessionService,
    repository: InMemorySessionRepository,
) -> None:
    """Archive a created (never started) session."""
    session = _make_session(SessionStatus.CREATED)
    await repository.create(session)

    archived = await service.archive_session(session.id)
    assert archived.status == SessionStatus.ARCHIVED


@pytest.mark.asyncio
async def test_archive_from_failed(
    service: SessionService,
    repository: InMemorySessionRepository,
) -> None:
    """Archive a failed session."""
    session = _make_session(SessionStatus.FAILED)
    await repository.create(session)

    archived = await service.archive_session(session.id)
    assert archived.status == SessionStatus.ARCHIVED


@pytest.mark.asyncio
async def test_archive_from_running_stops_first(
    service: SessionService,
    repository: InMemorySessionRepository,
    pod_manager: MockPodManager,
) -> None:
    """Archiving a running session should stop it first."""
    session = _make_session(SessionStatus.RUNNING, pod_name="pod-1")
    await repository.create(session)

    archived = await service.archive_session(session.id)

    assert archived.status == SessionStatus.ARCHIVED
    assert len(pod_manager.stop_calls) == 1


@pytest.mark.asyncio
async def test_archive_invalid_state_raises(
    service: SessionService,
    repository: InMemorySessionRepository,
) -> None:
    """Archiving a session in STOPPING state should raise SessionStateError."""
    session = _make_session(SessionStatus.STOPPING)
    await repository.create(session)

    with pytest.raises(SessionStateError) as exc_info:
        await service.archive_session(session.id)
    assert exc_info.value.operation == "archive"


@pytest.mark.asyncio
async def test_archive_already_archived_raises(
    service: SessionService,
    repository: InMemorySessionRepository,
) -> None:
    """Archiving an already archived session should raise SessionStateError."""
    session = _make_session(SessionStatus.ARCHIVED)
    await repository.create(session)

    with pytest.raises(SessionStateError):
        await service.archive_session(session.id)


@pytest.mark.asyncio
async def test_archive_not_found_raises(
    service: SessionService,
) -> None:
    """Archiving a nonexistent session should raise SessionNotFoundError."""
    with pytest.raises(SessionNotFoundError):
        await service.archive_session(uuid4())


@pytest.mark.asyncio
async def test_archive_broadcasts_event(
    service: SessionService,
    repository: InMemorySessionRepository,
    broadcaster: MockEventBroadcaster,
) -> None:
    """Archive should broadcast a session_updated event."""
    session = _make_session(SessionStatus.STOPPED)
    await repository.create(session)

    await service.archive_session(session.id)

    # At least one updated event for the archived session
    updated_statuses = [s.status for s in broadcaster.session_updated_events]
    assert SessionStatus.ARCHIVED in updated_statuses


# --- restore_session ---


@pytest.mark.asyncio
async def test_restore_from_archived(
    service: SessionService,
    repository: InMemorySessionRepository,
) -> None:
    """Restore an archived session to stopped state."""
    session = _make_session(SessionStatus.ARCHIVED)
    await repository.create(session)

    restored = await service.restore_session(session.id)

    assert restored.status == SessionStatus.STOPPED
    assert restored.archived_at is None


@pytest.mark.asyncio
async def test_restore_non_archived_raises(
    service: SessionService,
    repository: InMemorySessionRepository,
) -> None:
    """Restoring a non-archived session should raise SessionStateError."""
    session = _make_session(SessionStatus.STOPPED)
    await repository.create(session)

    with pytest.raises(SessionStateError) as exc_info:
        await service.restore_session(session.id)
    assert exc_info.value.operation == "restore"


@pytest.mark.asyncio
async def test_restore_not_found_raises(
    service: SessionService,
) -> None:
    """Restoring a nonexistent session should raise SessionNotFoundError."""
    with pytest.raises(SessionNotFoundError):
        await service.restore_session(uuid4())


@pytest.mark.asyncio
async def test_restore_broadcasts_event(
    service: SessionService,
    repository: InMemorySessionRepository,
    broadcaster: MockEventBroadcaster,
) -> None:
    """Restore should broadcast a session_updated event."""
    session = _make_session(SessionStatus.ARCHIVED)
    await repository.create(session)

    await service.restore_session(session.id)

    updated_statuses = [s.status for s in broadcaster.session_updated_events]
    assert SessionStatus.STOPPED in updated_statuses


# --- archive_stopped_sessions ---


@pytest.mark.asyncio
async def test_bulk_archive_stopped(
    service: SessionService,
    repository: InMemorySessionRepository,
) -> None:
    """Bulk archive all stopped sessions."""
    s1 = _make_session(SessionStatus.STOPPED)
    s2 = _make_session(SessionStatus.STOPPED)
    s3 = _make_session(SessionStatus.RUNNING, pod_name="pod-3")
    s4 = _make_session(SessionStatus.CREATED)

    for s in [s1, s2, s3, s4]:
        await repository.create(s)

    archived_ids = await service.archive_stopped_sessions()

    assert len(archived_ids) == 2
    assert s1.id in archived_ids
    assert s2.id in archived_ids

    # Verify they are actually archived
    for sid in archived_ids:
        session = await repository.get(sid)
        assert session.status == SessionStatus.ARCHIVED

    # Others unchanged
    assert (await repository.get(s3.id)).status == SessionStatus.RUNNING
    assert (await repository.get(s4.id)).status == SessionStatus.CREATED


@pytest.mark.asyncio
async def test_bulk_archive_none_stopped(
    service: SessionService,
    repository: InMemorySessionRepository,
) -> None:
    """Bulk archive with no stopped sessions returns empty list."""
    s1 = _make_session(SessionStatus.RUNNING, pod_name="pod-1")
    await repository.create(s1)

    archived_ids = await service.archive_stopped_sessions()
    assert archived_ids == []


# --- list_sessions filtering ---


@pytest.mark.asyncio
async def test_list_excludes_archived_by_default(
    service: SessionService,
    repository: InMemorySessionRepository,
) -> None:
    """List sessions should exclude archived sessions by default."""
    s1 = _make_session(SessionStatus.RUNNING, pod_name="pod-1")
    s2 = _make_session(SessionStatus.ARCHIVED)
    s3 = _make_session(SessionStatus.STOPPED)

    for s in [s1, s2, s3]:
        await repository.create(s)

    sessions = await service.list_sessions()

    assert len(sessions) == 2
    statuses = {s.status for s in sessions}
    assert SessionStatus.ARCHIVED not in statuses


@pytest.mark.asyncio
async def test_list_include_archived(
    service: SessionService,
    repository: InMemorySessionRepository,
) -> None:
    """List sessions with include_archived=True returns all."""
    s1 = _make_session(SessionStatus.RUNNING, pod_name="pod-1")
    s2 = _make_session(SessionStatus.ARCHIVED)

    for s in [s1, s2]:
        await repository.create(s)

    sessions = await service.list_sessions(include_archived=True)
    assert len(sessions) == 2


@pytest.mark.asyncio
async def test_list_filter_by_status(
    service: SessionService,
    repository: InMemorySessionRepository,
) -> None:
    """List sessions with status filter returns only matching."""
    s1 = _make_session(SessionStatus.RUNNING, pod_name="pod-1")
    s2 = _make_session(SessionStatus.ARCHIVED)
    s3 = _make_session(SessionStatus.STOPPED)

    for s in [s1, s2, s3]:
        await repository.create(s)

    archived = await service.list_sessions(status=SessionStatus.ARCHIVED)
    assert len(archived) == 1
    assert archived[0].status == SessionStatus.ARCHIVED

    running = await service.list_sessions(status=SessionStatus.RUNNING)
    assert len(running) == 1
    assert running[0].status == SessionStatus.RUNNING


# --- REST endpoint tests ---


@pytest.mark.asyncio
async def test_rest_archive_endpoint(
    service: SessionService,
    repository: InMemorySessionRepository,
) -> None:
    """Test the archive REST endpoint via service layer."""
    app = _make_test_app(service)
    client = TestClient(app)

    session = _make_session(SessionStatus.STOPPED)
    await repository.create(session)

    response = client.patch(f"/api/v1/forge/sessions/{session.id}/archive")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "archived"
    assert data["archived_at"] is not None


@pytest.mark.asyncio
async def test_rest_restore_endpoint(
    service: SessionService,
    repository: InMemorySessionRepository,
) -> None:
    """Test the restore REST endpoint."""
    client = TestClient(_make_test_app(service))

    session = _make_session(SessionStatus.ARCHIVED)
    await repository.create(session)

    response = client.patch(f"/api/v1/forge/sessions/{session.id}/restore")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "stopped"
    assert data["archived_at"] is None


@pytest.mark.asyncio
async def test_rest_archive_not_found(
    service: SessionService,
) -> None:
    """Archive endpoint returns 404 for missing session."""
    client = TestClient(_make_test_app(service))

    response = client.patch(f"/api/v1/forge/sessions/{uuid4()}/archive")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_rest_archive_conflict(
    service: SessionService,
    repository: InMemorySessionRepository,
) -> None:
    """Archive endpoint returns 409 for invalid state."""
    client = TestClient(_make_test_app(service))

    session = _make_session(SessionStatus.STOPPING)
    await repository.create(session)

    response = client.patch(f"/api/v1/forge/sessions/{session.id}/archive")
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_rest_restore_conflict(
    service: SessionService,
    repository: InMemorySessionRepository,
) -> None:
    """Restore endpoint returns 409 for non-archived session."""
    client = TestClient(_make_test_app(service))

    session = _make_session(SessionStatus.STOPPED)
    await repository.create(session)

    response = client.patch(f"/api/v1/forge/sessions/{session.id}/restore")
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_rest_bulk_archive(
    service: SessionService,
    repository: InMemorySessionRepository,
) -> None:
    """Bulk archive endpoint archives all stopped sessions."""
    client = TestClient(_make_test_app(service))

    s1 = _make_session(SessionStatus.STOPPED)
    s2 = _make_session(SessionStatus.STOPPED)
    s3 = _make_session(SessionStatus.RUNNING, pod_name="pod-3")

    for s in [s1, s2, s3]:
        await repository.create(s)

    response = client.post("/api/v1/forge/sessions/archive-stopped")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2


@pytest.mark.asyncio
async def test_rest_list_excludes_archived(
    service: SessionService,
    repository: InMemorySessionRepository,
) -> None:
    """GET /sessions excludes archived by default."""
    client = TestClient(_make_test_app(service))

    s1 = _make_session(SessionStatus.STOPPED)
    s2 = _make_session(SessionStatus.ARCHIVED)

    for s in [s1, s2]:
        await repository.create(s)

    response = client.get("/api/v1/forge/sessions")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["status"] == "stopped"


@pytest.mark.asyncio
async def test_rest_list_with_status_filter(
    service: SessionService,
    repository: InMemorySessionRepository,
) -> None:
    """GET /sessions?status=archived returns only archived."""
    client = TestClient(_make_test_app(service))

    s1 = _make_session(SessionStatus.STOPPED)
    s2 = _make_session(SessionStatus.ARCHIVED)

    for s in [s1, s2]:
        await repository.create(s)

    response = client.get("/api/v1/forge/sessions?status=archived")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["status"] == "archived"


@pytest.mark.asyncio
async def test_rest_list_with_include_archived(
    service: SessionService,
    repository: InMemorySessionRepository,
) -> None:
    """GET /sessions?include_archived=true returns all sessions."""
    client = TestClient(_make_test_app(service))

    s1 = _make_session(SessionStatus.STOPPED)
    s2 = _make_session(SessionStatus.ARCHIVED)

    for s in [s1, s2]:
        await repository.create(s)

    response = client.get("/api/v1/forge/sessions?include_archived=true")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2


# --- Helpers ---


def _make_test_app(service: SessionService):
    """Create a minimal FastAPI app for testing."""
    from fastapi import FastAPI

    from volundr.adapters.inbound.rest import create_router

    app = FastAPI()
    router = create_router(session_service=service)
    app.include_router(router)
    return app
