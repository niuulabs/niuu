"""GET /sessions?changed_since: only written sessions, the whole membership, a cursor."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from identity.adapters.cedar import CedarAuthorizationAdapter
from identity.models import TenantMembership, User
from niuu.domain.models import Principal
from tests.conftest import (
    InMemorySessionParticipantRepository,
    InMemorySessionRepository,
    MockPodManager,
)
from volundr.adapters.inbound.rest import create_router
from volundr.adapters.outbound.identity import AllowAllIdentityAdapter
from volundr.config import LocalMountsConfig
from volundr.domain.models import Session, SessionStatus
from volundr.domain.services.session import SessionService
from volundr.domain.services.session_participants import SessionParticipantService
from volundr.domain.session_participants import ParticipantRole

OWNER = {"x-auth-user-id": "owner", "x-auth-tenant": "acme", "x-auth-roles": "volundr:developer"}
INVITEE = {**OWNER, "x-auth-user-id": "invitee"}


@pytest.fixture
def wired():
    repository = InMemorySessionRepository()
    session_service = SessionService(
        repository, MockPodManager(), authorization=CedarAuthorizationAdapter()
    )
    user_repository = AsyncMock()
    user_repository.get.return_value = User(id="invitee", email="invitee@example.test")
    user_repository.get_memberships.return_value = [
        TenantMembership(user_id="invitee", tenant_id="acme")
    ]
    participants = SessionParticipantService(
        InMemorySessionParticipantRepository(), session_service, user_repository
    )
    app = FastAPI()
    app.include_router(create_router(session_service, session_participant_service=participants))
    app.state.identity = AllowAllIdentityAdapter(user_repository=user_repository)

    class _SettingsStub:
        local_mounts = LocalMountsConfig()

    app.state.settings = _SettingsStub()
    app.state.admin_settings = {}
    return TestClient(app), repository, participants


def _create(repository, name, **fields) -> Session:
    session = Session(name=name, model="m", owner_id="owner", tenant_id="acme", **fields)
    asyncio.run(repository.create(session))
    return session


def test_first_read_carries_every_session_and_a_cursor(wired):
    client, repository, _ = wired
    a = _create(repository, "a")
    b = _create(repository, "b")
    repository.change_cursor = 41

    body = client.get("/api/v1/forge/sessions?changed_since=0", headers=OWNER).json()

    assert body["cursor"] == "41"
    assert set(body["ids"]) == {str(a.id), str(b.id)}
    assert {item["id"] for item in body["sessions"]} == {str(a.id), str(b.id)}


def test_a_later_read_sends_only_written_sessions_but_names_all(wired):
    client, repository, _ = wired
    quiet = _create(repository, "quiet")
    busy = _create(repository, "busy")
    repository.changed_ids = {busy.id}

    body = client.get("/api/v1/forge/sessions?changed_since=41", headers=OWNER).json()

    assert set(body["ids"]) == {str(quiet.id), str(busy.id)}
    assert [item["id"] for item in body["sessions"]] == [str(busy.id)]


def test_changed_ids_outside_the_listing_are_never_sent(wired):
    client, repository, _ = wired
    live = _create(repository, "live")
    archived = _create(repository, "archived", status=SessionStatus.ARCHIVED)
    other_tenant = Session(name="theirs", model="m", owner_id="x", tenant_id="other")
    asyncio.run(repository.create(other_tenant))
    repository.changed_ids = {live.id, archived.id, other_tenant.id}

    body = client.get("/api/v1/forge/sessions?changed_since=7", headers=OWNER).json()
    assert body["ids"] == [str(live.id)]
    assert [item["id"] for item in body["sessions"]] == [str(live.id)]

    body = client.get(
        "/api/v1/forge/sessions?status=archived&changed_since=7", headers=OWNER
    ).json()
    assert body["ids"] == [str(archived.id)]
    assert [item["id"] for item in body["sessions"]] == [str(archived.id)]


def test_room_summaries_ride_along_every_read(wired):
    client, repository, participants = wired
    shared = _create(repository, "shared-room")
    owner = Principal("owner", "", "acme", ["volundr:developer"])
    invitee = Principal("invitee", "", "acme", ["volundr:developer"])

    async def share():
        await participants.invite(
            shared, owner, user_id="invitee", role=ParticipantRole.VIEWER, expires_at=None
        )
        await participants.accept(shared, invitee)

    asyncio.run(share())
    repository.changed_ids = set()

    body = client.get("/api/v1/forge/sessions?changed_since=9", headers=INVITEE).json()

    assert body["ids"] == [str(shared.id)]
    assert [(item["id"], item["kind"]) for item in body["sessions"]] == [
        (str(shared.id), "room_summary")
    ]


@pytest.mark.parametrize("extra", ["limit=5", "fields=summary"])
def test_changed_since_refuses_a_partial_listing(wired, extra):
    client, _, _ = wired
    response = client.get(f"/api/v1/forge/sessions?changed_since=0&{extra}", headers=OWNER)
    assert response.status_code == 400
    assert "changed_since" in response.json()["detail"]


def test_a_negative_cursor_is_rejected(wired):
    client, _, _ = wired
    assert client.get("/api/v1/forge/sessions?changed_since=-1", headers=OWNER).status_code == 422
