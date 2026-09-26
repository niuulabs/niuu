"""HTTP surface for invite/accept/revoke/list, and one real-Cedar integration case."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from niuu.domain.models import Principal
from niuu.ports.identity import HeaderAuthenticationPort, IdentityPort, InvalidTokenError
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


@pytest.fixture
def participants_api_on_kubernetes_remote():
    """Kubernetes backend, but the deployment opted into room_role_source: remote."""
    service, session_service = AsyncMock(), AsyncMock()
    app = FastAPI()
    app.include_router(
        create_session_participants_router(
            service, session_service, runtime_backend="kubernetes", room_role_source="remote"
        )
    )
    sid = uuid4()
    principal = Principal(user_id="owner", email="", tenant_id="acme", roles=["volundr:developer"])
    with patch(
        "volundr.adapters.inbound.rest_session_participants.extract_principal",
        new=AsyncMock(return_value=principal),
    ):
        path = f"/api/v1/forge/sessions/{sid}/participants"
        yield TestClient(app), service, session_service, path


def test_invite_on_kubernetes_remote_backend_is_no_longer_409(
    participants_api_on_kubernetes_remote,
):
    """room_role_source: remote lifts the 409 for a remote-capable backend."""
    client, service, session_service, path = participants_api_on_kubernetes_remote
    session_service.get_session.return_value = object()
    service.invite.return_value = _participant()
    response = client.post(path, json={"user_id": "invitee", "role": "observer"})
    assert response.status_code == 201
    service.invite.assert_awaited_once()


def test_invite_on_kubernetes_backend_409_names_the_remote_remedy(
    participants_api_on_kubernetes,
):
    """The default ("deployment") still 409s, and now names BOTH remedies:
    switch to the process backend, or opt into pod_manager.room_role_source:
    remote for this backend."""
    client, service, session_service, path = participants_api_on_kubernetes
    session_service.get_session.return_value = object()
    response = client.post(path, json={"user_id": "invitee", "role": "observer"})
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "process" in detail
    assert "room_role_source: remote" in detail
    service.invite.assert_not_awaited()


def test_docker_backend_stays_409_even_with_room_role_source_remote():
    """docker is deliberately excluded from REMOTE_CAPABLE_RUNTIME_BACKENDS —
    it is not routed through the session proxy and has no remote-adapter path."""
    service, session_service = AsyncMock(), AsyncMock()
    app = FastAPI()
    app.include_router(
        create_session_participants_router(
            service, session_service, runtime_backend="docker", room_role_source="remote"
        )
    )
    sid = uuid4()
    principal = Principal(user_id="owner", email="", tenant_id="acme", roles=["volundr:developer"])
    with patch(
        "volundr.adapters.inbound.rest_session_participants.extract_principal",
        new=AsyncMock(return_value=principal),
    ):
        session_service.get_session.return_value = object()
        response = TestClient(app).post(
            f"/api/v1/forge/sessions/{sid}/participants",
            json={"user_id": "invitee", "role": "observer"},
        )
    assert response.status_code == 409


@pytest.mark.parametrize("backend", ["openshell", "vm"])
def test_openshell_and_vm_stay_409_even_with_room_role_source_remote(backend):
    """Neither backend mounts a projected workload-identity token today
    (WorkloadIdentityContributor skips them), so the pod would have no
    credential to call the remote role endpoint with — REMOTE_CAPABLE_RUNTIME_BACKENDS
    is limited to "kubernetes" until that changes."""
    service, session_service = AsyncMock(), AsyncMock()
    app = FastAPI()
    app.include_router(
        create_session_participants_router(
            service, session_service, runtime_backend=backend, room_role_source="remote"
        )
    )
    sid = uuid4()
    principal = Principal(user_id="owner", email="", tenant_id="acme", roles=["volundr:developer"])
    with patch(
        "volundr.adapters.inbound.rest_session_participants.extract_principal",
        new=AsyncMock(return_value=principal),
    ):
        session_service.get_session.return_value = object()
        response = TestClient(app).post(
            f"/api/v1/forge/sessions/{sid}/participants",
            json={"user_id": "invitee", "role": "observer"},
        )
    assert response.status_code == 409


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


# --- GET .../participants/role: what skuld.room_role_remote.RemoteAuthorizationAdapter calls ---
#
# Only registered when room_role_source == "remote" (see
# test_route_absent_unless_room_role_source_is_remote), and gated by a
# session-scoped, forge:session:room-role-scoped workload credential —
# a much stricter bar than every other route on this router, which relies
# on extract_principal's ordinary Cedar checks.

_ROOM_ROLE_SIGNING_KEY = "test-only-signing-key-32-bytes-long!"
_WRONG_SIGNING_KEY = "a-different-test-signing-key-32-bytes!!"


def _scoped_workload_token(
    session_id,
    *,
    scopes=("forge:session:room-role",),
    workload_sub=None,
    signing_key=_ROOM_ROLE_SIGNING_KEY,
    **overrides,
):
    import time

    import jwt

    from niuu.domain.services.token_scope import VALKYRIE_BUILD_TOKEN_USE

    now = int(time.time())
    claims = {
        "sub": "niuu-workload",
        "iat": now,
        "exp": now + 600,
        "token_use": VALKYRIE_BUILD_TOKEN_USE,
        "scopes": list(scopes),
        "workload_sub": (
            workload_sub
            if workload_sub is not None
            else f"system:serviceaccount:volundr-sessions:openbao-session-{session_id}"
        ),
    }
    claims.update(overrides)
    return jwt.encode(claims, signing_key, algorithm="HS256")


class _VerifyingIdentity(IdentityPort):
    """Stands in for whatever REAL identity adapter Forge is configured with
    (JWKS-verifying, PAT-validating, ...) — the one thing every one of them
    has in common is that an unsigned or wrongly-signed bearer token is
    rejected. Deliberately an ``IdentityPort``, not a
    ``HeaderAuthenticationPort``: ``extract_principal`` then goes through
    its token-based branch (``validate_token`` on the Authorization header —
    the mandatory verification this endpoint now requires), while
    ``_resolve_target_principal``'s SEPARATE ``isinstance(identity,
    HeaderAuthenticationPort)`` check stays False, leaving the
    unmapped-roles-passthrough behavior the non-mapping tests below assert
    on unaffected — exactly like a real deployment's identity adapter, which
    does not double as the x-auth-* role-mapping port unless it also
    implements that port (see test_target_roles_are_mapped_through_the
    _identity_adapter for that separate, explicit case).
    """

    async def validate_token(self, raw_token: str) -> Principal:
        token = raw_token[7:] if raw_token.lower().startswith("bearer ") else raw_token
        import jwt

        try:
            jwt.decode(token, _ROOM_ROLE_SIGNING_KEY, algorithms=["HS256"])
        except jwt.InvalidTokenError as exc:
            raise InvalidTokenError(str(exc)) from exc
        return Principal(user_id="niuu-workload", email="", tenant_id="", roles=[])

    async def get_or_provision_user(self, principal: Principal):
        raise NotImplementedError("not exercised by these tests")


@pytest.fixture
def remote_participants_api():
    """runtime_backend/room_role_source combo that registers the role endpoint."""
    service, session_service = AsyncMock(), AsyncMock()
    app = FastAPI()
    app.state.identity = _VerifyingIdentity()
    app.include_router(
        create_session_participants_router(
            service,
            session_service,
            runtime_backend="kubernetes",
            room_role_source="remote",
        )
    )
    sid = uuid4()
    yield TestClient(app), service, session_service, sid


def test_route_absent_unless_room_role_source_is_remote(participants_api):
    """The default ("deployment") router never registers this route at all —
    it must not go live on a production deployment that hasn't opted in."""
    client, _service, session_service, path = participants_api
    session_service.get_session.return_value = object()
    response = client.get(
        path.rsplit("/participants", 1)[0] + "/participants/role",
        params={"user_id": "bob"},
        headers={"Authorization": f"Bearer {_scoped_workload_token(uuid4())}"},
    )
    assert response.status_code == 405


def test_unsigned_forged_token_is_rejected(remote_participants_api):
    """scoped_credential_claims/require_scope decode WITHOUT verifying a
    signature (by design — see token_scope._decode_claims's docstring:
    verification is delegated to Envoy upstream). An unsigned token carrying
    exactly the right claims (token_use, scope, and a workload_sub naming
    THIS session) must still be refused — extract_principal's mandatory
    verification is what stops a forged claim set on a Forge reached
    without Envoy in front, or directly via the app port."""
    import jwt

    client, service, session_service, sid = remote_participants_api
    session_service.get_session.return_value = object()
    forged = jwt.encode(
        {
            "sub": "niuu-workload",
            "token_use": "valkyrie_build",
            "scopes": ["forge:session:room-role"],
            "workload_sub": f"system:serviceaccount:volundr-sessions:openbao-session-{sid}",
        },
        key="",
        algorithm="none",
        headers={"alg": "none"},
    )
    response = client.get(
        f"/api/v1/forge/sessions/{sid}/participants/role",
        params={"user_id": "bob"},
        headers={"Authorization": f"Bearer {forged}"},
    )
    assert response.status_code == 401
    service.effective_room_role.assert_not_awaited()


def test_wrongly_signed_forged_token_is_rejected(remote_participants_api):
    """Same forged claim set as above, but signed with a key the configured
    identity adapter does not trust — still refused."""
    client, service, session_service, sid = remote_participants_api
    session_service.get_session.return_value = object()
    forged = _scoped_workload_token(sid, signing_key=_WRONG_SIGNING_KEY)
    response = client.get(
        f"/api/v1/forge/sessions/{sid}/participants/role",
        params={"user_id": "bob"},
        headers={"Authorization": f"Bearer {forged}"},
    )
    assert response.status_code == 401
    service.effective_room_role.assert_not_awaited()


def test_effective_role_endpoint_returns_the_service_answer(remote_participants_api):
    client, service, session_service, sid = remote_participants_api
    session_service.get_session.return_value = object()
    service.effective_room_role.return_value = "approver"
    token = _scoped_workload_token(sid)
    response = client.get(
        f"/api/v1/forge/sessions/{sid}/participants/role",
        params={"user_id": "bob", "tenant_id": "acme", "roles": "developer,other"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json() == {"role": "approver"}
    target = service.effective_room_role.await_args.args[1]
    assert target.user_id == "bob"
    assert target.tenant_id == "acme"
    # No identity adapter configured (app.state.identity is None) falls
    # through to the raw, unmapped roles — see
    # test_target_roles_are_mapped_through_the_identity_adapter for the
    # mapped case.
    assert target.roles == ["developer", "other"]


def test_effective_role_endpoint_returns_null_role_for_no_grant(remote_participants_api):
    """A 200 with role: null, not a 403/404 — "no grant" is an answer, not a
    failure, matching effective_room_role's own contract."""
    client, service, session_service, sid = remote_participants_api
    session_service.get_session.return_value = object()
    service.effective_room_role.return_value = None
    token = _scoped_workload_token(sid)
    response = client.get(
        f"/api/v1/forge/sessions/{sid}/participants/role",
        params={"user_id": "stranger"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json() == {"role": None}


def test_effective_role_endpoint_404s_for_a_missing_session(remote_participants_api):
    client, service, session_service, sid = remote_participants_api
    session_service.get_session.return_value = None
    token = _scoped_workload_token(sid)
    response = client.get(
        f"/api/v1/forge/sessions/{sid}/participants/role",
        params={"user_id": "bob"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404
    service.effective_room_role.assert_not_awaited()


def test_unscoped_human_jwt_is_refused(remote_participants_api):
    """require_scope alone would admit this — an ordinary unscoped JWT must
    still be refused, since this endpoint discloses grant existence."""
    import time

    import jwt

    client, service, session_service, sid = remote_participants_api
    session_service.get_session.return_value = object()
    now = int(time.time())
    token = jwt.encode(
        {"sub": "alice", "iat": now, "exp": now + 600},
        _ROOM_ROLE_SIGNING_KEY,
        algorithm="HS256",
    )
    response = client.get(
        f"/api/v1/forge/sessions/{sid}/participants/role",
        params={"user_id": "bob"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
    service.effective_room_role.assert_not_awaited()


def test_missing_bearer_token_is_refused(remote_participants_api):
    """extract_principal (the mandatory signature-verifying gate, run before
    the scope/session-binding checks) rejects this first, with 401."""
    client, service, session_service, sid = remote_participants_api
    session_service.get_session.return_value = object()
    response = client.get(
        f"/api/v1/forge/sessions/{sid}/participants/role", params={"user_id": "bob"}
    )
    assert response.status_code == 401
    service.effective_room_role.assert_not_awaited()


def test_effective_role_endpoint_requires_the_configured_scope_for_scoped_tokens(
    remote_participants_api,
):
    """A scoped workload credential minted for something ELSE (missing
    forge:session:room-role) must be refused, not silently admitted."""
    client, service, session_service, sid = remote_participants_api
    session_service.get_session.return_value = object()
    token = _scoped_workload_token(sid, scopes=["forge:session:create"])
    response = client.get(
        f"/api/v1/forge/sessions/{sid}/participants/role",
        params={"user_id": "bob"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
    service.effective_room_role.assert_not_awaited()


def test_credential_scoped_to_a_different_session_is_refused(remote_participants_api):
    """The scope alone is not enough — a token minted for session A's pod
    must not be usable to probe session B's participants."""
    client, service, session_service, sid = remote_participants_api
    session_service.get_session.return_value = object()
    other_session = uuid4()
    token = _scoped_workload_token(other_session)
    response = client.get(
        f"/api/v1/forge/sessions/{sid}/participants/role",
        params={"user_id": "bob"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
    service.effective_room_role.assert_not_awaited()


def test_target_roles_are_mapped_through_the_identity_adapter(remote_participants_api):
    """The raw, IdP-namespace-less roles a pod forwards ("developer") must be
    mapped the SAME way every other entry point maps them — an unmapped
    Principal here is the same demotion-to-403 class of incident this
    project has already hit once."""
    client, service, session_service, sid = remote_participants_api
    session_service.get_session.return_value = object()
    service.effective_room_role.return_value = "owner"

    mapped_principal = Principal(
        user_id="owner-1", email="", tenant_id="acme", roles=["volundr:developer"]
    )

    class _FakeHeaderIdentity(HeaderAuthenticationPort):
        async def validate_headers(self, headers: dict[str, str]) -> Principal:
            if "x-auth-roles" not in headers:
                # extract_principal's own mandatory verification call (raw
                # request headers — "authorization", no "x-auth-roles").
                # This test is about target role MAPPING, not caller auth.
                return Principal(user_id="niuu-workload", email="", tenant_id="", roles=[])
            assert headers["x-auth-roles"] == "developer"
            return mapped_principal

    client.app.state.identity = _FakeHeaderIdentity()
    token = _scoped_workload_token(sid)
    response = client.get(
        f"/api/v1/forge/sessions/{sid}/participants/role",
        params={"user_id": "owner-1", "tenant_id": "acme", "roles": "developer"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    target = service.effective_room_role.await_args.args[1]
    assert target.roles == ["volundr:developer"]


def test_target_role_mapping_uses_configured_header_names():
    """header_names mirrors settings.identity.kwargs (volundr.main's
    _resolve_ws_principal) rather than a hardcoded x-auth-* guess."""
    service, session_service = AsyncMock(), AsyncMock()
    app = FastAPI()
    app.include_router(
        create_session_participants_router(
            service,
            session_service,
            runtime_backend="kubernetes",
            room_role_source="remote",
            identity_header_names={
                "user_id_header": "x-custom-user",
                "tenant_header": "x-custom-tenant",
                "roles_header": "x-custom-roles",
            },
        )
    )
    sid = uuid4()
    session_service.get_session.return_value = object()
    service.effective_room_role.return_value = "viewer"

    class _CustomHeaderIdentity(HeaderAuthenticationPort):
        async def validate_headers(self, headers: dict[str, str]) -> Principal:
            if "x-custom-roles" not in headers:
                return Principal(user_id="niuu-workload", email="", tenant_id="", roles=[])
            assert headers["x-custom-user"] == "bob"
            assert headers["x-custom-tenant"] == "acme"
            assert headers["x-custom-roles"] == "developer"
            return Principal(user_id="bob", email="", tenant_id="acme", roles=["volundr:developer"])

    app.state.identity = _CustomHeaderIdentity()
    token = _scoped_workload_token(sid)
    response = TestClient(app).get(
        f"/api/v1/forge/sessions/{sid}/participants/role",
        params={"user_id": "bob", "tenant_id": "acme", "roles": "developer"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    target = service.effective_room_role.await_args.args[1]
    assert target.roles == ["volundr:developer"]


def test_identity_adapter_rejection_yields_no_role_not_an_error(remote_participants_api):
    client, service, session_service, sid = remote_participants_api
    session_service.get_session.return_value = object()

    class _RejectingIdentity(HeaderAuthenticationPort):
        async def validate_headers(self, headers: dict[str, str]) -> Principal:
            if "x-auth-roles" not in headers:
                # extract_principal's own mandatory verification call.
                return Principal(user_id="niuu-workload", email="", tenant_id="", roles=[])
            raise InvalidTokenError("unknown user")

    client.app.state.identity = _RejectingIdentity()
    token = _scoped_workload_token(sid)
    response = client.get(
        f"/api/v1/forge/sessions/{sid}/participants/role",
        params={"user_id": "ghost"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json() == {"role": None}
    service.effective_room_role.assert_not_awaited()
