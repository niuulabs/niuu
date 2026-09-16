"""Recovery must persist history and reject misleading empty success responses."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.test_domain.test_external_session_service import FakeProvider, _record
from volundr.adapters.inbound.rest import create_router
from volundr.domain.history_import import (
    HistoryImportConflictError,
    HistoryImportError,
    HistoryImportSessionNotFoundError,
    HistoryImportValidationError,
)
from volundr.domain.models import LocalMountSource, Session, SessionLogEntry
from volundr.domain.ports import SessionEventLogRepository
from volundr.domain.services import SessionAccessDeniedError, SessionService
from volundr.domain.services.external_sessions import ExternalSessionService


@pytest.fixture
def recovery(repository, pod_manager, tmp_path):
    provider = FakeProvider("codex", "codex", [_record("codex", "codex", "native-1", tmp_path)])
    session_service = SessionService(repository=repository, pod_manager=pod_manager)
    event_log = AsyncMock(spec=SessionEventLogRepository)
    event_log.import_history.return_value = 1
    service = ExternalSessionService(
        [provider], repository, session_service, event_log_repository=event_log
    )
    return service, provider, session_service, event_log


async def test_import_returns_only_after_native_history_is_committed(recovery):
    service, _, _, event_log = recovery
    session = await service.import_session("codex", "native-1")
    target, source, frames = event_log.import_history.await_args.args
    assert target == session.id
    assert source == "codex:native-1"
    assert all(frame.session_id == session.id for frame in frames)
    assert frames[0].payload["message"]["content"] == "Recovered request"
    assert frames[0].ts == datetime(2026, 6, 1, 10, 0, tzinfo=UTC)


@pytest.mark.parametrize("error", [ValueError("corrupt"), OSError("missing"), None])
async def test_unreadable_or_empty_native_history_creates_no_empty_session(
    recovery, repository, error
):
    service, provider, _, event_log = recovery
    provider.read_transcript = AsyncMock(side_effect=error, return_value=[])
    with pytest.raises(HistoryImportValidationError):
        await service.import_session("codex", "native-1")
    assert await repository.list() == []
    event_log.import_history.assert_not_awaited()


async def test_missing_durable_storage_is_an_explicit_failure(recovery, repository):
    service, _, _, _ = recovery
    service._event_log = None
    with pytest.raises(HistoryImportError, match="not configured"):
        await service.import_session("codex", "native-1")
    assert await repository.list() == []


async def test_failed_commit_exposes_created_session_for_safe_retry(recovery, repository):
    service, _, _, event_log = recovery
    event_log.import_history.side_effect = RuntimeError("database unavailable")
    with pytest.raises(HistoryImportError, match="history/import") as caught:
        await service.import_session("codex", "native-1")
    sessions = await repository.list()
    assert len(sessions) == 1
    assert str(sessions[0].id) in str(caught.value)
    event_log.import_history.side_effect = None
    result = await service.backfill_session(sessions[0].id)
    assert result["imported_frames"] == 1


async def test_backfill_uses_stored_identity_and_reports_idempotent_retry(recovery):
    service, _, _, event_log = recovery
    session = await service.import_session("codex", "native-1")
    event_log.import_history.return_value = 0
    result = await service.backfill_session(session.id)
    assert result == {
        "session_id": str(session.id),
        "provider": "codex",
        "external_session_id": "native-1",
        "imported_frames": 0,
        "source_frames": 1,
        "partial": False,
    }


async def test_access_is_checked_before_opening_native_transcript(recovery):
    service, provider, session_service, event_log = recovery
    session = await service.import_session("codex", "native-1")
    provider.read_transcript = AsyncMock()
    event_log.reset_mock()
    session_service._check_access = AsyncMock(
        side_effect=SessionAccessDeniedError(session.id, "foreign-user")
    )
    with pytest.raises(SessionAccessDeniedError):
        await service.backfill_session(session.id, principal=object())
    provider.read_transcript.assert_not_awaited()
    event_log.import_history.assert_not_awaited()


async def test_backfill_rejects_workspace_identity_drift(recovery, repository, tmp_path):
    service, provider, _, event_log = recovery
    session = await service.import_session("codex", "native-1")
    moved = tmp_path / "different"
    moved.mkdir()
    provider._records[0] = provider._records[0].model_copy(update={"workspace_path": str(moved)})
    event_log.reset_mock()
    with pytest.raises(HistoryImportValidationError, match="workspace differs"):
        await service.backfill_session(session.id)
    event_log.import_history.assert_not_awaited()


async def test_backfill_missing_session_and_native_identity(recovery, repository, tmp_path):
    service, _, _, _ = recovery
    with pytest.raises(HistoryImportSessionNotFoundError):
        await service.backfill_session(uuid4())
    session = Session(name="regular", source=LocalMountSource(local_path=str(tmp_path)))
    await repository.create(session)
    with pytest.raises(HistoryImportValidationError, match="no imported native"):
        await service.backfill_session(session.id)


async def test_partial_source_is_reported(recovery):
    service, provider, _, _ = recovery
    session = await service.import_session("codex", "native-1")
    provider.read_transcript = AsyncMock(
        return_value=[
            SessionLogEntry(
                session_id=session.id,
                seq=1,
                kind="user",
                ts=datetime.now(UTC),
                payload={
                    "type": "user",
                    "message": {"content": "saved"},
                    "metadata": {"native_import": {"partial": True}},
                },
            )
        ]
    )
    assert (await service.backfill_session(session.id))["partial"] is True


@pytest.mark.parametrize(
    "error,status",
    [
        (HistoryImportConflictError("active writer"), 409),
        (HistoryImportSessionNotFoundError("missing"), 404),
        (HistoryImportValidationError("invalid source"), 422),
        (HistoryImportError("storage unavailable"), 503),
        (SessionAccessDeniedError(uuid4(), "user"), 403),
    ],
)
def test_rest_backfill_maps_failures(recovery, error, status):
    service, _, session_service, _ = recovery
    service.backfill_session = AsyncMock(side_effect=error)
    app = FastAPI()
    app.include_router(create_router(session_service, external_session_service=service))
    with TestClient(app) as client:
        response = client.post(f"/api/v1/forge/sessions/{uuid4()}/history/import")
    assert response.status_code == status
    assert response.json()["detail"] == str(error)


def test_rest_backfill_success_and_unavailable(recovery):
    service, _, session_service, _ = recovery
    sid = uuid4()
    result = {"session_id": str(sid), "imported_frames": 15}
    service.backfill_session = AsyncMock(return_value=result)
    app = FastAPI()
    app.include_router(create_router(session_service, external_session_service=service))
    with TestClient(app) as client:
        response = client.post(f"/api/v1/forge/sessions/{sid}/history/import")
    assert response.status_code == 200
    assert response.json() == result
    app = FastAPI()
    app.include_router(create_router(session_service))
    with TestClient(app) as client:
        assert client.post(f"/api/v1/forge/sessions/{sid}/history/import").status_code == 503


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer invalid"}])
@pytest.mark.parametrize("endpoint", ["sessions/import", f"sessions/{uuid4()}/history/import"])
def test_history_mutations_require_valid_identity_when_configured(recovery, headers, endpoint):
    from volundr.domain.ports import IdentityPort, InvalidTokenError

    service, _, session_service, event_log = recovery
    service.import_session = AsyncMock()
    service.backfill_session = AsyncMock()
    app = FastAPI()
    identity = AsyncMock(spec=IdentityPort)
    identity.validate_token.side_effect = InvalidTokenError("Invalid token")
    app.state.identity = identity
    app.include_router(create_router(session_service, external_session_service=service))
    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/forge/{endpoint}",
            headers=headers,
            json={"provider": "codex", "external_id": "native-1"},
        )
    assert response.status_code == 401
    service.import_session.assert_not_awaited()
    service.backfill_session.assert_not_awaited()
    event_log.import_history.assert_not_awaited()
