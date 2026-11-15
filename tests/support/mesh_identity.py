"""Test support for Forge's mesh identity: in-memory claims and a Forge app slice with it."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from niuu.domain.services.mesh_certificate import MeshCertificateIssuer
from tests.support.forge_session import ForgeApp, build_forge_app, token_issuer, token_service
from volundr.adapters.inbound.rest_mesh_identity import create_mesh_identity_router
from volundr.domain.mesh_peer_claims import MeshPeerClaims, PeerClaim, PeerIdClaimedError
from volundr.domain.services.mesh_identity import MeshIdentityService

MESH_TTL_SECONDS = 600


class InMemoryMeshPeerClaims(MeshPeerClaims):
    """The claim table's contract, in memory: the same rules as the SQL upsert."""

    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self._clock = clock or (lambda: datetime.now(UTC))

    async def claim(
        self,
        *,
        peer_id: str,
        holder: str,
        launch_id: str,
        mesh_realm: str,
        expires_at: datetime,
    ) -> None:
        current = self.rows.get(peer_id)
        if current is not None and current["holder"] != holder:
            if current["expires_at"] > self._clock():
                raise PeerIdClaimedError(peer_id)
        self.rows[peer_id] = {
            "holder": holder,
            "launch_id": launch_id,
            "mesh_realm": mesh_realm,
            "expires_at": expires_at,
        }

    async def release(self, *, holder: str, launch_id: str) -> list[PeerClaim]:
        released = [
            PeerClaim(
                peer_id=peer,
                holder=row["holder"],
                launch_id=launch_id,
                mesh_realm=row["mesh_realm"],
            )
            for peer, row in self.rows.items()
            if row["holder"] == holder and row["launch_id"] == launch_id
        ]
        for claim in released:
            del self.rows[claim.peer_id]
        return released


class RecordingRevocations:
    """A revocation port that records what the session service asked it to publish."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, PeerClaim]] = []

    async def revoke(self, *, session_id: str, claim: PeerClaim) -> None:
        self.calls.append((session_id, claim))


class _LateRetirer:
    """The session service is built before the mesh service that needs its repository."""

    def __init__(self) -> None:
        self.target: MeshIdentityService | None = None

    async def retire(self, session_id: UUID, launch_id: str | None) -> None:
        assert self.target is not None
        await self.target.retire(session_id, launch_id)


def mesh_issuer(signing: Any = None, ttl_seconds: int = MESH_TTL_SECONDS) -> MeshCertificateIssuer:
    return MeshCertificateIssuer(signing or token_issuer(), ttl_seconds=ttl_seconds)


@dataclass
class MeshForge:
    forge: ForgeApp
    service: MeshIdentityService | None
    issuer: MeshCertificateIssuer
    claims: InMemoryMeshPeerClaims
    revocations: RecordingRevocations = field(default_factory=RecordingRevocations)

    def use_revocations(self, port: Any) -> None:
        """Publish through ``port`` (needs the app's own issuer and claims to be built)."""
        assert self.service is not None
        self.service._revocations = port


def build_mesh_forge(*, enabled: bool = True, revocations: Any = None, **kwargs: Any) -> MeshForge:
    """A Forge app slice with the mesh identity route (and the stop/restart hooks) mounted.

    ``enabled=False`` mounts the route without a service, as Forge does when it runs
    without a mesh identity.
    """
    late = _LateRetirer()
    # One signer for the session credentials and the mesh certificates, as in Forge.
    signing = token_issuer()
    forge = build_forge_app(
        tokens=token_service(signing), mesh_identity=late if enabled else None, **kwargs
    )
    issuer = mesh_issuer(signing)
    claims = InMemoryMeshPeerClaims()
    recorded = RecordingRevocations()
    service = (
        MeshIdentityService(
            issuer,
            claims,
            forge.sessions,
            revocations=revocations if revocations is not None else recorded,
        )
        if enabled
        else None
    )
    late.target = service
    forge.app.include_router(create_mesh_identity_router(service))
    return MeshForge(
        forge=forge, service=service, issuer=issuer, claims=claims, revocations=recorded
    )
