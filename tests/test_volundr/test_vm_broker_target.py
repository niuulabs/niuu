"""Forge reaches a VM session's broker where its session proxy does.

A VM session's chat endpoint is Forge's own public ``/s/{id}/session`` route. Forge's
server-side broker calls (conversation, logs, gates, diff, ...) must dial the target
``VmPodManager.broker_target`` names instead of hairpinning out through the ingress,
which left a running VM session's conversation at 503 "No accessible workspace path".
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from niuu.domain.models import Principal
from niuu.ports.session_proxy import SessionProxyTarget
from tests.conftest import (
    InMemorySessionRepository,
    MockPodManager,
    make_session_participant_service,
)
from tests.test_domain.test_session_archive_service import InMemorySessionEventLog
from volundr.adapters.inbound.rest import create_router
from volundr.adapters.outbound.archive_store import FileSystemArchiveStore
from volundr.config import LocalMountsConfig
from volundr.domain.models import Session, SessionLogEntry, SessionStatus
from volundr.domain.services import SessionArchiveService, SessionService
from volundr.domain.session_broker import broker_target_request

_CONV_PATH = "/api/v1/forge/sessions/{sid}/conversation"
_LOGS_PATH = "/api/v1/forge/sessions/{sid}/logs"
_TUNNEL = SessionProxyTarget("http://skuld.internal", "127.0.0.1", 40123)


class _VmLikePodManager(MockPodManager):
    """A pod manager whose broker lives behind Forge's session proxy."""

    def __init__(self, target: SessionProxyTarget | Exception) -> None:
        super().__init__()
        self._target = target

    async def broker_target(self, session: Session) -> SessionProxyTarget:
        if isinstance(self._target, Exception):
            raise self._target
        return self._target


class _NoWorkspaceStorage:
    def resolve_session_workspace_path(self, _session_id: str) -> str | None:
        return None

    async def get_workspace_by_session(self, _session_id: str):
        return None


def _client(pod_manager: MockPodManager, repository, event_log=None) -> TestClient:
    session_service = SessionService(
        repository=repository, pod_manager=pod_manager, validate_repos=False
    )
    archive_service = SessionArchiveService(
        session_service,
        _NoWorkspaceStorage(),
        FileSystemArchiveStore(),
        event_log_repository=event_log or InMemorySessionEventLog(),
    )
    app = FastAPI()
    app.include_router(
        create_router(
            session_service,
            archive_service=archive_service,
            session_participant_service=make_session_participant_service(session_service),
        )
    )

    class _SettingsStub:
        local_mounts = LocalMountsConfig()

    app.state.settings = _SettingsStub()
    app.state.admin_settings = {}
    return TestClient(app)


async def _running_vm_session(
    repository: InMemorySessionRepository, *, owner_id: str | None = None
) -> Session:
    session = Session(name="vm", model="m", status=SessionStatus.STOPPED, owner_id=owner_id)
    running = session.with_endpoints(
        f"wss://forge-vm.example.test/s/{session.id}/session",
        f"https://forge-vm.example.test/s/{session.id}/session",
    ).with_status(SessionStatus.RUNNING)
    await repository.create(running)
    return running


def _patch_broker(monkeypatch: pytest.MonkeyPatch, body: dict) -> AsyncMock:
    response = MagicMock(spec=httpx.Response)
    response.status_code = 200
    response.json.return_value = body
    response.raise_for_status.return_value = None
    client = AsyncMock()
    client.get.return_value = response
    client_cls = MagicMock()
    client_cls.return_value.__aenter__.return_value = client
    monkeypatch.setattr("volundr.adapters.inbound.rest.httpx.AsyncClient", client_cls)
    return client


_LIVE = {
    "turns": [
        {"id": "u1", "role": "user", "content": "hello"},
        {"id": "a1", "role": "assistant", "content": "hi"},
    ],
    "is_active": True,
    "last_activity": "typing",
}


def test_broker_target_request_dials_the_connect_address_with_the_service_host():
    url, headers = broker_target_request(_TUNNEL, "api", "conversation", "history")

    assert url == "http://127.0.0.1:40123/api/conversation/history"
    assert headers == {"Host": "skuld.internal"}


def test_broker_target_request_quotes_segments_and_keeps_the_service_path():
    target = SessionProxyTarget("https://svc.example/base", "::1", 8443, connect_secure=True)

    url, headers = broker_target_request(target, "api", "help", "requests", "a/b", "answer")

    assert url == "https://[::1]:8443/base/api/help/requests/a%2Fb/answer"
    assert headers == {"Host": "svc.example"}


async def test_running_vm_conversation_is_read_from_the_broker_target(monkeypatch):
    repository = InMemorySessionRepository()
    session = await _running_vm_session(repository)
    broker = _patch_broker(monkeypatch, _LIVE)

    resp = _client(_VmLikePodManager(_TUNNEL), repository).get(_CONV_PATH.format(sid=session.id))

    assert resp.status_code == 200
    assert [turn["id"] for turn in resp.json()["turns"]] == ["u1", "a1"]
    call = broker.get.await_args
    assert call.args[0] == "http://127.0.0.1:40123/api/conversation/history"
    assert call.kwargs["headers"]["Host"] == "skuld.internal"


async def test_other_sessions_are_still_read_at_their_chat_endpoint(monkeypatch):
    repository = InMemorySessionRepository()
    session = await _running_vm_session(repository)
    broker = _patch_broker(monkeypatch, _LIVE)

    resp = _client(MockPodManager(), repository).get(_CONV_PATH.format(sid=session.id))

    assert resp.status_code == 200
    call = broker.get.await_args
    assert call.args[0] == (
        f"https://forge-vm.example.test/s/{session.id}/api/conversation/history"
    )
    assert "Host" not in call.kwargs["headers"]


async def test_vm_without_a_ready_broker_serves_the_durable_log(monkeypatch):
    """A broker that cannot be reached is the existing unreachable-broker case: the
    durable rebuild serves the transcript, never a call to Forge's own public URL."""
    repository = InMemorySessionRepository()
    session = await _running_vm_session(repository)
    broker = _patch_broker(monkeypatch, _LIVE)
    now = datetime.now(UTC)
    event_log = InMemorySessionEventLog(
        [
            SessionLogEntry(
                session_id=session.id,
                seq=1,
                kind="conversation.turn",
                payload={"turn": {"id": "d1", "role": "user", "content": "from the log"}},
                ts=now,
                role="user",
            )
        ]
    )
    pod_manager = _VmLikePodManager(ValueError(f"Session {session.id} has no ready VM broker"))

    resp = _client(pod_manager, repository, event_log).get(_CONV_PATH.format(sid=session.id))

    assert resp.status_code == 200
    assert [turn["content"] for turn in resp.json()["turns"]] == ["from the log"]
    broker.get.assert_not_awaited()


async def test_vm_logs_without_a_ready_broker_are_not_found(monkeypatch):
    repository = InMemorySessionRepository()
    session = await _running_vm_session(repository)
    broker = _patch_broker(monkeypatch, {"lines": []})
    pod_manager = _VmLikePodManager(ValueError("no ready VM broker"))

    resp = _client(pod_manager, repository).get(_LOGS_PATH.format(sid=session.id))

    assert resp.status_code == 404
    assert "no active endpoint" in resp.json()["detail"]
    broker.get.assert_not_awaited()


async def test_vm_logs_are_read_from_the_broker_target(monkeypatch):
    repository = InMemorySessionRepository()
    session = await _running_vm_session(repository)
    broker = _patch_broker(monkeypatch, {"lines": [], "total": 0})

    resp = _client(_VmLikePodManager(_TUNNEL), repository).get(_LOGS_PATH.format(sid=session.id))

    assert resp.status_code == 200
    call = broker.get.await_args
    assert call.args[0] == "http://127.0.0.1:40123/api/logs"
    assert call.kwargs["headers"]["Host"] == "skuld.internal"


_OWNER = Principal(user_id="owner-1", email="o@example.test", tenant_id="default", roles=[])


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        (
            "post",
            "/api/v1/forge/sessions/{sid}/workflow/gates/g1/resolve",
            {"decision": "approved", "notes": "", "source": "human"},
        ),
        (
            "post",
            "/api/v1/forge/sessions/{sid}/help/requests/help-1/answer",
            {"answer": "yes", "source": "a2a"},
        ),
        ("get", "/api/v1/forge/chronicles/{sid}/diff?file=README.md", None),
    ],
)
async def test_vm_broker_writes_without_a_ready_broker_are_not_found(
    monkeypatch, method, path, body
):
    repository = InMemorySessionRepository()
    session = await _running_vm_session(repository, owner_id=_OWNER.user_id)
    monkeypatch.setattr(
        "volundr.adapters.inbound.rest.extract_principal", AsyncMock(return_value=_OWNER)
    )
    broker = _patch_broker(monkeypatch, {})
    client = _client(_VmLikePodManager(ValueError("no ready VM broker")), repository)

    kwargs = {"json": body} if body is not None else {}
    resp = getattr(client, method)(path.format(sid=session.id), **kwargs)

    assert resp.status_code == 404, resp.text
    assert "no active endpoint" in resp.json()["detail"]
    broker.get.assert_not_awaited()
    broker.post.assert_not_awaited()
