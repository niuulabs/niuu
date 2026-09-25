"""HTTP surface for invite/accept/revoke/list, and one real-Cedar integration case."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from niuu.domain.models import Principal
from volundr.adapters.inbound.rest_session_participants import create_session_participants_router
from volundr.domain.services.session import SessionAccessDeniedError
from volundr.domain.session_participants import (
    CrossTenantInviteError,
    InviteeNotFoundError,
    OwnerInviteError,
    ParticipantAlreadyActiveError,
    ParticipantNotFoundError,
    ParticipantRole,
    ParticipantStateError,
    ParticipantStatus,
    SelfInviteError,
    SessionParticipant,
)


def _participant(**overrides) -> SessionParticipant:
    now = datetime.now(UTC)
    fields = {
        "session_id": uuid4(),
        "user_id": "invitee",
        "tenant_id": "acme",
        "role": ParticipantRole.OBSERVER,
        "status": ParticipantStatus.INVITED,
        "invited_by": "owner",
        "created_at": now,
        "updated_at": now,
        "expires_at": None,
    }
    fields.update(overrides)
    return SessionParticipant(**fields)


@pytest.fixture
def participants_api():
    service, session_service = AsyncMock(), AsyncMock()
    app = FastAPI()
    app.include_router(create_session_participants_router(service, session_service))
    sid = uuid4()
    principal = Principal(user_id="owner", email="", tenant_id="acme", roles=["volundr:developer"])
    with patch(
        "volundr.adapters.inbound.rest_session_participants.extract_principal",
        new=AsyncMock(return_value=principal),
    ):
        path = f"/api/v1/forge/sessions/{sid}/participants"
        yield TestClient(app), service, session_service, path


def test_missing_session_is_404_before_any_service_call(participants_api):
    client, service, session_service, path = participants_api
    session_service.get_session.return_value = None
    response = client.post(path, json={"user_id": "invitee", "role": "observer"})
    assert response.status_code == 404
    service.invite.assert_not_awaited()


def test_invite_returns_the_created_grant(participants_api):
    client, service, session_service, path = participants_api
    session_service.get_session.return_value = object()
    service.invite.return_value = _participant()
    response = client.post(path, json={"user_id": "invitee", "role": "observer"})
    assert response.status_code == 201
    body = response.json()
    assert body["user_id"] == "invitee" and body["status"] == "invited"
    assert service.invite.await_args.kwargs["user_id"] == "invitee"
    assert service.invite.await_args.kwargs["role"] == ParticipantRole.OBSERVER


@pytest.fixture
def participants_api_on_kubernetes():
    """Same as participants_api, but a runtime_backend that can't honor grants."""
    service, session_service = AsyncMock(), AsyncMock()
    app = FastAPI()
    app.include_router(
        create_session_participants_router(service, session_service, runtime_backend="kubernetes")
    )
    sid = uuid4()
    principal = Principal(user_id="owner", email="", tenant_id="acme", roles=["volundr:developer"])
    with patch(
        "volundr.adapters.inbound.rest_session_participants.extract_principal",
        new=AsyncMock(return_value=principal),
    ):
        path = f"/api/v1/forge/sessions/{sid}/participants"
        yield TestClient(app), service, session_service, path


def test_invite_on_kubernetes_backend_is_409_before_any_service_call(
    participants_api_on_kubernetes,
):
    """A grant on a Gateway-routed backend could never be attached to (Envoy's
    ext_authz only ever authorizes owner/admin) — refuse it up front rather
    than let the invite succeed and every attach silently 403 afterward."""
    client, service, session_service, path = participants_api_on_kubernetes
    session_service.get_session.return_value = object()
    response = client.post(path, json={"user_id": "invitee", "role": "observer"})
    assert response.status_code == 409
    assert "process" in response.json()["detail"]
    service.invite.assert_not_awaited()


def test_invite_denied_for_non_owner_is_403(participants_api):
    client, service, session_service, path = participants_api
    session_service.get_session.return_value = object()
    service.invite.side_effect = SessionAccessDeniedError(uuid4(), "owner")
    response = client.post(path, json={"user_id": "invitee", "role": "observer"})
    assert response.status_code == 403


def test_invite_of_nonexistent_user_is_generic_422_not_404(participants_api):
    """No enumeration: "doesn't exist" must read identically to "wrong tenant"."""
    client, service, session_service, path = participants_api
    session_service.get_session.return_value = object()
    service.invite.side_effect = InviteeNotFoundError("no such user")
    response = client.post(path, json={"user_id": "ghost", "role": "observer"})
    assert response.status_code == 422
    assert response.json()["detail"] == "Cannot invite this user"


def test_invite_of_cross_tenant_user_is_generic_422(participants_api):
    client, service, session_service, path = participants_api
    session_service.get_session.return_value = object()
    service.invite.side_effect = CrossTenantInviteError("wrong tenant")
    response = client.post(path, json={"user_id": "invitee", "role": "observer"})
    assert response.status_code == 422
    assert response.json()["detail"] == "Cannot invite this user"


def test_invite_self_is_generic_422(participants_api):
    client, service, session_service, path = participants_api
    session_service.get_session.return_value = object()
    service.invite.side_effect = SelfInviteError("cannot invite yourself")
    response = client.post(path, json={"user_id": "owner", "role": "observer"})
    assert response.status_code == 422
    assert response.json()["detail"] == "Cannot invite this user"


def test_invite_owner_is_generic_422(participants_api):
    client, service, session_service, path = participants_api
    session_service.get_session.return_value = object()
    service.invite.side_effect = OwnerInviteError("owner already has full access")
    response = client.post(path, json={"user_id": "owner", "role": "observer"})
    assert response.status_code == 422
    assert response.json()["detail"] == "Cannot invite this user"


def test_reinviting_an_active_participant_is_409(participants_api):
    client, service, session_service, path = participants_api
    session_service.get_session.return_value = object()
    service.invite.side_effect = ParticipantAlreadyActiveError("already active")
    response = client.post(path, json={"user_id": "invitee", "role": "approver"})
    assert response.status_code == 409


def test_invite_rejects_a_timezone_naive_expiry(participants_api):
    client, _service, session_service, path = participants_api
    session_service.get_session.return_value = object()
    response = client.post(
        path,
        json={"user_id": "invitee", "role": "observer", "expires_at": "2030-01-01T00:00:00"},
    )
    assert response.status_code == 422


def test_invite_accepts_a_timezone_aware_expiry(participants_api):
    client, service, session_service, path = participants_api
    session_service.get_session.return_value = object()
    service.invite.return_value = _participant()
    response = client.post(
        path,
        json={
            "user_id": "invitee",
            "role": "observer",
            "expires_at": "2030-01-01T00:00:00+00:00",
        },
    )
    assert response.status_code == 201


def test_accept_activates_the_grant(participants_api):
    client, service, session_service, path = participants_api
    session_service.get_session.return_value = object()
    service.accept.return_value = _participant(status=ParticipantStatus.ACTIVE)
    response = client.post(path + "/accept")
    assert response.status_code == 200
    assert response.json()["status"] == "active"


def test_accept_with_no_invitation_is_404(participants_api):
    client, service, session_service, path = participants_api
    session_service.get_session.return_value = object()
    service.accept.side_effect = ParticipantNotFoundError("no grant")
    assert client.post(path + "/accept").status_code == 404


def test_accept_expired_invitation_is_409(participants_api):
    client, service, session_service, path = participants_api
    session_service.get_session.return_value = object()
    service.accept.side_effect = ParticipantStateError("expired")
    assert client.post(path + "/accept").status_code == 409


def test_revoke_succeeds_and_returns_no_content(participants_api):
    client, service, session_service, path = participants_api
    session_service.get_session.return_value = object()
    response = client.delete(path + "/invitee")
    assert response.status_code == 204
    assert service.revoke.await_args.args[-1] == "invitee"


def test_revoke_denied_is_403(participants_api):
    client, service, session_service, path = participants_api
    session_service.get_session.return_value = object()
    service.revoke.side_effect = SessionAccessDeniedError(uuid4(), "outsider")
    assert client.delete(path + "/invitee").status_code == 403


def test_revoke_of_nonexistent_grant_is_404(participants_api):
    client, service, session_service, path = participants_api
    session_service.get_session.return_value = object()
    service.revoke.side_effect = ParticipantNotFoundError("no grant")
    assert client.delete(path + "/nobody").status_code == 404


def test_list_returns_every_grant(participants_api):
    client, service, session_service, path = participants_api
    session_service.get_session.return_value = object()
    service.list_participants.return_value = [_participant(), _participant(user_id="second")]
    response = client.get(path)
    assert response.status_code == 200
    assert [p["user_id"] for p in response.json()] == ["invitee", "second"]


def test_list_denied_is_403(participants_api):
    client, service, session_service, path = participants_api
    session_service.get_session.return_value = object()
    service.list_participants.side_effect = SessionAccessDeniedError(uuid4(), "stranger")
    assert client.get(path).status_code == 403


def test_actual_cedar_policy_enforces_invite_and_accept_end_to_end():
    """A non-owner cannot invite; the invitee can accept their own invitation."""
    from identity.adapters.cedar import CedarAuthorizationAdapter
    from tests.conftest import (
        InMemorySessionParticipantRepository,
        InMemorySessionRepository,
        MockPodManager,
    )
    from volundr.domain.models import Session
    from volundr.domain.services.session import SessionService
    from volundr.domain.services.session_participants import SessionParticipantService

    async def scenario():
        session_repository = InMemorySessionRepository()
        session_service = SessionService(
            session_repository, MockPodManager(), authorization=CedarAuthorizationAdapter()
        )
        session = Session(name="s", model="m", owner_id="owner", tenant_id="acme")
        await session_repository.create(session)
        user_repository = AsyncMock()
        from identity.models import TenantMembership, User

        user_repository.get.return_value = User(id="invitee", email="invitee@example.test")
        user_repository.get_memberships.return_value = [
            TenantMembership(user_id="invitee", tenant_id="acme")
        ]
        service = SessionParticipantService(
            InMemorySessionParticipantRepository(), session_service, user_repository
        )
        app = FastAPI()
        app.include_router(create_session_participants_router(service, session_service))
        client = TestClient(app)

        owner = Principal(user_id="owner", email="", tenant_id="acme", roles=["volundr:developer"])
        outsider = Principal(
            user_id="outsider", email="", tenant_id="acme", roles=["volundr:developer"]
        )
        invitee = Principal(
            user_id="invitee", email="", tenant_id="acme", roles=["volundr:developer"]
        )
        path = f"/api/v1/forge/sessions/{session.id}/participants"

        with patch(
            "volundr.adapters.inbound.rest_session_participants.extract_principal",
            new=AsyncMock(return_value=outsider),
        ):
            denied = client.post(path, json={"user_id": "invitee", "role": "observer"})
        assert denied.status_code == 403

        with patch(
            "volundr.adapters.inbound.rest_session_participants.extract_principal",
            new=AsyncMock(return_value=owner),
        ):
            invited = client.post(path, json={"user_id": "invitee", "role": "observer"})
        assert invited.status_code == 201

        with patch(
            "volundr.adapters.inbound.rest_session_participants.extract_principal",
            new=AsyncMock(return_value=invitee),
        ):
            accepted = client.post(path + "/accept")
        assert accepted.status_code == 200 and accepted.json()["status"] == "active"

    import asyncio

    asyncio.run(scenario())
