"""Real filesystem archive service -> REST projection/window/tool-result regression."""

import json
from pathlib import Path
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from niuu.domain.conversation_timeline import project_timeline
from tests.conftest import (
    InMemorySessionRepository,
    MockPodManager,
    make_session_participant_service,
)
from volundr.adapters.inbound.rest import create_router
from volundr.adapters.outbound.archive_store import FileSystemArchiveStore
from volundr.adapters.outbound.local_storage_adapter import LocalStorageAdapter
from volundr.config import LocalMountsConfig
from volundr.domain.models import Session, SessionStatus
from volundr.domain.services import SessionArchiveService, SessionService


@pytest.mark.parametrize("source", ["workspace", "archive"])
@pytest.mark.parametrize("detail", ["full", "shallow"])
async def test_timed_archive_rest_preserves_identity_before_paging_and_elision(
    tmp_path, source, detail
):
    fixture = json.loads(
        (Path(__file__).parents[1] / "fixtures/codex_steering_archive_timeline.json").read_text()
    )
    sid = fixture["session_id"]
    session = Session(
        id=UUID(sid), name="timed-archive", model="model", status=SessionStatus.ARCHIVED
    )
    repository = InMemorySessionRepository()
    await repository.create(session)
    sessions = SessionService(
        repository=repository, pod_manager=MockPodManager(), validate_repos=False
    )
    storage = LocalStorageAdapter(base_dir=str(tmp_path))
    await storage.create_session_workspace(sid, user_id="test", tenant_id="test")
    workspace = Path(storage.resolve_session_workspace_path(sid))
    path = (
        workspace / ".skuld" / f"conversation_{sid}.json"
        if source == "workspace"
        else workspace / ".volundr" / "archive" / "transcript.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    original = json.dumps(fixture).encode()
    path.write_bytes(original)
    archives = SessionArchiveService(sessions, storage, FileSystemArchiveStore())
    app = FastAPI()
    app.include_router(
        create_router(
            sessions,
            archive_service=archives,
            session_participant_service=make_session_participant_service(sessions),
        )
    )

    class Settings:
        local_mounts = LocalMountsConfig()

    app.state.settings = Settings()
    app.state.admin_settings = {}
    expected = project_timeline(fixture["turns"], sid)
    url = f"/api/v1/forge/sessions/{sid}/conversation"
    with TestClient(app) as client:
        response = client.get(url, params={"detail": detail})
        assert response.status_code == 200
        full = response.json()
        assert [t["id"] for t in full["turns"]] == [t["id"] for t in expected]
        assert [t["created_at"] for t in full["turns"]] == [t["created_at"] for t in expected]
        assert full["total_turns"] == 6
        assert full["projection_revision"].endswith(";timeline-1")
        for _ in range(3):
            tail = client.get(
                url, params={"after": 2, "after_id": expected[2]["id"], "detail": detail}
            ).json()
            assert tail["window_offset"] == 3
            assert [t["id"] for t in tail["turns"]] == [t["id"] for t in expected[3:]]
            assert not {t["id"] for t in expected[:3]} & {t["id"] for t in tail["turns"]}
        recent = client.get(url, params={"limit": 3, "detail": detail}).json()
        assert recent["window_offset"] == 3
        assert recent["turns"] == tail["turns"]
        bad = client.get(url, params={"after": 2, "after_id": "old-cumulative-row"}).json()
        assert bad["window_offset"] == -1 and bad["turns"] == []
        result = client.get(f"/api/v1/forge/sessions/{sid}/tool-result/tool-before")
        assert result.status_code == 200
        assert result.json()["content"] == "fixture-only\n"
        assert result.json()["input"] == fixture["turns"][-1]["parts"][0]["input"]
    assert path.read_bytes() == original
