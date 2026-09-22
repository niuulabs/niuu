"""Read/unread is authenticated, reader-scoped, durable and CAS-protected."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from niuu.domain.models import Principal
from tests.conftest import InMemorySessionRepository, MockPodManager
from volundr.adapters.inbound.rest import create_router
from volundr.domain.models import GitSource, Session, SessionStatus
from volundr.domain.services.session import SessionService
from volundr.domain.session_read_state import SessionReadState


@pytest.fixture
def inbox():
    repo = InMemorySessionRepository()
    session = Session(
        id=uuid4(),
        name="inbox",
        model="model",
        status=SessionStatus.STOPPED,
        source=GitSource(repo="https://example.test/repo", branch="main"),
        owner_id="reader-a",
        tenant_id="tenant",
    )
    asyncio.run(repo.create(session))
    repo.read_markers = {
        (session.id, reader): SessionReadState(
            latest_output_seq=20, latest_output_turn_id="final-20"
        )
        for reader in ["reader-a", "reader-b"]
    }
    authz = SimpleNamespace(
        is_allowed=AsyncMock(return_value=True),
        filter_allowed=AsyncMock(side_effect=lambda principal, action, resources: resources),
    )
    broadcaster = SimpleNamespace(publish=AsyncMock())
    service = SessionService(repo, MockPodManager(), authorization=authz, broadcaster=broadcaster)
    app = FastAPI()
    app.state.identity = SimpleNamespace(get_or_provision_user=AsyncMock())
    app.state.settings = SimpleNamespace()
    app.include_router(create_router(service))
    principal = Principal(
        user_id="reader-a", email="reader@example.test", tenant_id="tenant", roles=[]
    )
    with patch(
        "volundr.adapters.inbound.auth.extract_principal", new=AsyncMock(return_value=principal)
    ) as identity:
        with TestClient(app) as client:
            yield client, repo, session, identity, authz, broadcaster, app


def test_read_then_manual_unread_and_stale_read_conflict(inbox):
    client, repo, session, _, _, broadcaster, _ = inbox
    path = f"/api/v1/forge/sessions/{session.id}/read-state"
    assert client.get(path).json()["is_unread"]
    response = client.patch(path, json={"state": "read", "through_seq": 20, "expected_revision": 0})
    assert response.status_code == 200 and not response.json()["is_unread"]
    assert response.json()["revision"] == 1
    response = client.patch(
        path, json={"state": "unread", "through_seq": 20, "expected_revision": 1}
    )
    assert response.status_code == 200 and response.json()["is_unread"]
    assert (
        client.patch(
            path, json={"state": "read", "through_seq": 20, "expected_revision": 1}
        ).status_code
        == 409
    )
    assert client.get(path).json()["manually_unread"]
    assert repo._sessions[session.id] == session  # lifecycle/activity never mutated
    assert repo.read_markers[(session.id, "reader-b")].revision == 0
    event = broadcaster.publish.call_args.args[0]
    assert event.type.value == "session_read_state"
    assert set(event.data) == {"session_id", "owner_id"}  # no private marker in SSE


def test_old_observed_cursor_keeps_new_reply_unread(inbox):
    client, _, session, *_ = inbox
    path = f"/api/v1/forge/sessions/{session.id}/read-state"
    response = client.patch(path, json={"state": "read", "through_seq": 10, "expected_revision": 0})
    assert response.status_code == 200 and response.json()["is_unread"]
    assert response.json()["read_through_seq"] == 10


def test_list_and_detail_include_current_reader_projection(inbox):
    client, repo, session, identity, *_ = inbox
    prefix = "/api/v1/forge/sessions"
    client.patch(
        f"{prefix}/{session.id}/read-state",
        json={"state": "read", "through_seq": 20, "expected_revision": 0},
    )
    assert not client.get(prefix).json()[0]["read_state"]["is_unread"]
    assert not client.get(f"{prefix}/{session.id}").json()["read_state"]["is_unread"]
    identity.return_value = Principal(
        user_id="reader-b", email="b@example.test", tenant_id="tenant", roles=[]
    )
    assert client.get(f"{prefix}/{session.id}/read-state").json()["is_unread"]


@pytest.mark.parametrize("method", ["get", "patch"])
def test_authorization_and_missing_session(inbox, method):
    client, _, session, _, authz, *_ = inbox
    kwargs = (
        {}
        if method == "get"
        else {"json": {"state": "read", "through_seq": 20, "expected_revision": 0}}
    )
    authz.is_allowed.return_value = False
    response = getattr(client, method)(f"/api/v1/forge/sessions/{session.id}/read-state", **kwargs)
    assert response.status_code == 403
    authz.is_allowed.return_value = True
    assert (
        getattr(client, method)(
            f"/api/v1/forge/sessions/{uuid4()}/read-state", **kwargs
        ).status_code
        == 404
    )


def test_identity_required_and_request_cannot_choose_reader(inbox):
    client, repo, session, _, _, _, app = inbox
    path = f"/api/v1/forge/sessions/{session.id}/read-state"
    response = client.patch(
        path,
        json={"state": "read", "through_seq": 20, "expected_revision": 0, "user_id": "reader-b"},
    )
    assert response.status_code == 200
    assert repo.read_markers[(session.id, "reader-b")].is_unread
    app.state.identity = None
    assert client.get(path).status_code == 401
    assert (
        client.patch(
            path, json={"state": "read", "through_seq": 20, "expected_revision": 0}
        ).status_code
        == 401
    )


@pytest.mark.parametrize(
    "body",
    [
        {"state": "read", "through_seq": 99, "expected_revision": 0},
        {"state": "bad", "through_seq": 20, "expected_revision": 0},
        {"state": "read", "through_seq": 20},
    ],
)
def test_invalid_or_future_mutation_is_not_committed(inbox, body):
    client, _, session, *_ = inbox
    path = f"/api/v1/forge/sessions/{session.id}/read-state"
    assert client.patch(path, json=body).status_code == 422
    assert client.get(path).json()["revision"] == 0


def test_hint_failure_does_not_turn_committed_mutation_into_http_error(inbox):
    client, _, session, _, _, broadcaster, _ = inbox
    broadcaster.publish.side_effect = RuntimeError("hint transport unavailable")
    response = client.patch(
        f"/api/v1/forge/sessions/{session.id}/read-state",
        json={"state": "read", "through_seq": 20, "expected_revision": 0},
    )
    assert response.status_code == 200 and not response.json()["is_unread"]
