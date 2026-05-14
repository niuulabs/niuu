from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from tests.conftest import (
    InMemoryChronicleRepository,
    InMemorySessionRepository,
    InMemoryTimelineRepository,
    MockPodManager,
)
from volundr.adapters.outbound.archive_store import FileSystemArchiveStore
from volundr.adapters.outbound.local_storage_adapter import LocalStorageAdapter
from volundr.domain.models import Session, SessionStatus, TimelineEvent, TimelineEventType
from volundr.domain.services import ChronicleService, SessionArchiveService, SessionService
from volundr.domain.services.session_archive import SessionArchiveNotAvailableError


@pytest.fixture
def storage(tmp_path):
    return LocalStorageAdapter(base_dir=str(tmp_path))


@pytest.fixture
def session_repository():
    return InMemorySessionRepository()


@pytest.fixture
def session_service(session_repository):
    return SessionService(
        repository=session_repository,
        pod_manager=MockPodManager(),
        validate_repos=False,
    )


@pytest.fixture
def archive_store():
    return FileSystemArchiveStore()


@pytest.fixture
def chronicle_service(session_service):
    return ChronicleService(
        InMemoryChronicleRepository(),
        session_service,
        timeline_repository=InMemoryTimelineRepository(),
    )


@pytest.mark.asyncio
async def test_session_archive_service_reads_transcript_and_logs(
    storage,
    session_repository,
    session_service,
    archive_store,
):
    session = Session(
        name="archive-me",
        model="claude-sonnet-4",
        status=SessionStatus.STOPPED,
    )
    await session_repository.create(session)
    await storage.create_session_workspace(str(session.id), user_id="u1", tenant_id="t1")
    workspace = storage.resolve_session_workspace_path(str(session.id))
    assert workspace is not None

    transcript_file = Path(workspace) / ".skuld" / f"conversation_{session.id}.json"
    transcript_file.parent.mkdir(parents=True, exist_ok=True)
    transcript_file.write_text(
        json.dumps({"turns": [{"id": "1", "role": "user", "content": "hello"}]}),
        encoding="utf-8",
    )
    (Path(workspace) / ".skuld.log").write_text(
        "2026-01-01 12:00:00 skuld INFO hello\n",
        encoding="utf-8",
    )

    archive_service = SessionArchiveService(session_service, storage, archive_store)
    transcript = await archive_service.get_transcript(session.id)
    logs = await archive_service.get_logs(session.id)

    assert transcript["turns"][0]["content"] == "hello"
    assert logs["returned"] == 1
    assert logs["lines"][0]["message"] == "hello"


@pytest.mark.asyncio
async def test_session_archive_service_builds_archive_with_chronicle_and_timeline(
    storage,
    session_repository,
    session_service,
    chronicle_service,
    archive_store,
):
    session = Session(
        name="archive-me",
        model="claude-sonnet-4",
        status=SessionStatus.STOPPED,
    )
    await session_repository.create(session)
    await storage.create_session_workspace(str(session.id), user_id="u1", tenant_id="t1")
    workspace_path = storage.resolve_session_workspace_path(str(session.id))
    assert workspace_path is not None
    workspace = Path(workspace_path)
    (workspace / ".skuld").mkdir(parents=True, exist_ok=True)
    (workspace / ".skuld" / f"conversation_{session.id}.json").write_text(
        json.dumps({"turns": [{"id": "1", "role": "assistant", "content": "done"}]}),
        encoding="utf-8",
    )
    (workspace / ".skuld.log").write_text(
        "2026-01-01 12:00:00 skuld INFO archived\n",
        encoding="utf-8",
    )

    chronicle = await chronicle_service.create_chronicle(session.id)
    await chronicle_service.add_timeline_event(
        session.id,
        TimelineEvent(
            id=uuid4(),
            chronicle_id=chronicle.id,
            session_id=session.id,
            t=5,
            type=TimelineEventType.MESSAGE,
            label="assistant replied",
            tokens=12,
            created_at=datetime.now(UTC),
        ),
    )

    archive_service = SessionArchiveService(
        session_service,
        storage,
        archive_store,
        chronicle_service=chronicle_service,
    )
    manifest = await archive_service.build_archive(session.id, force=True)

    assert manifest["counts"]["turns"] == 1
    assert (workspace / ".volundr" / "archive" / "chronicle.json").exists()
    assert (workspace / ".volundr" / "archive" / "timeline.json").exists()

    cached_manifest = await archive_service.build_archive(session.id)
    assert cached_manifest["created_at"] == manifest["created_at"]

    download_path = await archive_service.get_transcript_download_path(session.id, "md")
    assert download_path.read_text(encoding="utf-8").startswith("# Session Transcript")

    archive_root = await archive_service.get_archive_root(session.id)
    assert (archive_root / "timeline.json").exists()


@pytest.mark.asyncio
async def test_session_archive_service_uses_file_endpoint_fallback(
    tmp_path,
    session_repository,
    archive_store,
):
    workspace = tmp_path / "external-workspace"
    (workspace / ".skuld").mkdir(parents=True, exist_ok=True)
    session = Session(
        name="endpoint-fallback",
        model="claude-sonnet-4",
        status=SessionStatus.STOPPED,
        code_endpoint=f"file://{workspace}",
    )
    await session_repository.create(session)
    transcript_file = workspace / ".skuld" / f"conversation_{session.id}.json"
    transcript_file.write_text(
        json.dumps({"turns": [{"id": "1", "role": "assistant", "content": "from file"}]}),
        encoding="utf-8",
    )

    class EmptyStorage:
        def resolve_session_workspace_path(self, _session_id: str) -> str | None:
            return None

        async def get_workspace_by_session(self, _session_id: str):
            return None

    session_service = SessionService(
        repository=session_repository,
        pod_manager=MockPodManager(),
        validate_repos=False,
    )
    archive_service = SessionArchiveService(session_service, EmptyStorage(), archive_store)

    transcript = await archive_service.get_transcript(session.id)

    assert transcript["turns"][0]["content"] == "from file"


@pytest.mark.asyncio
async def test_session_archive_service_manifest_download_and_error_paths(
    storage,
    session_repository,
    session_service,
    archive_store,
):
    session = Session(
        name="manifest-check",
        model="claude-sonnet-4",
        status=SessionStatus.STOPPED,
    )
    await session_repository.create(session)
    await storage.create_session_workspace(str(session.id), user_id="u1", tenant_id="t1")
    workspace_path = storage.resolve_session_workspace_path(str(session.id))
    assert workspace_path is not None
    workspace = Path(workspace_path)
    (workspace / ".skuld").mkdir(parents=True, exist_ok=True)
    (workspace / ".skuld" / f"conversation_{session.id}.json").write_text(
        json.dumps({"turns": []}),
        encoding="utf-8",
    )

    archive_service = SessionArchiveService(session_service, storage, archive_store)
    manifest = await archive_service.get_archive_manifest(session.id)
    download_path = await archive_service.get_transcript_download_path(session.id, "json")

    assert manifest["counts"]["turns"] == 0
    assert download_path.name == "transcript.json"

    with pytest.raises(ValueError, match="Unsupported transcript format"):
        await archive_service.get_transcript_download_path(session.id, "txt")


@pytest.mark.asyncio
async def test_session_archive_service_raises_when_workspace_unavailable(
    session_repository,
    archive_store,
):
    session = Session(
        name="missing-workspace",
        model="claude-sonnet-4",
        status=SessionStatus.STOPPED,
    )
    await session_repository.create(session)

    class MissingStorage:
        def resolve_session_workspace_path(self, _session_id: str) -> str | None:
            return None

        async def get_workspace_by_session(self, _session_id: str):
            return None

    session_service = SessionService(
        repository=session_repository,
        pod_manager=MockPodManager(),
        validate_repos=False,
    )
    archive_service = SessionArchiveService(session_service, MissingStorage(), archive_store)

    with pytest.raises(SessionArchiveNotAvailableError, match="No accessible workspace path"):
        await archive_service.get_archive_manifest(session.id)

    with pytest.raises(LookupError):
        await archive_service.get_transcript(uuid4())


@pytest.mark.asyncio
async def test_session_archive_service_uses_workspace_metadata_fallback(
    storage,
    session_repository,
    session_service,
    chronicle_service,
    archive_store,
):
    session = Session(
        name="workspace-metadata",
        model="claude-sonnet-4",
        status=SessionStatus.STOPPED,
    )
    await session_repository.create(session)
    await storage.create_session_workspace(str(session.id), user_id="u1", tenant_id="t1")
    workspace_path = storage.resolve_session_workspace_path(str(session.id))
    assert workspace_path is not None
    workspace = Path(workspace_path)
    (workspace / ".skuld").mkdir(parents=True, exist_ok=True)
    (workspace / ".skuld" / f"conversation_{session.id}.json").write_text(
        json.dumps({"turns": [{"id": "1", "role": "user", "content": "meta path"}]}),
        encoding="utf-8",
    )

    class WorkspaceOnlyStorage:
        async def get_workspace_by_session(self, session_id: str):
            return await storage.get_workspace_by_session(session_id)

        def resolve_session_workspace_path(self, _session_id: str) -> str | None:
            return None

    archive_service = SessionArchiveService(
        session_service,
        WorkspaceOnlyStorage(),
        archive_store,
        chronicle_service=chronicle_service,
    )

    transcript = await archive_service.get_transcript(session.id)
    manifest = await archive_service.get_archive_manifest(session.id)

    assert transcript["turns"][0]["content"] == "meta path"
    assert manifest["artifacts"]["chronicle"] is None
    assert manifest["artifacts"]["timeline"] is None


@pytest.mark.asyncio
async def test_session_archive_service_can_use_config_scoped_archive_root(
    tmp_path,
    monkeypatch,
    storage,
    session_repository,
    session_service,
):
    monkeypatch.setenv("NIUU_HOME", str(tmp_path / ".niuu"))
    session = Session(
        name="config-archive",
        model="claude-sonnet-4",
        status=SessionStatus.STOPPED,
    )
    await session_repository.create(session)
    await storage.create_session_workspace(str(session.id), user_id="u1", tenant_id="t1")
    workspace_path = storage.resolve_session_workspace_path(str(session.id))
    assert workspace_path is not None
    workspace = Path(workspace_path)
    (workspace / ".skuld").mkdir(parents=True, exist_ok=True)
    (workspace / ".skuld" / f"conversation_{session.id}.json").write_text(
        json.dumps({"turns": [{"id": "1", "role": "assistant", "content": "config root"}]}),
        encoding="utf-8",
    )

    archive_service = SessionArchiveService(
        session_service,
        storage,
        FileSystemArchiveStore(location="config", path="archives-store"),
    )

    manifest = await archive_service.build_archive(session.id, force=True)
    archive_root = await archive_service.get_archive_root(session.id)

    assert manifest["location"] == "config"
    assert archive_root == tmp_path / ".niuu" / "archives-store" / str(session.id)
    assert (archive_root / "transcript.json").exists()
