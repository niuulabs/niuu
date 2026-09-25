"""Runtime inspection must respect access, host ownership and loaded process identity."""

import asyncio
from uuid import uuid4

import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ConnectError, Response

from tests.conftest import (
    InMemorySessionRepository,
    MockPodManager,
    make_session_participant_service,
)
from tests.test_niuu.test_rest_volundr import _client, _headers, _instance
from volundr.adapters.inbound.rest import create_router
from volundr.domain.models import Session
from volundr.domain.services.session import SessionService


@pytest.fixture
def runtime():
    session = Session(name="runtime", model="gpt-6-astra", chat_endpoint="ws://broker/session")
    repo = InMemorySessionRepository()
    asyncio.run(repo.create(session))
    app = FastAPI()
    session_service = SessionService(repo, MockPodManager())
    app.include_router(
        create_router(
            session_service,
            runtime_build={
                "revision": "new",
                "source_sha256": "new-hash",
                "build": "release",
                "dirty": False,
            },
            session_participant_service=make_session_participant_service(session_service),
        )
    )
    with TestClient(app) as client:
        yield client, session, repo


@pytest.mark.parametrize(
    "digest,state", [("new-hash", "current"), ("old", "different"), (None, "unknown")]
)
@respx.mock
def test_loaded_identity_not_api_revision(runtime, digest, state):
    client, session, _ = runtime
    health = respx.get("http://broker/health").mock(
        return_value=Response(
            200,
            json={
                "session_id": str(session.id),
                "revision": "loaded",
                "source_sha256": digest,
            },
        )
    )
    result = client.get(f"/api/v1/forge/sessions/{session.id}/runtime-version")
    assert result.status_code == 200
    assert result.json()["state"] == state
    assert result.json()["current"]["revision"] == "loaded"
    assert result.json()["available"]["revision"] == "new"
    assert health.call_count == 1


@respx.mock
def test_unknown_probe_is_not_stopped(runtime):
    client, session, _ = runtime
    respx.get("http://broker/health").mock(side_effect=ConnectError("offline"))
    result = client.get(f"/api/v1/forge/sessions/{session.id}/runtime-version")
    assert result.json()["state"] == "unavailable"
    assert result.json()["current"] is None


@respx.mock
def test_reject_wrong_broker(runtime):
    client, session, _ = runtime
    respx.get("http://broker/health").mock(
        return_value=Response(200, json={"session_id": str(uuid4())})
    )
    assert client.get(f"/api/v1/forge/sessions/{session.id}/runtime-version").status_code == 502


def test_missing_and_stopped(runtime):
    client, session, repo = runtime
    assert client.get(f"/api/v1/forge/sessions/{uuid4()}/runtime-version").status_code == 404
    repo._sessions[session.id] = session.model_copy(update={"chat_endpoint": None})
    assert (
        client.get(f"/api/v1/forge/sessions/{session.id}/runtime-version").json()["state"]
        == "not_running"
    )


@respx.mock
def test_selected_host_returns_its_available_build():
    client = _client([_instance("remote", base_url="http://remote")])
    respx.get("http://remote/api/v1/forge/sessions/s").mock(
        return_value=Response(200, json={"id": "s"})
    )
    respx.get("http://remote/api/v1/forge/sessions/s/runtime-version").mock(
        return_value=Response(200, json={"state": "different", "available": {"revision": "remote"}})
    )
    result = client.get(
        "/api/v1/forge/sessions/s/runtime-version?instance_id=remote", headers=_headers()
    )
    assert result.status_code == 200
    assert result.json()["available"]["revision"] == "remote"


@respx.mock
def test_access_checked_before_probe(runtime):
    from unittest.mock import AsyncMock, patch

    from volundr.domain.services.session import SessionAccessDeniedError

    client, session, _ = runtime
    with patch(
        "volundr.domain.services.forge.ForgeService.ensure_access",
        new=AsyncMock(side_effect=SessionAccessDeniedError(session.id, "other")),
    ):
        assert client.get(f"/api/v1/forge/sessions/{session.id}/runtime-version").status_code == 403
    assert not respx.calls
