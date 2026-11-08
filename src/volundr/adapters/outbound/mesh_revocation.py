"""Forge's signed "this session stopped" notice on the mesh.

When a session's launch ends, peers should stop believing its certificate at once instead of
waiting for it to expire. Forge holds an Ed25519 key generated in memory at startup and an
``authority`` certificate it issues to itself (kind ``authority``: the only kind a peer
accepts a revocation from), renewed in the background like any peer's. It publishes the
revocation on ``mesh.revocation`` in the mesh realm the session joined, through the same
signing mesh every peer uses (:class:`niuu.adapters.mesh.attested.AttestedMesh`), so the
notice is signed and checked like any other message.

Publishing never blocks a stop and never fails silently: a failure is logged at error and
counted (``niuu.cap_telemetry.record_degraded``, path ``mesh.revocation.publish``); the
certificate's own lifetime (``forge_mcp.mesh_identity.ttl_seconds``) is the backstop.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from niuu.adapters.mesh.attested import REVOCATION_TOPIC, AttestedMesh
from niuu.adapters.mesh.sleipnir_mesh import SleipnirMeshAdapter
from niuu.cap_telemetry import record_degraded
from niuu.mesh.attestation import MeshAttestationVerifier, MeshSigner
from niuu.mesh.config import MeshAttestationConfig
from niuu.mesh.events import MeshEvent, MeshEventType
from volundr.domain.mesh_peer_claims import PeerClaim

logger = logging.getLogger(__name__)

DEGRADED_PATH = "mesh.revocation.publish"
#: The attention hint of a revocation: peers should act on it before anything else.
REVOCATION_URGENCY = 1.0


class MeshRevocationPublisher:
    """Publishes signed session revocations to the Sleipnir bus, one mesh per realm."""

    def __init__(
        self,
        bus: Any,
        signer: MeshSigner,
        *,
        attestation: MeshAttestationConfig | None = None,
    ) -> None:
        self._bus = bus
        self._signer = signer
        self._attestation = attestation or MeshAttestationConfig()
        self._meshes: dict[str, AttestedMesh] = {}

    @property
    def peer_id(self) -> str:
        return self._signer.peer_id

    @property
    def signer(self) -> MeshSigner:
        """Forge's authority signer, also used to sign Forge's platform events."""
        return self._signer

    async def start(self) -> None:
        """Obtain Forge's authority certificate; raises when it cannot be had."""
        await self._signer.start()

    async def stop(self) -> None:
        await self._signer.stop()
        self._meshes.clear()

    def _mesh_for(self, realm: str) -> AttestedMesh:
        mesh = self._meshes.get(realm)
        if mesh is not None:
            return mesh
        signer = self._signer
        verifier = MeshAttestationVerifier(
            lambda: signer.jwks,
            issuer=signer.issuer,
            own_memberships=signer.memberships,
            clock_skew_s=self._attestation.clock_skew_s,
            event_max_age_s=self._attestation.event_max_age_s,
        )
        inner = SleipnirMeshAdapter(
            publisher=self._bus,
            subscriber=self._bus,
            own_peer_id=signer.peer_id,
            environment_id=realm,
            manage_transport_lifecycle=False,
        )
        mesh = AttestedMesh(
            inner,
            signer,
            verifier,
            own_peer_id=signer.peer_id,
            event_max_age_s=self._attestation.event_max_age_s,
        )
        self._meshes[realm] = mesh
        return mesh

    async def revoke(self, *, session_id: str, claim: PeerClaim) -> None:
        """Tell the realm's peers that this launch ended. Never raises; failure is counted."""
        event = MeshEvent(
            type=MeshEventType.REVOCATION,
            source=self._signer.peer_id,
            payload={
                "session_id": session_id,
                "launch_id": claim.launch_id,
                "peer_id": claim.peer_id,
            },
            timestamp=datetime.now(UTC),
            urgency=REVOCATION_URGENCY,
            correlation_id=f"revocation:{session_id}:{claim.launch_id}",
            session_id="",
        )
        try:
            await self._mesh_for(claim.mesh_realm).publish(event, REVOCATION_TOPIC)
        except Exception as exc:
            record_degraded(
                DEGRADED_PATH,
                f"could not publish the revocation of session {session_id} (launch "
                f"{claim.launch_id}, peer {claim.peer_id}, realm {claim.mesh_realm!r}): {exc}; "
                "its peers keep believing its certificate until it expires "
                "(forge_mcp.mesh_identity.ttl_seconds)",
            )
