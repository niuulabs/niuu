"""MeshIdentityService: who may be certified, as what, and what retiring a launch does."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from niuu.domain.services.forge_session_token import ForgeSessionClaims
from niuu.mesh.attestation import KIND_RESIDENT, MeshAttestationVerifier, b64url, public_key_bytes
from tests.conftest import InMemorySessionRepository
from tests.support.mesh_identity import InMemoryMeshPeerClaims, RecordingRevocations, mesh_issuer
from volundr.domain.models import Session, SessionStatus
from volundr.domain.services.forge_session_launch import with_forge_mcp
from volundr.domain.services.mesh_identity import (
    DEFAULT_TENANT,
    ForgeMeshCertificateSource,
    MeshIdentityForbiddenError,
    MeshIdentityRefusedError,
    MeshIdentityService,
    MeshRequestError,
    TenantMembershipSource,
)

LAUNCH = "launch-1"


def _public() -> str:
    return b64url(public_key_bytes(Ed25519PrivateKey.generate()))


def _session(**updates) -> Session:
    return Session(
        name="s",
        owner_id="owner",
        tenant_id="tenant-1",
        status=SessionStatus.RUNNING,
        workload_config=with_forge_mcp({}, grants=[], launch_id=LAUNCH),
        **updates,
    )


def _credential(session: Session, **updates) -> ForgeSessionClaims:
    values = {
        "session_id": str(session.id),
        "launch_id": LAUNCH,
        "owner_id": "owner",
        "tenant_id": "tenant-1",
        "email": "",
        "scopes": (),
        "expires_at": 0,
        "token_id": "t",
    }
    values.update(updates)
    return ForgeSessionClaims(**values)


class _Source:
    def __init__(self, *names: str) -> None:
        self.names = names

    async def memberships(self, session: Session):
        return self.names


async def _service(session: Session | None = None, **kwargs):
    repository = InMemorySessionRepository()
    if session is not None:
        await repository.create(session)
    claims = InMemoryMeshPeerClaims()
    service = MeshIdentityService(mesh_issuer(), claims, repository, **kwargs)
    return service, claims


async def _issue(service, session, credential=None, **kwargs):
    return await service.issue_for_session(
        credential=credential or _credential(session),
        session_id=session.id,
        peer_id=kwargs.pop("peer_id", "peer"),
        public_key=kwargs.pop("public_key", _public()),
        mesh_realm=kwargs.pop("mesh_realm", ""),
    )


class TestRefusals:
    async def test_an_unknown_session_is_refused(self) -> None:
        service, claims = await _service()
        ghost = _session()
        with pytest.raises(MeshIdentityRefusedError, match="gone"):
            await _issue(service, ghost)
        assert claims.rows == {}

    async def test_another_owners_session_is_refused(self) -> None:
        session = _session()
        service, claims = await _service(session)
        with pytest.raises(MeshIdentityRefusedError, match="owner changed"):
            await _issue(service, session, _credential(session, owner_id="someone-else"))
        assert claims.rows == {}

    async def test_a_credential_for_another_session_is_forbidden(self) -> None:
        session = _session()
        service, _ = await _service(session)
        with pytest.raises(MeshIdentityForbiddenError, match="another session"):
            await _issue(service, session, _credential(session, session_id=str(uuid4())))

    @pytest.mark.parametrize("launch", ["old-launch", ""])
    async def test_a_stale_launch_is_refused(self, launch: str) -> None:
        session = _session()
        service, _ = await _service(session)
        with pytest.raises(MeshIdentityRefusedError, match="restarted"):
            await _issue(service, session, _credential(session, launch_id=launch))

    async def test_a_session_that_never_launched_is_refused(self) -> None:
        session = _session().model_copy(update={"workload_config": {}})
        service, _ = await _service(session)
        with pytest.raises(MeshIdentityRefusedError, match="restarted"):
            await _issue(service, session)

    @pytest.mark.parametrize("status", [SessionStatus.STOPPED, SessionStatus.ARCHIVED])
    async def test_a_stopped_or_archived_session_is_refused(self, status) -> None:
        session = _session().model_copy(update={"status": status})
        service, _ = await _service(session)
        with pytest.raises(MeshIdentityRefusedError, match=status.value):
            await _issue(service, session)

    @pytest.mark.parametrize("public_key", ["", "A", "AAAA", b64url(b"x" * 33)])
    async def test_a_key_that_is_not_32_bytes_is_refused(self, public_key: str) -> None:
        session = _session()
        service, claims = await _service(session)
        with pytest.raises(MeshRequestError, match="Ed25519"):
            await _issue(service, session, public_key=public_key)
        assert claims.rows == {}

    async def test_a_blank_peer_id_is_refused(self) -> None:
        session = _session()
        service, _ = await _service(session)
        with pytest.raises(MeshRequestError, match="peer_id"):
            await _issue(service, session, peer_id="  ")


class TestMemberships:
    async def test_the_tenant_comes_from_the_session_row(self) -> None:
        session = _session()
        service, _ = await _service(session)
        issued = await _issue(service, session)
        assert issued.memberships == ("tenant:tenant-1",)

    async def test_a_session_without_a_tenant_gets_the_default_one(self) -> None:
        session = _session().model_copy(update={"tenant_id": None})
        service, _ = await _service(session)
        issued = await _issue(service, session)
        assert issued.memberships == (f"tenant:{DEFAULT_TENANT}",)

    async def test_further_sources_add_without_replacing(self) -> None:
        session = _session()
        service, _ = await _service(
            session,
            membership_sources=[TenantMembershipSource(), _Source("org:acme", "tenant:tenant-1")],
        )
        issued = await _issue(service, session)
        assert issued.memberships == ("org:acme", "tenant:tenant-1")
        verifier = MeshAttestationVerifier(
            lambda: issued.certificate.jwks,
            issuer=issued.certificate.issuer,
            own_memberships=lambda: (),
        )
        assert verifier.read_certificate(issued.certificate.token).memberships == issued.memberships


class TestRetire:
    async def test_no_launch_means_nothing_to_retire(self) -> None:
        revocations = RecordingRevocations()
        service, _ = await _service(revocations=revocations)
        await service.retire(uuid4(), None)
        assert revocations.calls == []

    async def test_a_retire_without_a_revocation_port_still_frees_the_claim(self) -> None:
        session = _session()
        service, claims = await _service(session)
        assert service.publishes_revocations is False
        await _issue(service, session)

        await service.retire(session.id, LAUNCH)

        assert claims.rows == {}

    async def test_retire_publishes_once_per_released_claim(self) -> None:
        session = _session()
        revocations = RecordingRevocations()
        service, claims = await _service(session, revocations=revocations)
        assert service.publishes_revocations is True
        await _issue(service, session, peer_id="a", mesh_realm="r1")
        await _issue(service, session, peer_id="b", mesh_realm="r2")

        await service.retire(session.id, LAUNCH)
        await service.retire(session.id, LAUNCH)

        assert sorted((c.peer_id, c.mesh_realm) for _, c in revocations.calls) == [
            ("a", "r1"),
            ("b", "r2"),
        ]
        assert claims.rows == {}

    async def test_retiring_an_old_launch_leaves_the_new_launchs_claim(self) -> None:
        session = _session()
        service, claims = await _service(session, revocations=RecordingRevocations())
        await _issue(service, session)
        claims.rows["peer"]["launch_id"] = "newer"

        await service.retire(session.id, LAUNCH)

        assert "peer" in claims.rows


class TestForgeCertificateSource:
    async def test_each_issue_renews_the_claim_and_certifies_the_key(self) -> None:
        claims = InMemoryMeshPeerClaims()
        now = datetime(2026, 11, 8, tzinfo=UTC)
        source = ForgeMeshCertificateSource(
            mesh_issuer(ttl_seconds=60),
            claims,
            holder="resident:r",
            kind=KIND_RESIDENT,
            launch_id="l",
            memberships=("flock:f",),
            tenant_id="t",
            mesh_realm="realm",
            clock=lambda: now,
        )

        issued = await source.issue(b"\x02" * 32, "peer")

        assert claims.rows["peer"]["expires_at"] == now + timedelta(seconds=60)
        assert claims.rows["peer"]["mesh_realm"] == "realm"
        verifier = MeshAttestationVerifier(
            lambda: issued.jwks, issuer=issued.issuer, own_memberships=lambda: ()
        )
        certificate = verifier.read_certificate(issued.token)
        assert (certificate.kind, certificate.memberships, certificate.public_key) == (
            KIND_RESIDENT,
            ("flock:f",),
            b"\x02" * 32,
        )
