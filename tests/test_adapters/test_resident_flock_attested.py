"""Forge-hosted resident peers sign what they send and verify what they receive."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from niuu.adapters.mesh.attested import AttestedMesh, refusal_counts, reset_refusal_counts
from niuu.adapters.mesh.sleipnir_mesh import SleipnirMeshAdapter
from niuu.mesh.attestation import KIND_RESIDENT, MeshAttestationVerifier, MeshSigner
from niuu.mesh.events import MeshEvent, MeshEventType
from sleipnir.adapters.in_process import InProcessBus
from tests.support.mesh_identity import InMemoryMeshPeerClaims, mesh_issuer
from tests.test_adapters.test_resident_flock import (
    _Controller,
    _PersonaProvider,
    _Repository,
    _runtime,
)
from volundr.adapters.outbound.resident_flock import (
    ResidentFlockAdapter,
    ResidentMeshCertificates,
    resident_memberships,
)
from volundr.domain.mesh_peer_claims import PeerIdClaimedError
from volundr.domain.models import ResidentObservedState
from volundr.domain.ports import SessionPersona
from volundr.domain.services.mesh_identity import ForgeMeshCertificateSource

PERSONA = SessionPersona(
    name="event-hermes",
    system_prompt="React.",
    consumes_event_types=("proof.started",),
)


def _adapter(runtime, bus, claims, *, persona: bool = True) -> ResidentFlockAdapter:
    return ResidentFlockAdapter(
        _Repository(runtime),
        [_Controller("ok")],
        bus,
        persona_provider=_PersonaProvider(PERSONA) if persona else None,
        mesh_certificates=ResidentMeshCertificates(mesh_issuer(), claims),
    )


async def _sender(adapter: ResidentFlockAdapter, bus, flock_id: UUID, peer_id="sender-peer"):
    """Another certified peer of the same tenant and flock realm."""
    certificates = adapter._mesh_certificates
    signer = MeshSigner(
        ForgeMeshCertificateSource(
            certificates.issuer,
            certificates.claims,
            holder=f"resident:{peer_id}",
            kind=KIND_RESIDENT,
            launch_id="sender-launch",
            memberships=("tenant:tenant-a",),
        ),
        peer_id,
    )
    await signer.start()
    verifier = MeshAttestationVerifier(
        lambda: signer.jwks, issuer=signer.issuer, own_memberships=signer.memberships
    )
    inner = SleipnirMeshAdapter(
        publisher=bus,
        subscriber=bus,
        own_peer_id=peer_id,
        environment_id=str(flock_id),
        manage_transport_lifecycle=False,
    )
    return AttestedMesh(inner, signer, verifier, own_peer_id=peer_id), signer


def _event(source: str) -> MeshEvent:
    return MeshEvent(
        type=MeshEventType.OUTCOME,
        source=source,
        payload={"event_type": "proof.started", "prompt": "go"},
        timestamp=datetime.now(UTC),
        urgency=0.3,
        correlation_id="c1",
        session_id="room-1",
        task_id="t1",
        root_correlation_id="room-1",
    )


async def _settle(bus: InProcessBus, resident: ResidentFlockAdapter) -> None:
    await bus.flush()
    for _ in range(30):
        if resident._reactions and all(
            r.status in {"complete", "failed"} for r in resident._reactions.values()
        ):
            break
        await asyncio.sleep(0)
    await bus.flush()


class TestCertification:
    async def test_the_peer_is_certified_as_a_resident_of_its_flock_realm_and_tenant(self) -> None:
        flock_id, realm_id = uuid4(), uuid4()
        runtime = _runtime(flock_id=flock_id).model_copy(update={"realm_id": realm_id})
        claims = InMemoryMeshPeerClaims()
        resident = _adapter(runtime, InProcessBus(), claims)

        await resident.sync()

        peer = resident._peers[runtime.id]
        assert isinstance(peer.mesh, AttestedMesh)
        certificate = peer.signer.certificate
        assert certificate.kind == KIND_RESIDENT
        assert certificate.peer_id == "hermes-worker"
        assert certificate.session_id == ""
        assert certificate.launch_id == peer.launch_id != ""
        assert set(certificate.memberships) == {
            f"flock:{flock_id}",
            f"realm:{realm_id}",
            "tenant:tenant-a",
        }
        assert claims.rows["hermes-worker"] == {
            "holder": f"resident:{runtime.id}",
            "launch_id": peer.launch_id,
            "mesh_realm": str(flock_id),
            "expires_at": claims.rows["hermes-worker"]["expires_at"],
        }
        await resident.stop()

    def test_memberships_without_a_realm(self) -> None:
        flock_id = uuid4()
        assert resident_memberships(_runtime(flock_id=flock_id)) == (
            f"flock:{flock_id}",
            "tenant:tenant-a",
        )

    async def test_without_certificates_the_mesh_is_not_wrapped(self) -> None:
        runtime = _runtime(flock_id=uuid4())
        resident = ResidentFlockAdapter(_Repository(runtime), [_Controller()], InProcessBus())
        await resident.sync()
        peer = resident._peers[runtime.id]
        assert isinstance(peer.mesh, SleipnirMeshAdapter) and peer.signer is None
        await resident.stop()

    async def test_a_peer_id_held_by_a_session_fails_the_sync_loudly(self) -> None:
        runtime = _runtime(flock_id=uuid4())
        claims = InMemoryMeshPeerClaims()
        claims.rows["hermes-worker"] = {
            "holder": f"session:{uuid4()}",
            "launch_id": "l",
            "mesh_realm": "",
            "expires_at": datetime.now(UTC) + timedelta(minutes=5),
        }
        resident = _adapter(runtime, InProcessBus(), claims)

        with pytest.raises(PeerIdClaimedError, match="hermes-worker"):
            await resident.sync()

        assert resident._peers == {}


class TestSignedTraffic:
    async def test_an_event_from_a_certified_peer_wakes_the_resident(self) -> None:
        flock_id = uuid4()
        runtime = _runtime(flock_id=flock_id, persona_name="event-hermes")
        bus = InProcessBus()
        resident = _adapter(runtime, bus, InMemoryMeshPeerClaims())
        await resident.sync()
        sender, signer = await _sender(resident, bus, flock_id)

        await sender.publish(_event("sender-peer"), "proof.started")
        await _settle(bus, resident)

        assert len(resident._reactions) == 1
        await signer.stop()
        await resident.stop()

    async def test_an_unsigned_event_is_refused_logged_and_counted(self, caplog) -> None:
        flock_id = uuid4()
        runtime = _runtime(flock_id=flock_id, persona_name="event-hermes")
        bus = InProcessBus()
        resident = _adapter(runtime, bus, InMemoryMeshPeerClaims())
        await resident.sync()
        reset_refusal_counts()
        raw = SleipnirMeshAdapter(
            publisher=bus,
            subscriber=bus,
            own_peer_id="intruder",
            environment_id=str(flock_id),
            manage_transport_lifecycle=False,
        )

        with caplog.at_level("ERROR"):
            await raw.publish(_event("intruder"), "proof.started")
            await _settle(bus, resident)

        assert resident._reactions == {}
        assert refusal_counts() == {"event.missing": 1}
        assert any("mesh attestation refused" in r.getMessage() for r in caplog.records)
        await resident.stop()

    async def test_an_event_from_another_tenant_is_refused(self) -> None:
        flock_id = uuid4()
        runtime = _runtime(flock_id=flock_id, persona_name="event-hermes")
        bus = InProcessBus()
        resident = _adapter(runtime, bus, InMemoryMeshPeerClaims())
        await resident.sync()
        reset_refusal_counts()
        certificates = resident._mesh_certificates
        signer = MeshSigner(
            ForgeMeshCertificateSource(
                certificates.issuer,
                certificates.claims,
                holder="resident:stranger",
                kind=KIND_RESIDENT,
                launch_id="l",
                memberships=("tenant:someone-else",),
            ),
            "stranger",
        )
        await signer.start()
        stranger = AttestedMesh(
            SleipnirMeshAdapter(
                publisher=bus,
                subscriber=bus,
                own_peer_id="stranger",
                environment_id=str(flock_id),
                manage_transport_lifecycle=False,
            ),
            signer,
            MeshAttestationVerifier(
                lambda: signer.jwks, issuer=signer.issuer, own_memberships=signer.memberships
            ),
            own_peer_id="stranger",
        )

        await stranger.publish(_event("stranger"), "proof.started")
        await _settle(bus, resident)

        assert resident._reactions == {}
        assert refusal_counts() == {"event.not_member": 1}
        await signer.stop()
        await resident.stop()


class TestTeardown:
    async def test_stop_halts_the_signer_and_frees_the_peer_id(self) -> None:
        runtime = _runtime(flock_id=uuid4())
        claims = InMemoryMeshPeerClaims()
        resident = _adapter(runtime, InProcessBus(), claims)
        await resident.sync()
        signer = resident._peers[runtime.id].signer
        assert signer._renewal.task is not None

        await resident.stop()

        assert signer._renewal.task is None
        assert claims.rows == {}

    async def test_a_resident_that_is_no_longer_active_is_released_on_sync(self) -> None:
        runtime = _runtime(flock_id=uuid4())
        claims = InMemoryMeshPeerClaims()
        repository = _Repository(runtime)
        resident = ResidentFlockAdapter(
            repository,
            [_Controller()],
            InProcessBus(),
            mesh_certificates=ResidentMeshCertificates(mesh_issuer(), claims),
        )
        await resident.sync()
        assert "hermes-worker" in claims.rows

        repository.runtime = runtime.model_copy(
            update={"observed_state": ResidentObservedState.PENDING}
        )
        await resident.sync()

        assert resident._peers == {} and claims.rows == {}

    async def test_a_persona_change_re_certifies_under_a_new_launch(self) -> None:
        runtime = _runtime(flock_id=uuid4(), persona_name="event-hermes")
        claims = InMemoryMeshPeerClaims()
        provider = _PersonaProvider(PERSONA)
        resident = ResidentFlockAdapter(
            _Repository(runtime),
            [_Controller()],
            InProcessBus(),
            persona_provider=provider,
            mesh_certificates=ResidentMeshCertificates(mesh_issuer(), claims),
        )
        await resident.sync()
        first = resident._peers[runtime.id].launch_id
        provider.persona = SessionPersona(
            name="event-hermes", system_prompt="Changed", consumes_event_types=("proof.started",)
        )

        await resident.sync()

        second = resident._peers[runtime.id].launch_id
        assert first != second
        assert claims.rows["hermes-worker"]["launch_id"] == second
        await resident.stop()
