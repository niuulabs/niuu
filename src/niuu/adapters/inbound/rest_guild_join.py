"""Guild node-join REST endpoints — pairing, join, heartbeat, leave.

Mounted under ``/api/v1/niuu/guild``. ``pairing-codes`` and ``join`` are
authenticated the normal way (a human/admin bearer token, then a scoped
``node_join`` workload JWT respectively). ``heartbeat`` and ``leave`` are
node-originated and carry no bearer JWT at all — they are authenticated by an
Ed25519 signature over the request instead (see
``niuu.ports.node_verifier.RegisteredNodeVerifier``), so those two paths are
exempted from the JWT-identity middleware
(``niuu.adapters.pat_revocation_middleware``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status
from pydantic import BaseModel, Field

from niuu.adapters.inbound.auth import extract_principal
from niuu.domain.models import InstanceKind, Principal, RegisteredInstance, RegisteredNode
from niuu.domain.services.guild_join import (
    GuildJoinAccessError,
    GuildJoinError,
    GuildJoinService,
    IdentityTrustConfig,
    OfferedInstance,
    PairingCodeInvalidError,
)
from niuu.domain.services.instances import InstanceTransportSecurityError, InstanceValidationError
from niuu.domain.services.token_scope import NODE_JOIN_SCOPE, require_scope
from niuu.ports.node_verifier import NodeSignatureError, RegisteredNodeVerifier

#: Signature-carrying request headers a joined node must send.
NODE_ID_HEADER = "x-niuu-node-id"
TIMESTAMP_HEADER = "x-niuu-timestamp"
SIGNATURE_HEADER = "x-niuu-signature"


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


class PairingCodeResponse(BaseModel):
    code: str
    expires_at: datetime = Field(serialization_alias="expiresAt")


class JoinRequest(BaseModel):
    code: str
    node_name: str = Field(serialization_alias="nodeName", validation_alias="nodeName")
    public_key: str = Field(serialization_alias="publicKey", validation_alias="publicKey")
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


def _identity_response(identity: IdentityTrustConfig) -> IdentityTrustResponse:
    return IdentityTrustResponse(mode=identity.mode, issuers=identity.issuers)


async def _verify_node_request(
    request: Request,
    verifier: RegisteredNodeVerifier,
    node_id: str,
) -> RegisteredNode:
    header_node_id = request.headers.get(NODE_ID_HEADER, "")
    if header_node_id and header_node_id != node_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{NODE_ID_HEADER} does not match the path node id",
        )
    timestamp_header = request.headers.get(TIMESTAMP_HEADER, "")
    signature = request.headers.get(SIGNATURE_HEADER, "")
    if not timestamp_header or not signature:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Missing {TIMESTAMP_HEADER} or {SIGNATURE_HEADER} header",
        )
    try:
        timestamp = int(timestamp_header)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{TIMESTAMP_HEADER} must be an integer Unix timestamp",
        ) from exc
    body = await request.body()
    try:
        return await verifier.verify(
            node_id=node_id,
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
    """Create the `niuu join` router: pairing codes, join, heartbeat, leave."""
    router = APIRouter(prefix="/api/v1/niuu/guild", tags=["Guild Join"])

    @router.post(
        "/pairing-codes",
        response_model=PairingCodeResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def mint_pairing_code(
        principal: Principal = Depends(extract_principal),
    ) -> PairingCodeResponse:
        """Mint a single-use, short-TTL pairing code. Admin/owner only."""
        try:
            minted = await service.mint_pairing_code(principal)
        except GuildJoinAccessError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        return PairingCodeResponse(code=minted.code, expires_at=minted.expires_at)

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
                instances=[item.to_domain() for item in body.instances],
            )
        except PairingCodeInvalidError as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
        except InstanceTransportSecurityError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
            ) from exc
        except (GuildJoinError, InstanceValidationError) as exc:
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
        except InstanceTransportSecurityError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
            ) from exc
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
        """Node-signed leave: deregister this node's instances and itself."""
        node = await _verify_node_request(request, node_verifier, node_id)
        await service.leave(node)

    return router
