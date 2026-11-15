"""POST /sessions/{id}/mesh-identity: issuance, refusals, claims, and the mesh_peer lock-out."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from fastapi.testclient import TestClient

from niuu.adapters.pat_revocation_middleware import PATRevocationMiddleware
from niuu.domain.models import Principal
from niuu.domain.services.forge_session_token import ForgeSessionTokenError
from niuu.domain.services.token_scope import (
    FORGE_MESH_IDENTITY_SCOPE,
    FORGE_SESSION_SCOPES,
    MESH_PEER_TOKEN_USE,
    credential_allows_route,
)
from niuu.mesh.attestation import (
    KIND_RESIDENT,
    KIND_SESSION,
    MESH_AUDIENCE,
    MeshAttestationVerifier,
    b64url,
    public_key_bytes,
)
from tests.support.forge_session import OWNER, PREFIX, TENANT, bearer
from tests.support.mesh_identity import MeshForge, build_mesh_forge
from volundr.adapters.inbound.forge_session_auth import (
    ROUTE_POLICIES,
    Binding,
    match_policy,
)
from volundr.domain.models import SessionStatus

PEER = "skuld-peer-a"


def _key() -> tuple[Ed25519PrivateKey, str]:
    private = Ed25519PrivateKey.generate()
    return private, b64url(public_key_bytes(private))


def _body(public_key: str, peer_id: str = PEER, realm: str = "") -> dict:
    return {"public_key": public_key, "peer_id": peer_id, "mesh_realm": realm}


def _url(session_id: UUID) -> str:
    return f"{PREFIX}/sessions/{session_id}/mesh-identity"


@pytest.fixture
def mesh() -> MeshForge:
    return build_mesh_forge()


async def _started(mesh: MeshForge, name: str = "s", owner: str = OWNER):
    session = await mesh.forge.session(name, owner=owner)
    credential = await mesh.forge.start(session)
    return session, credential


class TestIssuance:
    async def test_issues_a_certificate_the_mesh_verifies(self, mesh: MeshForge) -> None:
        session, credential = await _started(mesh)
        private, public = _key()
        response = mesh.forge.client.post(
            _url(session.id), json=_body(public, realm="flock-7"), headers=bearer(credential.token)
        )
        assert response.status_code == 200, response.text
        issued = response.json()
        assert response.headers["cache-control"] == "no-store"
        assert issued["memberships"] == [f"tenant:{TENANT}"]
        assert issued["ttl_seconds"] == mesh.issuer.ttl_seconds

        verifier = MeshAttestationVerifier(
            lambda: issued["jwks"], issuer=issued["issuer"], own_memberships=lambda: ()
        )
        certificate = verifier.read_certificate(issued["certificate"])
        assert certificate.kind == KIND_SESSION
        assert certificate.peer_id == PEER
        assert certificate.session_id == str(session.id)
        assert certificate.launch_id == credential.launch_id
        assert certificate.memberships == (f"tenant:{TENANT}",)
        assert certificate.public_key == public_key_bytes(private)
        assert certificate.expires_at == issued["expires_at"]
        assert certificate.expires_at - certificate.issued_at == mesh.issuer.ttl_seconds

    async def test_the_certificate_says_nothing_a_forge_route_would_honour(
        self, mesh: MeshForge
    ) -> None:
        session, credential = await _started(mesh)
        _, public = _key()
        issued = mesh.forge.client.post(
            _url(session.id), json=_body(public), headers=bearer(credential.token)
        ).json()
        claims = jwt.decode(issued["certificate"], options={"verify_signature": False})
        assert claims["aud"] == [MESH_AUDIENCE]
        assert claims["token_use"] == MESH_PEER_TOKEN_USE
        assert "scopes" not in claims and "scope" not in claims
        assert "roles" not in claims and "email" not in claims
        assert claims["sub"] == f"mesh-peer:{PEER}"
        assert OWNER not in claims["sub"]
        assert claims["workload_session_id"] == str(session.id)

    async def test_records_the_claim_with_its_realm_and_expiry(self, mesh: MeshForge) -> None:
        session, credential = await _started(mesh)
        _, public = _key()
        issued = mesh.forge.client.post(
            _url(session.id), json=_body(public, realm="flock-7"), headers=bearer(credential.token)
        ).json()
        row = mesh.claims.rows[PEER]
        assert row["holder"] == f"session:{session.id}"
        assert row["launch_id"] == credential.launch_id
        assert row["mesh_realm"] == "flock-7"
        assert int(row["expires_at"].timestamp()) == pytest.approx(issued["expires_at"], abs=2)

    async def test_the_same_session_renews_its_peer_id(self, mesh: MeshForge) -> None:
        session, credential = await _started(mesh)
        _, public = _key()
        for _ in range(2):
            response = mesh.forge.client.post(
                _url(session.id), json=_body(public), headers=bearer(credential.token)
            )
            assert response.status_code == 200

    async def test_a_restart_keeps_the_peer_id_for_the_same_session(self, mesh: MeshForge) -> None:
        session, first = await _started(mesh)
        _, public = _key()
        assert (
            mesh.forge.client.post(
                _url(session.id), json=_body(public), headers=bearer(first.token)
            ).status_code
            == 200
        )
        await mesh.forge.set_status(session.id, SessionStatus.STOPPED)
        second = await mesh.forge.start(session)
        response = mesh.forge.client.post(
            _url(session.id), json=_body(public), headers=bearer(second.token)
        )
        assert response.status_code == 200
        assert mesh.claims.rows[PEER]["launch_id"] == second.launch_id


class TestRefusals:
    async def test_a_stopped_session_is_refused(self, mesh: MeshForge) -> None:
        session, credential = await _started(mesh)
        await mesh.forge.set_status(session.id, SessionStatus.STOPPED)
        _, public = _key()
        response = mesh.forge.client.post(
            _url(session.id), json=_body(public), headers=bearer(credential.token)
        )
        assert response.status_code == 401 and "stopped" in response.json()["detail"]
        assert PEER not in mesh.claims.rows

    async def test_an_archived_session_is_refused(self, mesh: MeshForge) -> None:
        session, credential = await _started(mesh)
        await mesh.forge.set_status(session.id, SessionStatus.ARCHIVED)
        _, public = _key()
        response = mesh.forge.client.post(
            _url(session.id), json=_body(public), headers=bearer(credential.token)
        )
        assert response.status_code == 401

    async def test_a_stale_launch_is_refused(self, mesh: MeshForge) -> None:
        session, first = await _started(mesh)
        await mesh.forge.set_status(session.id, SessionStatus.STOPPED)
        await mesh.forge.start(session)
        _, public = _key()
        response = mesh.forge.client.post(
            _url(session.id), json=_body(public), headers=bearer(first.token)
        )
        assert response.status_code == 401 and "restarted" in response.json()["detail"]

    async def test_another_sessions_url_is_refused(self, mesh: MeshForge) -> None:
        own, credential = await _started(mesh, "own")
        other = await mesh.forge.session("other")
        _, public = _key()
        response = mesh.forge.client.post(
            _url(other.id), json=_body(public), headers=bearer(credential.token)
        )
        assert response.status_code == 403
        assert own.id != other.id and PEER not in mesh.claims.rows

    async def test_a_session_of_another_owner_is_refused(self, mesh: MeshForge) -> None:
        foreign = await mesh.forge.session("foreign", owner="someone-else")
        _, credential = await _started(mesh, "own")
        _, public = _key()
        response = mesh.forge.client.post(
            _url(foreign.id), json=_body(public), headers=bearer(credential.token)
        )
        assert response.status_code == 403

    async def test_a_caller_without_a_session_credential_is_refused(self, mesh: MeshForge) -> None:
        session, _ = await _started(mesh)
        _, public = _key()
        response = mesh.forge.client.post(_url(session.id), json=_body(public))
        assert response.status_code == 403
        assert "session credential" in response.json()["detail"]

    @pytest.mark.parametrize(
        "public_key",
        ["", "AAAA", b64url(b"\x01" * 31), b64url(b"\x01" * 33), "not base64 at all"],
    )
    async def test_a_key_that_is_not_a_raw_ed25519_key_is_refused(
        self, mesh: MeshForge, public_key: str
    ) -> None:
        session, credential = await _started(mesh)
        response = mesh.forge.client.post(
            _url(session.id), json=_body(public_key), headers=bearer(credential.token)
        )
        assert response.status_code == 422
        assert PEER not in mesh.claims.rows

    @pytest.mark.parametrize("peer_id", ["", " padded "])
    async def test_an_unusable_peer_id_is_refused(self, mesh: MeshForge, peer_id: str) -> None:
        session, credential = await _started(mesh)
        _, public = _key()
        response = mesh.forge.client.post(
            _url(session.id), json=_body(public, peer_id=peer_id), headers=bearer(credential.token)
        )
        assert response.status_code == 422

    async def test_unknown_body_fields_are_refused(self, mesh: MeshForge) -> None:
        session, credential = await _started(mesh)
        _, public = _key()
        response = mesh.forge.client.post(
            _url(session.id),
            json={**_body(public), "memberships": ["flock:x"]},
            headers=bearer(credential.token),
        )
        assert response.status_code == 422

    async def test_disabled_forge_answers_503_with_the_remedy(self) -> None:
        mesh = build_mesh_forge(enabled=False)
        session, credential = await _started(mesh)
        _, public = _key()
        response = mesh.forge.client.post(
            _url(session.id), json=_body(public), headers=bearer(credential.token)
        )
        assert response.status_code == 503
        assert "forge_mcp.mesh_identity" in response.json()["detail"]


class TestPeerIdClaims:
    async def test_a_peer_id_held_by_another_live_session_is_a_409(self, mesh: MeshForge) -> None:
        first, first_credential = await _started(mesh, "first")
        second, second_credential = await _started(mesh, "second")
        _, public = _key()
        assert (
            mesh.forge.client.post(
                _url(first.id), json=_body(public), headers=bearer(first_credential.token)
            ).status_code
            == 200
        )
        response = mesh.forge.client.post(
            _url(second.id), json=_body(public), headers=bearer(second_credential.token)
        )
        assert response.status_code == 409
        assert PEER in response.json()["detail"] and "another live" in response.json()["detail"]
        assert mesh.claims.rows[PEER]["holder"] == f"session:{first.id}"

    async def test_an_expired_claim_can_be_taken_over(self, mesh: MeshForge) -> None:
        session, credential = await _started(mesh)
        mesh.claims.rows[PEER] = {
            "holder": "session:long-gone",
            "launch_id": "old",
            "mesh_realm": "",
            "expires_at": datetime.now(UTC) - timedelta(seconds=1),
        }
        _, public = _key()
        response = mesh.forge.client.post(
            _url(session.id), json=_body(public), headers=bearer(credential.token)
        )
        assert response.status_code == 200
        assert mesh.claims.rows[PEER]["holder"] == f"session:{session.id}"

    async def test_a_resident_holding_the_peer_id_blocks_a_session(self, mesh: MeshForge) -> None:
        session, credential = await _started(mesh)
        mesh.claims.rows[PEER] = {
            "holder": f"resident:{uuid.uuid4()}",
            "launch_id": "l",
            "mesh_realm": "",
            "expires_at": datetime.now(UTC) + timedelta(minutes=5),
        }
        _, public = _key()
        response = mesh.forge.client.post(
            _url(session.id), json=_body(public), headers=bearer(credential.token)
        )
        assert response.status_code == 409


class TestPolicy:
    def test_the_route_is_the_sessions_own_with_its_own_scope(self) -> None:
        sid = uuid.uuid4()
        matched = match_policy("POST", _url(sid))
        assert matched is not None
        policy, params = matched
        assert policy.scope == FORGE_MESH_IDENTITY_SCOPE
        assert policy.binding is Binding.OWN
        assert params["session_id"] == str(sid)

    def test_the_scope_belongs_to_every_session_credential(self) -> None:
        assert FORGE_MESH_IDENTITY_SCOPE in FORGE_SESSION_SCOPES

    async def test_a_credential_without_the_scope_is_refused(self, mesh: MeshForge) -> None:
        session = await mesh.forge.session()
        credential = await mesh.forge.start(session)
        claims = jwt.decode(credential.token, options={"verify_signature": False})
        narrowed = [s for s in claims["scopes"] if s != FORGE_MESH_IDENTITY_SCOPE]
        token = mesh.forge.tokens.signing_issuer.issue_token(
            principal=Principal(
                user_id=OWNER, email="", tenant_id=TENANT, roles=["volundr:developer"]
            ),
            workload_subject=f"forge-session:{session.id}",
            workload_name="s",
            audiences=["volundr-api"],
            token_use="forge_session",
            claims={
                "session_id": str(session.id),
                "launch_id": credential.launch_id,
                "scopes": narrowed,
            },
            ttl_seconds=600,
        ).token
        _, public = _key()
        response = mesh.forge.client.post(
            _url(session.id), json=_body(public), headers=bearer(token)
        )
        assert response.status_code == 403
        assert FORGE_MESH_IDENTITY_SCOPE in response.json()["detail"]


# --------------------------------------------------------------------------------------
# No Forge route admits a mesh_peer certificate.
# --------------------------------------------------------------------------------------


def _mounted_routes(app: FastAPI) -> list[tuple[str, str]]:
    """Every (method, path) the app documents, path parameters filled with samples."""
    sample = {"session_id": str(uuid.uuid4())}
    routes: list[tuple[str, str]] = []
    for template, operations in app.openapi()["paths"].items():
        path = re.sub(r"\{(\w+)\}", lambda m: sample.get(m.group(1), "sample"), template)
        routes.extend((method.upper(), path) for method in operations)
    return routes


@pytest.fixture
def mesh_peer_token(mesh: MeshForge) -> str:
    _, public = _key()
    raw = Ed25519PrivateKey.generate()
    return mesh.issuer.issue(
        peer_id="peer-x",
        kind=KIND_SESSION,
        public_key=public_key_bytes(raw),
        memberships=[f"tenant:{TENANT}"],
        session_id=str(uuid.uuid4()),
        launch_id="launch",
        tenant_id=TENANT,
    ).token


class TestNoRouteAdmitsAMeshPeerCertificate:
    def test_the_edge_denies_it_on_every_mounted_route(
        self, mesh: MeshForge, mesh_peer_token: str
    ) -> None:
        routes = _mounted_routes(mesh.forge.app)
        assert len(routes) > 30, "the Forge app slice should mount its real routers"
        admitted = [
            (method, path)
            for method, path in routes
            if credential_allows_route(mesh_peer_token, method, path)
        ]
        assert admitted == []

    @pytest.mark.parametrize("method", ["GET", "POST", "PUT", "PATCH", "DELETE"])
    def test_the_edge_denies_it_on_every_session_policy_route_and_identity_me(
        self, mesh_peer_token: str, method: str
    ) -> None:
        sid = uuid.uuid4()
        paths = [
            re.sub(r"\{session_id\}", str(sid), re.sub(r"\{\w+\}", "x", policy.template))
            for policy in ROUTE_POLICIES
        ]
        paths += [
            "/api/v1/identity/me",
            "/api/v1/forge/mcp",
            f"{PREFIX}/sessions",
            f"{PREFIX}/notifications",
            "/api/v1/tokens/workload/exchange",
            "/health",
            "/",
        ]
        admitted = [p for p in paths if credential_allows_route(mesh_peer_token, method, p)]
        assert admitted == []

    def test_the_identity_edge_middleware_refuses_it_everywhere_but_not_a_plain_jwt(
        self, mesh: MeshForge, mesh_peer_token: str
    ) -> None:
        guarded = FastAPI()
        guarded.add_middleware(PATRevocationMiddleware, enabled=True)
        routes = _mounted_routes(mesh.forge.app)
        for method, path in routes:
            guarded.add_api_route(path, lambda: {"ok": True}, methods=[method])
        client = TestClient(guarded)
        plain = jwt.encode({"sub": "someone", "exp": 4102444800}, "k" * 32, algorithm="HS256")

        refused = [
            (method, path)
            for method, path in routes
            if client.request(method, path, headers=bearer(mesh_peer_token)).status_code == 403
        ]
        assert refused == routes
        control = client.request(*routes[0], headers=bearer(plain))
        assert control.status_code == 200

    async def test_forge_session_verification_rejects_it(
        self, mesh: MeshForge, mesh_peer_token: str
    ) -> None:
        with pytest.raises(ForgeSessionTokenError):
            mesh.forge.tokens.verify(mesh_peer_token)

    async def test_the_session_middleware_does_not_treat_it_as_a_session_credential(
        self, mesh: MeshForge, mesh_peer_token: str
    ) -> None:
        session, _ = await _started(mesh)
        _, public = _key()
        response = mesh.forge.client.post(
            _url(session.id), json=_body(public), headers=bearer(mesh_peer_token)
        )
        # require_scope denies a mesh_peer token (it carries no scopes), before any handler runs.
        assert response.status_code == 403
        assert "required scope" in response.json()["detail"]
        assert PEER not in mesh.claims.rows

    async def test_a_resident_certificate_is_locked_out_too(self, mesh: MeshForge) -> None:
        token = mesh.issuer.issue(
            peer_id="resident-x",
            kind=KIND_RESIDENT,
            public_key=public_key_bytes(Ed25519PrivateKey.generate()),
            memberships=["flock:f"],
            launch_id="l",
            tenant_id=TENANT,
        ).token
        assert not credential_allows_route(token, "GET", "/api/v1/identity/me")
        assert not credential_allows_route(token, "GET", f"{PREFIX}/sessions")
