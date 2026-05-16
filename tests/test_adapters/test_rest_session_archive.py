from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.conftest import InMemorySessionRepository, MockPodManager
from volundr.adapters.inbound.rest import create_router
from volundr.adapters.outbound.archive_store import FileSystemArchiveStore
from volundr.adapters.outbound.local_storage_adapter import LocalStorageAdapter
from volundr.domain.models import LocalMountSource, Session, SessionStatus
from volundr.domain.services import SessionArchiveService, SessionService


@pytest.fixture
def repository():
    return InMemorySessionRepository()


@pytest.fixture
def storage(tmp_path):
    return LocalStorageAdapter(base_dir=str(tmp_path))


@pytest.fixture
def session_service(repository):
    return SessionService(
        repository=repository,
        pod_manager=MockPodManager(),
        validate_repos=False,
    )


@pytest.fixture
def archive_service(session_service, storage):
    return SessionArchiveService(session_service, storage, FileSystemArchiveStore())


def build_app(session_service, archive_service):
    app = FastAPI()
    app.include_router(create_router(session_service, archive_service=archive_service))
    return app


async def _seed_workspace(storage, repository):
    session = Session(name="seed", status=SessionStatus.STOPPED)
    await repository.create(session)
    await storage.create_session_workspace(str(session.id), user_id="u1", tenant_id="t1")
    workspace = storage.resolve_session_workspace_path(str(session.id))
    assert workspace is not None
    workspace_path = Path(workspace)
    (workspace_path / ".skuld").mkdir(parents=True, exist_ok=True)
    (workspace_path / ".skuld" / f"conversation_{session.id}.json").write_text(
        json.dumps({"turns": [{"id": "1", "role": "user", "content": "hello from archive"}]}),
        encoding="utf-8",
    )
    (workspace_path / ".skuld.log").write_text(
        "2026-01-01 12:00:00 skuld INFO hello from archive\n",
        encoding="utf-8",
    )
    return session


@pytest.mark.asyncio
async def test_get_conversation_falls_back_to_workspace(
    repository,
    storage,
    session_service,
    archive_service,
):
    session = await _seed_workspace(storage, repository)
    client = TestClient(build_app(session_service, archive_service))

    response = client.get(f"/api/v1/volundr/sessions/{session.id}/conversation")
    assert response.status_code == 200
    assert response.json()["turns"][0]["content"] == "hello from archive"


@pytest.mark.asyncio
async def test_get_logs_aggregate_falls_back_to_workspace(
    repository,
    storage,
    session_service,
    archive_service,
):
    session = await _seed_workspace(storage, repository)
    client = TestClient(build_app(session_service, archive_service))

    response = client.get(f"/api/v1/volundr/sessions/{session.id}/logs/aggregate")
    assert response.status_code == 200
    payload = response.json()
    assert payload["returned"] == 1
    assert payload["lines"][0]["message"] == "hello from archive"


@pytest.mark.asyncio
async def test_download_transcript_builds_archive_and_returns_markdown(
    repository,
    storage,
    session_service,
    archive_service,
):
    session = await _seed_workspace(storage, repository)
    client = TestClient(build_app(session_service, archive_service))

    response = client.get(f"/api/v1/volundr/sessions/{session.id}/transcript/download?format=md")
    assert response.status_code == 200
    assert response.text.startswith("# Session Transcript")
    assert "hello from archive" in response.text


@pytest.mark.asyncio
async def test_get_conversation_falls_back_to_local_mount_source_without_storage(
    repository,
    session_service,
    tmp_path,
):
    workspace = tmp_path / "local-mount-rest"
    (workspace / ".skuld").mkdir(parents=True, exist_ok=True)
    session = Session(
        name="local-mount-rest",
        status=SessionStatus.STOPPED,
        source=LocalMountSource(local_path=str(workspace)),
    )
    await repository.create(session)
    (workspace / ".skuld" / f"conversation_{session.id}.json").write_text(
        json.dumps({"turns": [{"id": "1", "role": "assistant", "content": "rest mount"}]}),
        encoding="utf-8",
    )

    class MissingStorage:
        def resolve_session_workspace_path(self, _session_id: str) -> str | None:
            return None

        async def get_workspace_by_session(self, _session_id: str):
            return None

    archive_service = SessionArchiveService(
        session_service,
        MissingStorage(),
        FileSystemArchiveStore(),
    )
    client = TestClient(build_app(session_service, archive_service))

    response = client.get(f"/api/v1/volundr/sessions/{session.id}/conversation")

    assert response.status_code == 200
    assert response.json()["turns"][0]["content"] == "rest mount"


@pytest.mark.asyncio
async def test_get_session_transcript_reads_config_archive_without_workspace_lookup(
    repository,
    storage,
    session_service,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("NIUU_HOME", str(tmp_path / ".niuu"))
    session = await _seed_workspace(storage, repository)
    seeded_archive = SessionArchiveService(
        session_service,
        storage,
        FileSystemArchiveStore(location="config", path="archives-store"),
    )
    await seeded_archive.build_archive(session.id, force=True)

    class MissingStorage:
        def resolve_session_workspace_path(self, _session_id: str) -> str | None:
            return None

        async def get_workspace_by_session(self, _session_id: str):
            return None

    replay_archive = SessionArchiveService(
        session_service,
        MissingStorage(),
        FileSystemArchiveStore(location="config", path="archives-store"),
    )
    client = TestClient(build_app(session_service, replay_archive))

    response = client.get(f"/api/v1/volundr/sessions/{session.id}/transcript")

    assert response.status_code == 200
    assert response.json()["turns"][0]["content"] == "hello from archive"
