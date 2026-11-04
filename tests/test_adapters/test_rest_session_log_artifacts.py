"""POST /sessions/{id}/log records artifact publications from the STORED rows (F1)."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from identity.adapters.identity import EnvoyHeaderAuthenticationAdapter
from tests.conftest import InMemorySessionRepository, MockEventBroadcaster, MockPodManager
from tests.support.artifacts import service
from tests.test_adapters.test_rest_session_log import InMemoryLog
from volundr.adapters.inbound.rest_session_log import create_session_log_router
from volundr.adapters.outbound.memory_artifacts import InMemoryArtifactPublicationRepository
from volundr.domain.artifact_ports import ArtifactStoreUnavailableError
from volundr.domain.artifacts import Origin
from volundr.domain.models import Session
from volundr.domain.services.session import SessionService

HEADERS = {"x-auth-user-id": "owner-a", "x-auth-tenant": "tenant-a"}


class CountingLog(InMemoryLog):
    def __init__(self) -> None:
        super().__init__()
        self.read_back: list[list[int]] = []

    async def read_seqs(self, session_id, seqs):
        self.read_back.append(sorted(seqs))
        return await super().read_seqs(session_id, seqs)


def _app(*, with_artifacts: bool = True):
    log = CountingLog()
    repo = InMemoryArtifactPublicationRepository()
    sessions = InMemorySessionRepository()
    session_service = SessionService(sessions, MockPodManager(), broadcaster=MockEventBroadcaster())
    artifacts = service(repo)
    app = FastAPI()
    app.state.identity = EnvoyHeaderAuthenticationAdapter()
    app.include_router(
        create_session_log_router(
            log,
            session_service=session_service,
            artifact_service=artifacts if with_artifacts else None,
        )
    )
    return TestClient(app), log, repo, artifacts, sessions


def _hook_frame(seq: int, key: str = "Pub1") -> dict:
    url = f"https://claude.ai/artifact/{key}"
    return {
        "seq": seq,
        "kind": "claude_hook",
        "payload": {
            "type": "claude_hook",
            "hook_event_name": "PostToolUse",
            "payload": {
                "hook_event_name": "PostToolUse",
                "tool_name": "Artifact",
                "tool_use_id": f"toolu_{seq}",
                "tool_input": {},
                "tool_response": {"url": url, "version": f"{seq}-v", "title": "Deck"},
            },
        },
    }


def _chatter(seq: int) -> dict:
    return {"seq": seq, "kind": "content_block_delta", "payload": {"type": "delta", "n": seq}}


async def _session(sessions) -> Session:
    return await sessions.create(Session(name="s", owner_id="owner-a", tenant_id="tenant-a"))


async def _rows(repo):
    return await repo.list_after(None, after=0, limit=100)


async def test_append_records_a_live_publication_once():
    client, log, repo, _, sessions = _app()
    session = await _session(sessions)
    url = f"/api/v1/forge/sessions/{session.id}/log"
    batch = {"entries": [_chatter(1), _hook_frame(2)]}

    assert client.post(url, json=batch, headers=HEADERS).status_code == 201
    assert client.post(url, json=batch, headers=HEADERS).status_code == 201  # producer retry

    [row] = await _rows(repo)
    assert (row.session_id, row.session_seq, row.provider_key) == (session.id, 2, "Pub1")
    assert row.origin is Origin.LIVE and row.burst_id is not None
    assert log.read_back == [[2], [2]]  # chatter is never read back


async def test_conflicting_seq_is_never_projected():
    client, log, repo, _, sessions = _app()
    session = await _session(sessions)
    url = f"/api/v1/forge/sessions/{session.id}/log"
    client.post(url, json={"entries": [_chatter(1)]}, headers=HEADERS)

    response = client.post(url, json={"entries": [_hook_frame(1)]}, headers=HEADERS)

    assert response.json()["conflicts"] == [1]
    assert await _rows(repo) == []
    assert log.read_back == []


async def test_store_outage_asks_the_producer_to_retry():
    client, _, repo, artifacts, sessions = _app()
    session = await _session(sessions)
    url = f"/api/v1/forge/sessions/{session.id}/log"
    original = artifacts.project_log_entries

    async def down(*args, **kwargs):
        raise ArtifactStoreUnavailableError("pool closed")

    artifacts.project_log_entries = down
    response = client.post(url, json={"entries": [_hook_frame(3)]}, headers=HEADERS)
    assert response.status_code == 503

    artifacts.project_log_entries = original
    assert client.post(url, json={"entries": [_hook_frame(3)]}, headers=HEADERS).status_code == 201
    assert [r.session_seq for r in await _rows(repo)] == [3]


async def test_projection_defect_fails_the_append_and_the_retry_records():
    """A projection defect is a 500, not an acknowledged append missing its publication."""
    client, log, repo, artifacts, sessions = _app()
    session = await _session(sessions)
    url = f"/api/v1/forge/sessions/{session.id}/log"
    original = artifacts.project_log_entries

    async def broken(*args, **kwargs):
        raise RuntimeError("boom")

    artifacts.project_log_entries = broken
    failing = TestClient(client.app, raise_server_exceptions=False)
    response = failing.post(url, json={"entries": [_hook_frame(4)]}, headers=HEADERS)
    assert response.status_code == 500
    assert await log.latest_seq(session.id) == 4  # the transcript frame is kept
    assert await _rows(repo) == []

    artifacts.project_log_entries = original
    assert client.post(url, json={"entries": [_hook_frame(4)]}, headers=HEADERS).status_code == 201
    assert [r.session_seq for r in await _rows(repo)] == [4]


async def test_without_artifact_service_nothing_is_read_back():
    client, log, _, _, sessions = _app(with_artifacts=False)
    session = await _session(sessions)
    response = client.post(
        f"/api/v1/forge/sessions/{session.id}/log",
        json={"entries": [_hook_frame(1)]},
        headers=HEADERS,
    )
    assert response.status_code == 201
    assert log.read_back == []
