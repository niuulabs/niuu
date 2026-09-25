"""Guild node-join REST endpoints — pairing, join, heartbeat, leave, revoke.

Mounted under ``/api/v1/niuu/guild``. ``pairing-codes``, ``join``, and the
admin ``nodes`` routes are authenticated the normal way (a human/admin
bearer token, a scoped ``node_join`` workload JWT, and a human/admin bearer
token respectively). ``heartbeat`` and ``leave`` are node-originated and
carry no bearer JWT at all — they are authenticated by an Ed25519 signature
over the request instead (see ``niuu.ports.node_verifier.RegisteredNodeVerifier``),
so those two paths are exempted from the JWT-identity middleware
(``niuu.adapters.pat_revocation_middleware``) and from Envoy's jwt_authn
filter in the Guild chart.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status
from pydantic import BaseModel, Field

from niuu.adapters.inbound.auth import extract_principal
from niuu.domain.models import InstanceKind, Principal, RegisteredInstance, RegisteredNode
from niuu.domain.services.guild_join import (
    GuildJoinAccessError,
    GuildJoinError,
    GuildJoinService,
    IdentityTrustConfig,
    NodeRegistrationConflictError,
    OfferedInstance,
    PairingCodeInvalidError,
    PairingCodeMintingUnavailableError,
    UntrustedNodeError,
)
from niuu.domain.services.token_scope import NODE_JOIN_SCOPE, require_scope
from niuu.ports.node_verifier import NodeSignatureError, RegisteredNodeVerifier

#: Signature-carrying request headers a joined node must send.
NODE_ID_HEADER = "x-niuu-node-id"
TIMESTAMP_HEADER = "x-niuu-timestamp"
SIGNATURE_HEADER = "x-niuu-signature"

#: Same uniform message the verifier itself uses — an unknown/malformed node
#: id must be indistinguishable from a bad signature.
_AUTH_FAILED = "Node authentication failed"


class OfferedInstanceRequest(BaseModel):
    kind: str = Field(default=InstanceKind.GENERIC.value)
    base_url: str = Field(serialization_alias="baseUrl", validation_alias="baseUrl")
    ravn_base_url: str = Field(
        default="", serialization_alias="ravnBaseUrl", validation_alias="ravnBaseUrl"
    )
    config: dict[str, Any] = Field(default_factory=dict)

    def to_domain(self) -> OfferedInstance:
        return OfferedInstance(
            kind=InstanceKind(self.kind),
            base_url=self.base_url,
            ravn_base_url=self.ravn_base_url,
            config=self.config,
        )


class InstanceResponse(BaseModel):
    id: str
    kind: str
    slug: str
    name: str
    base_url: str = Field(serialization_alias="baseUrl")
    visibility: str


def _instance_response(instance: RegisteredInstance) -> InstanceResponse:
    return InstanceResponse(
        id=instance.id,
        kind=instance.kind.value,
        slug=instance.slug,
        name=instance.name,
        base_url=instance.base_url,
        visibility=instance.visibility.value,
    )


class MintPairingCodeRequest(BaseModel):
    allow_plaintext: bool = Field(
        default=False,
        serialization_alias="allowPlaintext",
        validation_alias="allowPlaintext",
        description="Consent for the joining node to register a plaintext (http://) instance URL.",
    )
    allow_untrusted_node_auth: bool = Field(
        default=False,
        serialization_alias="allowUntrustedNodeAuth",
        validation_alias="allowUntrustedNodeAuth",
        description="Consent for a host_auth.mode: none node to join an oidc Guild.",
    )


class PairingCodeResponse(BaseModel):
    code: str
    expires_at: datetime = Field(serialization_alias="expiresAt")


class JoinRequest(BaseModel):
    code: str
    node_name: str = Field(serialization_alias="nodeName", validation_alias="nodeName")
    public_key: str = Field(serialization_alias="publicKey", validation_alias="publicKey")
    node_auth_mode: str = Field(
        default="none",
        serialization_alias="nodeAuthMode",
        validation_alias="nodeAuthMode",
        description="The joining host's own host_auth.mode ('none' or 'oidc').",
    )
    instances: list[OfferedInstanceRequest] = Field(default_factory=list)


class IdentityTrustResponse(BaseModel):
    mode: str
    issuers: list[dict[str, Any]]


class JoinResponse(BaseModel):
    node_id: str = Field(serialization_alias="nodeId")
    instances: list[InstanceResponse]
    identity: IdentityTrustResponse


class HeartbeatRequest(BaseModel):
    instances: list[OfferedInstanceRequest] = Field(default_factory=list)


class HeartbeatResponse(BaseModel):
    ok: bool = True
    last_seen_at: datetime | None = Field(default=None, serialization_alias="lastSeenAt")
    instances: list[InstanceResponse] = Field(default_factory=list)


class NodeResponse(BaseModel):
    id: str
    name: str
    tenant_id: str = Field(serialization_alias="tenantId")
    created_by: str = Field(serialization_alias="createdBy")
    created_at: datetime = Field(serialization_alias="createdAt")
    last_seen_at: datetime | None = Field(default=None, serialization_alias="lastSeenAt")


def _node_response(node: RegisteredNode) -> NodeResponse:
    return NodeResponse(
        id=node.id,
        name=node.name,
        tenant_id=node.tenant_id,
        created_by=node.created_by,
        created_at=node.created_at,
        last_seen_at=node.last_seen_at,
    )


def _identity_response(identity: IdentityTrustConfig) -> IdentityTrustResponse:
    return IdentityTrustResponse(mode=identity.mode, issuers=identity.issuers)


def _parse_node_id(node_id: str) -> str:
    """Validate the path param is a UUID before any DB lookup.

    A malformed id must fail exactly like an unknown one — see
    ``_AUTH_FAILED`` — never a distinguishable 400/422 that tells a caller
    "that ID shape doesn't even exist" versus "that node doesn't exist".
    """
    try:
        return str(UUID(node_id))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_AUTH_FAILED) from exc


async def _verify_node_request(
    request: Request,
    verifier: RegisteredNodeVerifier,
    node_id: str,
) -> RegisteredNode:
    validated_node_id = _parse_node_id(node_id)
    header_node_id = request.headers.get(NODE_ID_HEADER, "")
    if header_node_id and header_node_id != validated_node_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_AUTH_FAILED)
    timestamp_header = request.headers.get(TIMESTAMP_HEADER, "")
    signature = request.headers.get(SIGNATURE_HEADER, "")
    if not timestamp_header or not signature:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_AUTH_FAILED)
    try:
        timestamp = int(timestamp_header)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_AUTH_FAILED) from exc
    body = await request.body()
    try:
        return await verifier.verify(
            node_id=validated_node_id,
            method=request.method,
            path=request.url.path,
            timestamp=timestamp,
            body=body,
            signature=signature,
        )
    except NodeSignatureError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc


def create_guild_join_router(
    service: GuildJoinService,
    *,
    node_verifier: RegisteredNodeVerifier,
) -> APIRouter:
    """Create the `niuu join` router: pairing codes, join, heartbeat, leave, revoke."""
    router = APIRouter(prefix="/api/v1/niuu/guild", tags=["Guild Join"])

    @router.post(
        "/pairing-codes",
        response_model=PairingCodeResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def mint_pairing_code(
        body: MintPairingCodeRequest = MintPairingCodeRequest(),
        principal: Principal = Depends(extract_principal),
    ) -> PairingCodeResponse:
        """Mint a single-use, short-TTL pairing code. Admin/owner only."""
        try:
            minted = await service.mint_pairing_code(
                principal,
                allow_plaintext=body.allow_plaintext,
                allow_untrusted_node_auth=body.allow_untrusted_node_auth,
            )
        except GuildJoinAccessError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except PairingCodeMintingUnavailableError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
            ) from exc
        return PairingCodeResponse(code=minted.code, expires_at=minted.expires_at)

    @router.get("/nodes", response_model=list[NodeResponse])
    async def list_nodes(
        principal: Principal = Depends(extract_principal),
    ) -> list[NodeResponse]:
        """List nodes joined under the caller's tenant. Admin/owner only."""
        try:
            nodes = await service.list_nodes(principal)
        except GuildJoinAccessError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        return [_node_response(node) for node in nodes]

    @router.delete("/nodes/{node_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def revoke_node(
        node_id: str = Path(description="Registered node UUID"),
        principal: Principal = Depends(extract_principal),
    ) -> None:
        """Revoke a node — deregisters it and every instance it offered.

        Admin/owner only, so a stolen node key can be cut off immediately.
        """
        try:
            revoked = await service.revoke_node(principal, node_id)
        except GuildJoinAccessError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        if not revoked:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=node_id)

    @router.post(
        "/join",
        response_model=JoinResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def join(
        body: JoinRequest,
        _scope: None = Depends(require_scope(NODE_JOIN_SCOPE)),
    ) -> JoinResponse:
        """Consume a pairing code and register a new node and its instances."""
        try:
            result = await service.join(
                raw_code=body.code,
                node_name=body.node_name,
                public_key=body.public_key,
                node_auth_mode=body.node_auth_mode,
                instances=[item.to_domain() for item in body.instances],
            )
        except PairingCodeInvalidError as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
        except UntrustedNodeError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except NodeRegistrationConflictError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except GuildJoinError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        return JoinResponse(
            node_id=result.node.id,
            instances=[_instance_response(i) for i in result.instances],
            identity=_identity_response(result.identity),
        )

    @router.post("/nodes/{node_id}/heartbeat", response_model=HeartbeatResponse)
    async def heartbeat(
        request: Request,
        body: HeartbeatRequest,
        node_id: str = Path(description="Registered node UUID"),
    ) -> HeartbeatResponse:
        """Node-signed heartbeat: refresh presence and re-sync offered instances."""
        node = await _verify_node_request(request, node_verifier, node_id)
        try:
            refreshed, instances = await service.heartbeat(
                node, [item.to_domain() for item in body.instances]
            )
        except GuildJoinError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        return HeartbeatResponse(
            last_seen_at=refreshed.last_seen_at,
            instances=[_instance_response(i) for i in instances],
        )

    @router.post("/nodes/{node_id}/leave", status_code=status.HTTP_204_NO_CONTENT)
    async def leave(
        request: Request,
        node_id: str = Path(description="Registered node UUID"),
    ) -> None:
        """Node-signed leave: deregister this node (instances cascade)."""
        node = await _verify_node_request(request, node_verifier, node_id)
        await service.leave(node)

    return router
