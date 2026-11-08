"""A launch's end frees its peer ids and is announced on the mesh, signed by Forge."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from niuu.adapters.mesh.attested import REVOCATION_TOPIC, AttestedMesh, event_subject
from niuu.adapters.mesh.sleipnir_mesh import SleipnirMeshAdapter, _sleipnir_to_ravn
from niuu.cap_telemetry import degraded_counts
from niuu.mesh import mesh_event_prefix
from niuu.mesh.attestation import (
    KIND_AUTHORITY,
    KIND_RESIDENT,
    PURPOSE_EVENT,
    AttestationReason,
    AttestationRefusedError,
    MeshAttestationVerifier,
    MeshSigner,
    b64url,
    public_key_bytes,
)
from niuu.mesh.events import MeshEvent, MeshEventType
from sleipnir.adapters.in_process import InProcessBus
from tests.support.forge_session import PREFIX, TENANT, bearer
from tests.support.mesh_identity import MeshForge, build_mesh_forge
from volundr.adapters.outbound.mesh_revocation import DEGRADED_PATH, MeshRevocationPublisher
from volundr.domain.mesh_peer_claims import PeerIdClaimedError
from volundr.domain.models import SessionStatus
from volundr.domain.services.mesh_identity import ForgeMeshCertificateSource

REALM = "flock-7"
PEER = "skuld-peer-a"


async def _publisher(
    mesh_issuer, claims, bus, peer_id: str = "forge:test"
) -> MeshRevocationPublisher:
    signer = MeshSigner(
        ForgeMeshCertificateSource(
            mesh_issuer,
            claims,
            holder=f"authority:{peer_id}",
            kind=KIND_AUTHORITY,
            launch_id="authority-launch",
            memberships=(),
        ),
        peer_id,
    )
    publisher = MeshRevocationPublisher(bus, signer)
    await publisher.start()
    return publisher


class _Revocations:
    """Collects what lands on the realm's revocation topic."""

    def __init__(self, bus: InProcessBus, realm: str = REALM) -> None:
        self.events: list = []
        self._bus = bus
        self._pattern = f"{mesh_event_prefix(realm)}.mesh.revocation"

    async def _collect(self, event) -> None:
        self.events.append(event)

    async def listen(self) -> None:
        await self._bus.subscribe([self._pattern], self._collect)


async def _certified_session(mesh: MeshForge, realm: str = REALM):
    session = await mesh.forge.session()
    credential = await mesh.forge.start(session)
    response = mesh.forge.client.post(
        f"{PREFIX}/sessions/{session.id}/mesh-identity",
        json={
            "public_key": b64url(public_key_bytes(Ed25519PrivateKey.generate())),
            "peer_id": PEER,
            "mesh_realm": realm,
        },
        headers=bearer(credential.token),
    )
    assert response.status_code == 200, response.text
    return session, credential


@pytest.fixture
async def bus():
    return InProcessBus()


@pytest.fixture
async def wired(bus: InProcessBus):
    """A Forge whose revocations go out signed on a real bus, plus a listening peer."""
    mesh = build_mesh_forge()
    publisher = await _publisher(mesh.issuer, mesh.claims, bus)
    mesh.use_revocations(publisher)
    seen = _Revocations(bus)
    await seen.listen()
    yield mesh, publisher, seen
    await publisher.stop()


async def _peer_mesh(mesh: MeshForge, bus: InProcessBus, realm: str = REALM) -> AttestedMesh:
    signer = MeshSigner(
        ForgeMeshCertificateSource(
            mesh.issuer,
            mesh.claims,
            holder="resident:listener",
            kind=KIND_RESIDENT,
            launch_id="l",
            memberships=(f"tenant:{TENANT}",),
        ),
        "listener",
    )
    await signer.start()
    verifier = MeshAttestationVerifier(
        lambda: signer.jwks,
        issuer=signer.issuer,
        own_memberships=signer.memberships,
        revocation_retain_s=signer.ttl_s,
    )
    inner = SleipnirMeshAdapter(
        publisher=bus,
        subscriber=bus,
        own_peer_id="listener",
        environment_id=realm,
        manage_transport_lifecycle=False,
    )
    peer = AttestedMesh(inner, signer, verifier, own_peer_id="listener")
    await peer.start()
    return peer


class TestStopPublishesOneSignedRevocation:
    async def test_stop_publishes_exactly_one_event_a_peer_verifies_as_forge(self, bus, wired):
        mesh, publisher, seen = wired
        session, credential = await _certified_session(mesh)
        peer = await _peer_mesh(mesh, bus)

        await mesh.forge.session_service.stop_session(session.id)
        await bus.flush()

        assert len(seen.events) == 1
        event = _sleipnir_to_ravn(seen.events[0])
        assert event.source == "forge:test"
        assert event.payload == {
            "session_id": str(session.id),
            "launch_id": credential.launch_id,
            "peer_id": PEER,
        }
        # Checked the way a peer checks it: against Forge's keys, as the authority.
        verifier = MeshAttestationVerifier(
            lambda: mesh.issuer.jwks(), issuer=mesh.issuer.issuer, own_memberships=lambda: ()
        )
        sender = verifier.verify(
            event.attestation,
            PURPOSE_EVENT,
            event_subject(event, REVOCATION_TOPIC),
            claimed=event.source,
            authority=True,
        )
        assert sender.kind == KIND_AUTHORITY and sender.peer_id == "forge:test"
        # ... and a listening peer drops the launch at once.
        assert (str(session.id), credential.launch_id) in peer.verifier.revoked()
        await peer.stop()

    async def test_a_session_cannot_forge_a_revocation(self, bus, wired):
        mesh, _, _ = wired
        session, credential = await _certified_session(mesh)
        peer = await _peer_mesh(mesh, bus)
        impostor = await _peer_mesh(mesh, bus)  # a resident-kind peer, not Forge
        await impostor.publish(
            MeshEvent(
                type=MeshEventType.DECISION,
                source="listener",
                payload={"session_id": str(session.id), "launch_id": credential.launch_id},
                timestamp=datetime.now(UTC),
                urgency=1.0,
                correlation_id="x",
                session_id="",
            ),
            REVOCATION_TOPIC,
        )
        await bus.flush()
        assert peer.verifier.revoked() == {}
        await peer.stop()
        await impostor.stop()

    async def test_the_claim_is_released_and_a_second_end_publishes_nothing(self, bus, wired):
        mesh, _, seen = wired
        session, _ = await _certified_session(mesh)

        await mesh.forge.session_service.stop_session(session.id)
        assert PEER not in mesh.claims.rows
        await mesh.forge.session_service.archive_session(session.id)
        await mesh.forge.session_service.delete_session(session.id)
        await bus.flush()

        assert len(seen.events) == 1

    async def test_the_revocation_goes_to_the_realm_the_session_joined(self, bus, wired):
        mesh, _, seen = wired
        elsewhere = _Revocations(bus, realm="other-realm")
        await elsewhere.listen()
        session, _ = await _certified_session(mesh, realm=REALM)

        await mesh.forge.session_service.stop_session(session.id)
        await bus.flush()

        assert len(seen.events) == 1 and elsewhere.events == []

    async def test_an_empty_realm_is_the_default_namespace(self, bus, wired):
        mesh, _, _ = wired
        default_realm = _Revocations(bus, realm="")
        await default_realm.listen()
        session, _ = await _certified_session(mesh, realm="")

        await mesh.forge.session_service.stop_session(session.id)
        await bus.flush()

        assert len(default_realm.events) == 1


class TestEveryWayALaunchEnds:
    async def test_a_restart_retires_the_previous_launch(self, bus, wired):
        mesh, _, seen = wired
        session, first = await _certified_session(mesh)
        # The session row was moved to stopped by something that is not stop_session.
        await mesh.forge.set_status(session.id, SessionStatus.STOPPED)

        await mesh.forge.start(session)
        await bus.flush()

        assert len(seen.events) == 1
        assert _sleipnir_to_ravn(seen.events[0]).payload["launch_id"] == first.launch_id
        assert PEER not in mesh.claims.rows

    async def test_a_restart_after_a_stop_publishes_nothing_more(self, bus, wired):
        mesh, _, seen = wired
        session, _ = await _certified_session(mesh)
        await mesh.forge.session_service.stop_session(session.id)

        await mesh.forge.start(session)
        await bus.flush()

        assert len(seen.events) == 1

    async def test_delete_retires_the_launch(self, bus, wired):
        mesh, _, seen = wired
        session, _ = await _certified_session(mesh)

        assert await mesh.forge.session_service.delete_session(session.id)
        await bus.flush()

        assert len(seen.events) == 1 and PEER not in mesh.claims.rows

    async def test_a_session_found_dead_is_retired(self, bus, wired):
        mesh, _, seen = wired
        session, _ = await _certified_session(mesh)
        stored = await mesh.forge.sessions.get(session.id)

        await mesh.forge.session_service._persist_reconciled_session(
            stored.model_copy(update={"status": SessionStatus.RUNNING}), SessionStatus.STOPPED
        )
        await bus.flush()

        assert len(seen.events) == 1 and PEER not in mesh.claims.rows

    async def test_a_session_that_never_asked_for_a_certificate_publishes_nothing(self, bus, wired):
        mesh, _, seen = wired
        session = await mesh.forge.session()
        await mesh.forge.start(session)

        await mesh.forge.session_service.stop_session(session.id)
        await bus.flush()

        assert seen.events == []


class TestStopNeverDependsOnThePublish:
    async def test_a_failing_publish_is_logged_at_error_counted_and_does_not_block_stop(
        self, caplog
    ):
        class BrokenBus:
            async def publish(self, event) -> None:
                raise ConnectionError("bus is down")

        mesh = build_mesh_forge()
        mesh.use_revocations(await _publisher(mesh.issuer, mesh.claims, BrokenBus()))
        session, _ = await _certified_session(mesh)
        before = degraded_counts().get(DEGRADED_PATH, 0)

        with caplog.at_level(logging.ERROR):
            stopped = await mesh.forge.session_service.stop_session(session.id)

        assert stopped.status is SessionStatus.STOPPED
        assert degraded_counts()[DEGRADED_PATH] == before + 1
        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert any(
            "bus is down" in r.getMessage() and "mesh_identity.ttl_seconds" in r.getMessage()
            for r in errors
        )
        assert PEER not in mesh.claims.rows  # the claim is freed whatever the bus did

    async def test_without_a_bus_the_claim_is_still_released(self):
        mesh = build_mesh_forge()  # records revocations instead of publishing
        session, credential = await _certified_session(mesh)

        await mesh.forge.session_service.stop_session(session.id)

        assert PEER not in mesh.claims.rows
        [(session_id, claim)] = mesh.revocations.calls
        assert (session_id, claim.launch_id, claim.peer_id) == (
            str(session.id),
            credential.launch_id,
            PEER,
        )

    async def test_the_authority_needs_a_certificate_to_start(self, bus):
        probe = build_mesh_forge()
        probe.claims.rows["forge:test"] = {
            "holder": f"session:{UUID(int=1)}",
            "launch_id": "l",
            "mesh_realm": "",
            "expires_at": datetime.max.replace(tzinfo=UTC),
        }
        with pytest.raises(PeerIdClaimedError, match="forge:test"):
            await _publisher(probe.issuer, probe.claims, bus)


class TestAuthorityOnlyWordCounts:
    async def test_a_peer_that_is_not_forge_is_not_the_authority(self, bus, wired):
        mesh, _, _ = wired
        peer = await _peer_mesh(mesh, bus)
        subject = {"x": 1}
        attestation = peer.signer.attest(PURPOSE_EVENT, subject)
        with pytest.raises(AttestationRefusedError) as refused:
            peer.verifier.verify(attestation, PURPOSE_EVENT, subject, authority=True)
        assert refused.value.reason is AttestationReason.NOT_AUTHORITY
        await peer.stop()
