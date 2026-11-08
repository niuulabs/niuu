"""Forge's mesh identity for sessions: who a mesh peer is, and when it stops being it.

A session's Skuld asks Forge for a mesh certificate (``niuu.mesh.attestation``) with the
session's own Forge credential, naming only its Ed25519 public key, the peer id it wants and
the mesh realm it joins. Forge decides everything else:

* **Whether the session may.** It must exist, belong to the credential's owner, be on the
  credential's current launch and not be stopped or archived: the same rules that revoke
  the credential itself (``volundr.adapters.inbound.forge_session_auth``).
* **Whether the peer id is free.** The claim table gives a peer id to one live holder; a
  second holder is refused (409), the same session on a new launch is not.
* **Who the peer belongs to** (its memberships), from the session row and the
  :class:`MeshMembershipSource` list, never from anything the session says.

When the session stops, restarts or is deleted, :meth:`MeshIdentityService.retire` releases
its claims and, when Forge has an event bus, publishes a signed revocation so peers drop it
at once instead of waiting for its certificate to expire.

Memberships today: ``tenant:<tenant id>`` from the session row. A Forge session has no
link to a resident runtime's flock or realm (a ``ResidentRuntime`` owns its flock and the
sessions it creates are engine-owned, not Forge sessions), so no ``flock:`` or ``realm:``
membership is invented for one. Other sources (an organisation, a project) plug in through
:class:`MeshMembershipSource` without changing this service.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

import jwt

from niuu.domain.services.forge_session_token import ForgeSessionClaims
from niuu.domain.services.mesh_certificate import MeshCertificateIssuer
from niuu.mesh.attestation import (
    KIND_SESSION,
    IssuedMeshCertificate,
    b64url_decode,
)
from volundr.domain.mesh_peer_claims import MeshPeerClaims, PeerClaim, session_holder
from volundr.domain.models import Session, SessionStatus
from volundr.domain.ports import SessionRepository
from volundr.domain.services.forge_session_launch import launch_id_of

logger = logging.getLogger(__name__)

#: The tenant of a session row that has none: the same name the session credential uses.
DEFAULT_TENANT = "default"
TENANT_PREFIX = "tenant:"
FLOCK_PREFIX = "flock:"
REALM_PREFIX = "realm:"

_ED25519_PUBLIC_KEY_BYTES = 32
_REVOKED_STATUSES = frozenset({SessionStatus.STOPPED, SessionStatus.ARCHIVED})


class MeshIdentityRefusedError(Exception):
    """The session credential no longer entitles its holder to a mesh identity (401)."""


class MeshIdentityForbiddenError(Exception):
    """The credential is not the session's own (403)."""


class MeshRequestError(ValueError):
    """The request is malformed: not a raw Ed25519 key, or an unusable peer id (422)."""


class MeshMembershipSource(Protocol):
    """Where a session's mesh memberships come from. Sources add; none removes."""

    async def memberships(self, session: Session) -> Collection[str]:
        """The memberships this source grants ``session`` (``kind:id`` strings)."""


class TenantMembershipSource:
    """``tenant:<tenant id>``: every session belongs to its tenant's mesh."""

    async def memberships(self, session: Session) -> Collection[str]:
        return (f"{TENANT_PREFIX}{session.tenant_id or DEFAULT_TENANT}",)


class MeshRevocationPort(Protocol):
    """Tells the mesh a launch ended. Failure is the implementation's to report loudly."""

    async def revoke(self, *, session_id: str, claim: PeerClaim) -> None:
        """Announce that ``claim``'s launch of ``session_id`` ended; raises when it cannot."""


class MeshIdentityRetirer(Protocol):
    """What the session service calls when a session's launch ends."""

    async def retire(self, session_id: UUID, launch_id: str | None) -> None:
        """Release the session's mesh peer claim and revoke the launch that held it."""


@dataclass(frozen=True)
class IssuedSessionIdentity:
    """A session's mesh certificate, the trust anchors to check others', and what it says."""

    peer_id: str
    certificate: IssuedMeshCertificate
    expires_at: int
    memberships: tuple[str, ...]


class ForgeMeshCertificateSource:
    """Issues certificates to a Forge-side peer (a resident, Forge itself) in process.

    The :class:`niuu.mesh.attestation.MeshSigner` of such a peer renews through this; each
    issue renews the peer id's claim, so a peer id stays held while its signer runs and
    frees itself one certificate lifetime after the signer stops.
    """

    def __init__(
        self,
        issuer: MeshCertificateIssuer,
        claims: MeshPeerClaims,
        *,
        holder: str,
        kind: str,
        launch_id: str,
        memberships: Collection[str],
        tenant_id: str = "",
        mesh_realm: str = "",
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._issuer = issuer
        self._claims = claims
        self._holder = holder
        self._kind = kind
        self._launch_id = launch_id
        self._memberships = tuple(memberships)
        self._tenant_id = tenant_id
        self._mesh_realm = mesh_realm
        self._clock = clock or (lambda: datetime.now(UTC))

    async def issue(self, public_key: bytes, peer_id: str) -> IssuedMeshCertificate:
        expires_at = self._clock() + timedelta(seconds=self._issuer.ttl_seconds)
        await self._claims.claim(
            peer_id=peer_id,
            holder=self._holder,
            launch_id=self._launch_id,
            mesh_realm=self._mesh_realm,
            expires_at=expires_at,
        )
        return self._issuer.issue(
            peer_id=peer_id,
            kind=self._kind,
            public_key=public_key,
            memberships=self._memberships,
            launch_id=self._launch_id,
            tenant_id=self._tenant_id,
        )


class MeshIdentityService:
    """Issues session mesh certificates and retires them when the launch ends."""

    def __init__(
        self,
        issuer: MeshCertificateIssuer,
        claims: MeshPeerClaims,
        sessions: SessionRepository,
        *,
        membership_sources: Sequence[MeshMembershipSource] | None = None,
        revocations: MeshRevocationPort | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._issuer = issuer
        self._claims = claims
        self._sessions = sessions
        self._sources = tuple(membership_sources or (TenantMembershipSource(),))
        self._revocations = revocations
        self._clock = clock or (lambda: datetime.now(UTC))

    @property
    def publishes_revocations(self) -> bool:
        return self._revocations is not None

    async def issue_for_session(
        self,
        *,
        credential: ForgeSessionClaims,
        session_id: UUID,
        peer_id: str,
        public_key: str,
        mesh_realm: str,
    ) -> IssuedSessionIdentity:
        """Claim ``peer_id`` for the session and certify ``public_key`` under it.

        ``public_key`` is the raw 32-byte Ed25519 key, base64url without padding.
        """
        if credential.session_id != str(session_id):
            raise MeshIdentityForbiddenError("This Forge session token is bound to another session")
        raw_key = _decode_key(public_key)
        _check_peer_id(peer_id)
        session = await self._sessions.get(session_id)
        launch_id = _check_live(credential, session)
        memberships = await self._memberships(session)
        expires_at = self._clock() + timedelta(seconds=self._issuer.ttl_seconds)
        await self._claims.claim(
            peer_id=peer_id,
            holder=session_holder(session_id),
            launch_id=launch_id,
            mesh_realm=mesh_realm,
            expires_at=expires_at,
        )
        certificate = self._issuer.issue(
            peer_id=peer_id,
            kind=KIND_SESSION,
            public_key=raw_key,
            memberships=memberships,
            session_id=str(session_id),
            launch_id=launch_id,
            tenant_id=session.tenant_id or DEFAULT_TENANT,
        )
        exp = jwt.decode(certificate.token, options={"verify_signature": False})["exp"]
        return IssuedSessionIdentity(
            peer_id=peer_id,
            certificate=certificate,
            expires_at=int(exp),
            memberships=tuple(sorted(set(memberships))),
        )

    async def _memberships(self, session: Session) -> list[str]:
        found: list[str] = []
        for source in self._sources:
            found.extend(await source.memberships(session))
        return found

    async def retire(self, session_id: UUID, launch_id: str | None) -> None:
        """The launch ended: free its peer ids and tell the mesh (once per claim).

        Idempotent: a launch already retired has no claims left, so nothing is published
        twice. A launch that never asked for a certificate has none either.
        """
        if not launch_id:
            return
        released = await self._claims.release(
            holder=session_holder(session_id), launch_id=launch_id
        )
        if self._revocations is None:
            return
        for claim in released:
            await self._revocations.revoke(session_id=str(session_id), claim=claim)


def _decode_key(text: str) -> bytes:
    try:
        raw = b64url_decode(text)
    except ValueError as exc:
        raise MeshRequestError(
            "public_key must be the raw 32-byte Ed25519 public key, base64url without padding"
        ) from exc
    if len(raw) != _ED25519_PUBLIC_KEY_BYTES:
        raise MeshRequestError(
            f"public_key must be a raw {_ED25519_PUBLIC_KEY_BYTES}-byte Ed25519 public key, "
            f"got {len(raw)} bytes"
        )
    return raw


def _check_peer_id(peer_id: str) -> None:
    if not peer_id or peer_id != peer_id.strip():
        raise MeshRequestError("peer_id must be a non-empty string without surrounding spaces")


def _check_live(credential: ForgeSessionClaims, session: Session | None) -> str:
    """The session's current launch id, or the reason its credential is no longer good."""
    if session is None:
        raise MeshIdentityRefusedError("Forge session token revoked: its session is gone")
    if session.owner_id != credential.owner_id:
        raise MeshIdentityRefusedError("Forge session token revoked: session owner changed")
    launch_id = launch_id_of(session.workload_config)
    if launch_id != credential.launch_id or launch_id is None:
        raise MeshIdentityRefusedError(
            "Forge session token revoked: the session has been restarted since it was issued"
        )
    if session.status in _REVOKED_STATUSES:
        raise MeshIdentityRefusedError(
            f"Forge session token revoked: the session is {session.status.value}"
        )
    return launch_id
