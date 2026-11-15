"""REST adapter for a session's mesh certificate (under ``/api/v1/forge``).

``POST /sessions/{session_id}/mesh-identity`` gives the session's Skuld the certificate it
signs mesh messages under (``niuu.mesh.attestation``), and renews it. Only the session's
own ``forge_session`` credential (scope ``forge:mesh:identity``) may call it, and only for
its own session. The body carries what the peer alone knows (its public key, the peer id it
wants, the mesh realm it joins); Forge decides everything the certificate says.

* 200 the certificate, the JWKS that checks other peers' certificates, the issuer and
  lifetime, and the memberships Forge certified
* 401 the credential is revoked (session gone, restarted, stopped or archived)
* 403 not the session's own Forge session credential
* 409 the peer id is held by another live session or resident
* 422 the public key is not a raw Ed25519 key, or the body is malformed
* 503 Forge runs without a mesh identity (``mesh.attestation.required: false`` or
  ``forge_mcp.session_tokens.enabled`` is false)
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from niuu.domain.services.mesh_certificate import MeshCertificateError
from niuu.domain.services.token_scope import FORGE_MESH_IDENTITY_SCOPE, require_scope
from volundr.adapters.inbound.forge_session_auth import (
    forge_session_claims,
    require_bound_session,
)
from volundr.domain.mesh_peer_claims import PeerIdClaimedError
from volundr.domain.services.mesh_identity import (
    MeshIdentityForbiddenError,
    MeshIdentityRefusedError,
    MeshIdentityService,
    MeshRequestError,
)

logger = logging.getLogger(__name__)


class MeshIdentityRequest(BaseModel):
    """Body of ``POST /sessions/{id}/mesh-identity``."""

    model_config = ConfigDict(extra="forbid")

    public_key: str = Field(
        min_length=1,
        description="The peer's raw 32-byte Ed25519 public key, base64url without padding. "
        "The private key never leaves the peer.",
    )
    peer_id: str = Field(min_length=1, description="The mesh peer id this peer wants.")
    mesh_realm: str = Field(
        default="",
        description="The mesh realm (Sleipnir environment) the peer joins; empty is the "
        "default namespace. Forge publishes this session's revocation in that realm.",
    )


class MeshIdentityResponse(BaseModel):
    """The certificate and what a peer needs to verify other peers' certificates."""

    certificate: str = Field(description="The mesh_peer JWT. Show it to peers, never to Forge.")
    jwks: dict[str, Any] = Field(description="Forge's signing keys for certificates.")
    issuer: str
    ttl_seconds: int = Field(description="Lifetime of the certificate; renew at half of it.")
    expires_at: int = Field(description="Unix time the certificate expires.")
    memberships: list[str] = Field(description="What Forge certified the peer belongs to.")


def create_mesh_identity_router(
    service: MeshIdentityService | None,
    *,
    prefix: str = "/api/v1/forge",
) -> APIRouter:
    """The mesh identity route. ``service=None`` mounts it answering 503 with the remedy."""
    router = APIRouter(prefix=prefix, tags=["Mesh identity"])

    @router.post(
        "/sessions/{session_id}/mesh-identity",
        response_model=MeshIdentityResponse,
        dependencies=[Depends(require_scope(FORGE_MESH_IDENTITY_SCOPE))],
    )
    async def issue_mesh_identity(
        request: Request,
        body: MeshIdentityRequest,
        response: Response,
        session_id: UUID = Path(description="Session id"),
    ) -> MeshIdentityResponse:
        """Certify the session's peer: claim its peer id and sign its public key."""
        if service is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "This Forge issues no mesh identities: enable forge_mcp.session_tokens and "
                "forge_mcp.mesh_identity, or run the mesh with mesh.attestation.required: false",
            )
        credential = forge_session_claims(request)
        if credential is None:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Only the session's own Forge session credential may obtain its mesh identity",
            )
        require_bound_session(request, session_id)
        try:
            issued = await service.issue_for_session(
                credential=credential,
                session_id=session_id,
                peer_id=body.peer_id,
                public_key=body.public_key,
                mesh_realm=body.mesh_realm,
            )
        except MeshIdentityForbiddenError as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
        except MeshIdentityRefusedError as exc:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, str(exc), headers={"WWW-Authenticate": "Bearer"}
            ) from exc
        except PeerIdClaimedError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        except (MeshRequestError, MeshCertificateError) as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
        response.headers["Cache-Control"] = "no-store"
        return MeshIdentityResponse(
            certificate=issued.certificate.token,
            jwks=issued.certificate.jwks,
            issuer=issued.certificate.issuer,
            ttl_seconds=issued.certificate.ttl_s,
            expires_at=issued.expires_at,
            memberships=list(issued.memberships),
        )

    return router
