"""Session listing and the room-scoped session view never leak owner-only detail.

Real Cedar end to end: a participant's session appears in GET /sessions as a
SessionRoomSummary (identity, status, owner, room, project, model, activity and
the proxied chat endpoint), never a full SessionResponse (repo, pod_name, error,
activity_metadata, code_endpoint, source, ...), and
GET /sessions/{id} still 403s for them while GET /sessions/{id}/room works.
"""

from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from identity.adapters.cedar import CedarAuthorizationAdapter
from identity.models import TenantMembership, User
from tests.conftest import (
    InMemorySessionParticipantRepository,
    InMemorySessionRepository,
    MockPodManager,
)
from volundr.adapters.inbound.rest import create_router
from volundr.adapters.outbound.identity import AllowAllIdentityAdapter
from volundr.config import LocalMountsConfig
from volundr.domain.models import GitSource, Session, SessionActivityState
from volundr.domain.projects import SessionCoordination, SessionReference
from volundr.domain.services.session import SessionService
from volundr.domain.services.session_participants import SessionParticipantService
from volundr.domain.session_participants import ParticipantRole

PROJECT_ID = UUID("11111111-2222-3333-4444-555555555555")

# Everything owner/admin-only that a room summary must never carry.
EXCLUDED_FIELDS = (
    "source",
    "code_endpoint",
    "pod_name",
    "error",
    "activity_metadata",
    "external_session_id",
    "cli_session_id",
    "local_path",
    "repo",
    "branch",
    "tenant_id",
)


def _headers(user_id: str, roles: str = "volundr:developer") -> dict:
    return {"x-auth-user-id": user_id, "x-auth-tenant": "acme", "x-auth-roles": roles}


@pytest.fixture
def wired_app():
    session_repository = InMemorySessionRepository()
    session_service = SessionService(
        session_repository, MockPodManager(), authorization=CedarAuthorizationAdapter()
    )
    user_repository = AsyncMock()
    user_repository.get.return_value = User(id="invitee", email="invitee@example.test")
    user_repository.get_memberships.return_value = [
        TenantMembership(user_id="invitee", tenant_id="acme")
    ]
    participant_service = SessionParticipantService(
        InMemorySessionParticipantRepository(), session_service, user_repository
    )

    app = FastAPI()
    app.include_router(
        create_router(session_service, session_participant_service=participant_service)
    )
    app.state.identity = AllowAllIdentityAdapter(user_repository=user_repository)

    class _SettingsStub:
        local_mounts = LocalMountsConfig()

    app.state.settings = _SettingsStub()
    app.state.admin_settings = {}
    return app, session_service, participant_service


async def _owned_and_shared_sessions(session_service, participant_service):
    owned = Session(
        name="mine",
        model="m",
        owner_id="owner",
        tenant_id="acme",
        source=GitSource(repo="https://github.com/acme/repo", branch="main"),
    )
    await session_service._repository.create(owned)
    shared = Session(
        name="shared-room",
        model="m",
        owner_id="owner",
        tenant_id="acme",
        chat_endpoint="ws://10.1.2.3:8080/session",
        code_endpoint="http://10.1.2.3:8080/code",
        pod_name="pod-secret",
        error="boom",
        activity_state=SessionActivityState.AWAITING_INPUT,
        activity_metadata={"tool": "bash"},
        external_session_id="native-1",
        coordination=SessionCoordination(
            project_id=PROJECT_ID,
            role="worker",
            parent=SessionReference(instance_id="elsewhere", session_id=uuid4()),
            objective="private objective",
            labels=["internal"],
        ),
    )
    await session_service._repository.create(shared)
    from niuu.domain.models import Principal

    owner_principal = Principal("owner", "", "acme", ["volundr:developer"])
    await participant_service.invite(
        shared, owner_principal, user_id="invitee", role=ParticipantRole.VIEWER, expires_at=None
    )
    invitee_principal = Principal("invitee", "", "acme", ["volundr:developer"])
    await participant_service.accept(shared, invitee_principal)
    return owned, shared


def test_participant_session_renders_as_room_summary_in_the_list(wired_app):
    app, session_service, participant_service = wired_app
    client = TestClient(app)

    import asyncio

    owned, shared = asyncio.run(_owned_and_shared_sessions(session_service, participant_service))

    response = client.get("/api/v1/forge/sessions", headers=_headers("invitee"))
    assert response.status_code == 200
    body = response.json()
    by_id = {item["id"]: item for item in body}

    assert owned.id.__str__() not in by_id  # invitee doesn't own or participate in it
    summary = by_id[str(shared.id)]
    assert summary["kind"] == "room_summary"
    assert summary["name"] == "shared-room"
    assert summary["room"]["role"] == "viewer"
    assert summary["room"]["participant_status"] == "active"
    # Never the owner-only detail a full SessionResponse would carry.
    for excluded in EXCLUDED_FIELDS:
        assert excluded not in summary
    assert "pod-secret" not in response.text
    assert "10.1.2.3:8080/code" not in response.text


def test_room_summary_carries_what_a_member_needs_to_render_and_attach(wired_app):
    app, session_service, participant_service = wired_app
    client = TestClient(app)

    import asyncio

    _owned, shared = asyncio.run(_owned_and_shared_sessions(session_service, participant_service))

    response = client.get("/api/v1/forge/sessions", headers=_headers("invitee"))
    summary = {item["id"]: item for item in response.json()}[str(shared.id)]

    assert summary["owner_id"] == "owner"
    assert summary["model"] == "m"
    assert summary["chat_endpoint"] == "ws://10.1.2.3:8080/session"
    assert summary["activity_state"] == "awaiting_input"
    assert summary["needs_attention"] is True
    assert summary["last_active"] == shared.created_at.isoformat()
    # Project id and role only: no parent, objective, revision or labels.
    assert summary["coordination"] == {"project_id": str(PROJECT_ID), "role": "worker"}
    assert "private objective" not in response.text
    assert "elsewhere" not in response.text


def test_room_summary_of_a_session_without_a_project_or_endpoint(wired_app):
    app, session_service, participant_service = wired_app
    client = TestClient(app)

    import asyncio

    _owned, shared = asyncio.run(_owned_and_shared_sessions(session_service, participant_service))
    stored = asyncio.run(session_service._repository.get(shared.id))
    stored.coordination = None
    stored.chat_endpoint = None
    stored.activity_state = None
    asyncio.run(session_service._repository.update(stored))

    summary = client.get(f"/api/v1/forge/sessions/{shared.id}/room", headers=_headers("invitee"))

    body = summary.json()
    assert body["coordination"] is None
    assert body["chat_endpoint"] is None
    assert body["activity_state"] is None
    assert body["needs_attention"] is False


def test_owner_sees_their_own_session_as_full_detail(wired_app):
    app, session_service, participant_service = wired_app
    client = TestClient(app)

    import asyncio

    owned, shared = asyncio.run(_owned_and_shared_sessions(session_service, participant_service))

    response = client.get("/api/v1/forge/sessions", headers=_headers("owner"))
    assert response.status_code == 200
    body = response.json()
    by_id = {item["id"]: item for item in body}
    assert "pod_name" in by_id[str(owned.id)]
    assert "pod_name" in by_id[str(shared.id)]


def test_plain_session_get_403s_a_participant(wired_app):
    app, session_service, participant_service = wired_app
    client = TestClient(app)

    import asyncio

    _owned, shared = asyncio.run(_owned_and_shared_sessions(session_service, participant_service))

    response = client.get(f"/api/v1/forge/sessions/{shared.id}", headers=_headers("invitee"))
    assert response.status_code == 403


def test_room_endpoint_works_for_a_participant(wired_app):
    app, session_service, participant_service = wired_app
    client = TestClient(app)

    import asyncio

    _owned, shared = asyncio.run(_owned_and_shared_sessions(session_service, participant_service))

    response = client.get(f"/api/v1/forge/sessions/{shared.id}/room", headers=_headers("invitee"))
    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "room_summary"
    assert body["room"]["role"] == "viewer"
    assert body["chat_endpoint"] == "ws://10.1.2.3:8080/session"
    assert body["coordination"] == {"project_id": str(PROJECT_ID), "role": "worker"}
    for excluded in EXCLUDED_FIELDS:
        assert excluded not in body


def test_room_endpoint_403s_an_unrelated_tenant_developer(wired_app):
    app, session_service, participant_service = wired_app
    client = TestClient(app)

    import asyncio

    _owned, shared = asyncio.run(_owned_and_shared_sessions(session_service, participant_service))

    response = client.get(f"/api/v1/forge/sessions/{shared.id}/room", headers=_headers("stranger"))
    assert response.status_code == 403


def test_room_endpoint_works_for_the_owner_too(wired_app):
    app, session_service, participant_service = wired_app
    client = TestClient(app)

    import asyncio

    _owned, shared = asyncio.run(_owned_and_shared_sessions(session_service, participant_service))

    response = client.get(f"/api/v1/forge/sessions/{shared.id}/room", headers=_headers("owner"))
    assert response.status_code == 200
    assert response.json()["room"]["role"] == "owner"


async def _shared_session_without_a_broker(session_service, participant_service):
    from niuu.domain.models import Principal

    shared = Session(name="shared-history", model="m", owner_id="owner", tenant_id="acme")
    await session_service._repository.create(shared)
    owner_principal = Principal("owner", "", "acme", ["volundr:developer"])
    await participant_service.invite(
        shared, owner_principal, user_id="invitee", role=ParticipantRole.VIEWER, expires_at=None
    )
    await participant_service.accept(
        shared, Principal("invitee", "", "acme", ["volundr:developer"])
    )
    return shared


@pytest.mark.parametrize(
    "user, admitted", [("invitee", True), ("owner", True), ("stranger", False)]
)
def test_conversation_is_read_on_the_room_right(wired_app, user, admitted):
    """A participant reads the conversation of the room they joined; a tenant developer
    who was never invited does not. (This app has no broker or archive behind the
    route, so an admitted caller gets that 503, not the history.)"""
    import asyncio

    app, session_service, participant_service = wired_app
    client = TestClient(app)
    shared = asyncio.run(_shared_session_without_a_broker(session_service, participant_service))

    response = client.get(
        f"/api/v1/forge/sessions/{shared.id}/conversation", headers=_headers(user)
    )

    if admitted:
        assert response.status_code == 503, response.text
        assert response.json()["detail"] == "Session archive service not available"
        return
    assert response.status_code == 403
    assert "Access denied" in response.json()["detail"]


def test_conversation_without_a_caller_is_refused(wired_app):
    import asyncio

    app, session_service, participant_service = wired_app
    client = TestClient(app)
    shared = asyncio.run(_shared_session_without_a_broker(session_service, participant_service))

    app.state.identity = None  # no identity: the request has no principal at all

    response = client.get(f"/api/v1/forge/sessions/{shared.id}/conversation")

    assert response.status_code == 401, response.text
    assert "Authentication is required" in response.json()["detail"]


#: Each route's answer once a caller is admitted. This app has no broker or archive
#: behind the routes, so an admitted caller reaches that answer, never the data.
_TRANSCRIPT_ROUTES = [
    ("/tool-result/tu1", 404, "tool_result not found: tu1"),
    ("/attachments", 503, "Session archive service not available"),
    ("/read-state", 200, None),
    ("/files/presented/pf1", 404, "has no active endpoint"),
]


@pytest.mark.parametrize(("suffix", "code", "detail"), _TRANSCRIPT_ROUTES)
@pytest.mark.parametrize("user", ["invitee", "owner", "stranger"])
def test_transcript_reads_are_open_to_the_room_and_closed_to_strangers(
    wired_app, suffix, code, detail, user
):
    """Tool results, attachments, presented files and the reader's own read state follow
    the conversation: the session's room reads them, a tenant developer who was never
    invited is refused before anything is looked up."""
    import asyncio

    app, session_service, participant_service = wired_app
    client = TestClient(app)
    shared = asyncio.run(_shared_session_without_a_broker(session_service, participant_service))

    response = client.get(f"/api/v1/forge/sessions/{shared.id}{suffix}", headers=_headers(user))

    if user == "stranger":
        assert response.status_code == 403, response.text
        assert response.json()["detail"] in (
            f"Access denied to session {shared.id}",
            "Access denied",
        )
        return
    assert response.status_code == code, response.text
    if detail is not None:
        assert detail in response.json()["detail"]


def test_an_invitee_marks_the_shared_session_read(wired_app):
    import asyncio

    app, session_service, participant_service = wired_app
    client = TestClient(app)
    shared = asyncio.run(_shared_session_without_a_broker(session_service, participant_service))

    response = client.patch(
        f"/api/v1/forge/sessions/{shared.id}/read-state",
        headers=_headers("invitee"),
        json={"state": "read", "expected_revision": 0, "through_seq": 0},
    )

    assert response.status_code == 200, response.text


def test_a_stranger_cannot_mark_the_shared_session_read(wired_app):
    import asyncio

    app, session_service, participant_service = wired_app
    client = TestClient(app)
    shared = asyncio.run(_shared_session_without_a_broker(session_service, participant_service))

    response = client.patch(
        f"/api/v1/forge/sessions/{shared.id}/read-state",
        headers=_headers("stranger"),
        json={"state": "read", "expected_revision": 0, "through_seq": 0},
    )

    assert response.status_code == 403, response.text


def test_a_tool_result_without_a_caller_is_refused(wired_app):
    import asyncio

    app, session_service, participant_service = wired_app
    client = TestClient(app)
    shared = asyncio.run(_shared_session_without_a_broker(session_service, participant_service))

    app.state.identity = None  # no identity: the request has no principal at all

    response = client.get(f"/api/v1/forge/sessions/{shared.id}/tool-result/tu1")

    assert response.status_code == 401, response.text
