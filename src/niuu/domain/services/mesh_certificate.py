"""Issuing mesh certificates: Forge's statement of who a mesh peer is.

A certificate (:class:`niuu.mesh.attestation.MeshCertificate`) is a short-lived RS256 JWT
signed with the same key the workload-identity issuer signs everything with, so peers
verify it against the JWKS they already trust. Unlike a session credential it is
addressed to the mesh, not to Forge:

* ``aud = ["niuu-mesh"]``: no Forge or Envoy audience list contains it;
* ``token_use = "mesh_peer"`` with no scopes, which the edge and Forge deny everywhere
  (``niuu.domain.services.token_scope``);
* ``sub = "mesh-peer:<peer id>"`` and no roles: it speaks for no person.

Forge decides every claim. The peer supplies only its own Ed25519 public key (and names
the peer id it wants, which Forge refuses when another live holder has it).
"""

from __future__ import annotations

import time
from collections.abc import Collection
from typing import Any
from uuid import uuid4

from niuu.domain.services.token_scope import MESH_PEER_TOKEN_USE
from niuu.mesh.attestation import (
    KIND_AUTHORITY,
    KIND_RESIDENT,
    KIND_SERVICE,
    KIND_SESSION,
    MESH_AUDIENCE,
    IssuedMeshCertificate,
    b64url,
)

#: Seconds a certificate is valid before ``nbf``: tolerates clock drift between hosts.
_NOT_BEFORE_LEEWAY_S = 5

_KINDS = frozenset({KIND_SESSION, KIND_RESIDENT, KIND_AUTHORITY, KIND_SERVICE})


class MeshCertificateError(ValueError):
    """A certificate cannot be issued for what was asked."""


class MeshCertificateIssuer:
    """Signs mesh certificates with a workload-identity issuer's key.

    ``signing`` is a :class:`niuu.domain.services.workload_identity.WorkloadIdentityService`
    (anything with ``issuer``, ``jwks()`` and ``sign_claims``). ``ttl_seconds`` bounds how
    long a leaked or stale certificate is believed; peers renew in the background well
    before it ends.
    """

    def __init__(self, signing: Any, *, ttl_seconds: int) -> None:
        if ttl_seconds < 2:
            raise ValueError(
                f"forge_mcp.mesh_identity.ttl_seconds must be at least 2, got {ttl_seconds}"
            )
        self._signing = signing
        self._ttl_seconds = int(ttl_seconds)

    @property
    def ttl_seconds(self) -> int:
        return self._ttl_seconds

    @property
    def issuer(self) -> str:
        return str(self._signing.issuer)

    def jwks(self) -> dict[str, Any]:
        return dict(self._signing.jwks())

    def issue(
        self,
        *,
        peer_id: str,
        kind: str,
        public_key: bytes,
        memberships: Collection[str],
        session_id: str = "",
        launch_id: str = "",
        tenant_id: str = "",
    ) -> IssuedMeshCertificate:
        if kind not in _KINDS:
            raise MeshCertificateError(f"unknown mesh certificate kind {kind!r}")
        if not peer_id.strip():
            raise MeshCertificateError("a mesh certificate needs a peer id")
        if len(public_key) != 32:
            raise MeshCertificateError("the mesh key must be a raw 32-byte Ed25519 public key")
        if kind == KIND_SESSION and not (session_id and launch_id):
            raise MeshCertificateError("a session certificate needs its session and launch")
        now = int(time.time())
        claims = {
            "iss": self.issuer,
            "aud": [MESH_AUDIENCE],
            "sub": f"mesh-peer:{peer_id}",
            "iat": now,
            "nbf": now - _NOT_BEFORE_LEEWAY_S,
            "exp": now + self._ttl_seconds,
            "jti": str(uuid4()),
            "tenant_id": tenant_id,
            "token_use": MESH_PEER_TOKEN_USE,
            "workload_peer_id": peer_id,
            "workload_kind": kind,
            "workload_session_id": session_id,
            "workload_launch_id": launch_id,
            "workload_memberships": sorted(set(memberships)),
            "workload_key": b64url(public_key),
        }
        return IssuedMeshCertificate(
            token=self._signing.sign_claims(claims),
            jwks=self.jwks(),
            issuer=self.issuer,
            ttl_s=self._ttl_seconds,
        )
