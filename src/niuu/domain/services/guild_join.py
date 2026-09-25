"""Domain service orchestrating `niuu join` — pairing codes, nodes, instances.

See ``.claude/rules/architecture.md`` (the ``node_join`` scoped-workload-token
exception) and ``docs/operator/joining-machines.md`` for the end-to-end flow.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4

from niuu.domain.models import (
    InstanceKind,
    InstanceVisibility,
    Principal,
    RegisteredInstance,
    RegisteredNode,
)
from niuu.domain.services.instances import InstanceService
from niuu.domain.services.token_scope import NODE_JOIN_SCOPE, VALKYRIE_BUILD_TOKEN_USE
from niuu.ports.instances import InstanceRepository
from niuu.ports.nodes import NodeRepository
from niuu.ports.pairing_codes import PairingCodeRepository
from niuu.ports.workload_identity import WorkloadTokenIssuer


class GuildJoinError(Exception):
    """Base error for the node-join flow."""


class GuildJoinAccessError(GuildJoinError):
    """Raised when a principal may not mint a pairing code."""


class PairingCodeInvalidError(GuildJoinError):
    """Raised when a presented pairing code cannot be consumed.

    Deliberately does not distinguish unknown / expired / already-used to
    the caller — any of the three means "mint a new one", and telling a
    would-be attacker which reason applies leaks information for free.
    """


@dataclass(frozen=True)
class OfferedInstance:
    """One runtime instance a joining or heartbeating node offers."""

    kind: InstanceKind
    base_url: str
    ravn_base_url: str = ""
    config: dict = field(default_factory=dict)


@dataclass(frozen=True)
class MintedPairingCode:
    """A freshly minted pairing code, returned once."""

    code: str
    expires_at: datetime


@dataclass(frozen=True)
class IdentityTrustConfig:
    """What a newly joined node should trust to verify human identity.

    Guild does not invent a second human-auth path for joined nodes — they
    adopt the same OIDC issuer(s) Guild itself trusts (see the "Shared IdP"
    owner decision).
    """

    mode: str
    issuers: list[dict]


@dataclass(frozen=True)
class JoinResult:
    node: RegisteredNode
    instances: list[RegisteredInstance]
    identity: IdentityTrustConfig


def _slugify(value: str) -> str:
    slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in value).strip("-")
    return slug or "node"


def _hash_code(raw_code: str) -> str:
    return hashlib.sha256(raw_code.encode()).hexdigest()


#: Raw Ed25519 public keys are exactly 32 bytes.
_ED25519_PUBLIC_KEY_LENGTH = 32


def _validate_public_key(public_key: str) -> str:
    stripped = public_key.strip()
    try:
        raw = base64.b64decode(stripped, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise GuildJoinError("Node public key must be base64-encoded") from exc
    if len(raw) != _ED25519_PUBLIC_KEY_LENGTH:
        raise GuildJoinError(
            f"Node public key must decode to {_ED25519_PUBLIC_KEY_LENGTH} raw bytes "
            f"(a raw Ed25519 public key), got {len(raw)}"
        )
    return stripped


class GuildJoinService:
    """Mints pairing codes and admits/heartbeats/removes joined nodes."""

    def __init__(
        self,
        *,
        pairing_codes: PairingCodeRepository,
        nodes: NodeRepository,
        instance_service: InstanceService,
        instance_repository: InstanceRepository,
        workload_identity: WorkloadTokenIssuer,
        identity_trust: IdentityTrustConfig,
    ) -> None:
        self._pairing_codes = pairing_codes
        self._nodes = nodes
        self._instance_service = instance_service
        self._instance_repository = instance_repository
        self._workload_identity = workload_identity
        self._identity_trust = identity_trust

    async def mint_pairing_code(self, principal: Principal) -> MintedPairingCode:
        """Mint a single-use pairing code, admin/owner only.

        The code is itself a scoped workload JWT (``scopes=["node_join"]``),
        so the join route is admitted by the existing
        ``require_scope("node_join")`` machinery; this method additionally
        records the code's hash so it can be spent exactly once.
        """
        if "volundr:admin" not in principal.roles:
            raise GuildJoinAccessError("Only admins may mint a node pairing code")

        issued = self._workload_identity.issue_token(
            principal=principal,
            workload_subject=principal.user_id,
            workload_name="guild-node-pairing",
            audiences=["volundr-api"],
            token_use=VALKYRIE_BUILD_TOKEN_USE,
            claims={"scopes": [NODE_JOIN_SCOPE]},
        )
        expires_at = datetime.fromtimestamp(issued.expires_at, UTC)
        await self._pairing_codes.create(
            code_hash=_hash_code(issued.token),
            created_by=principal.user_id,
            tenant_id=principal.tenant_id,
            expires_at=expires_at,
        )
        return MintedPairingCode(code=issued.token, expires_at=expires_at)

    async def join(
        self,
        *,
        raw_code: str,
        node_name: str,
        public_key: str,
        instances: list[OfferedInstance],
    ) -> JoinResult:
        """Consume a pairing code exactly once and register a new node.

        Request validation happens before the code is consumed — a malformed
        name or key must not burn a single-use code the operator will have
        to re-mint.
        """
        name = node_name.strip()
        if not name:
            raise GuildJoinError("Node name is required")
        validated_public_key = _validate_public_key(public_key)

        consumed = await self._pairing_codes.consume(_hash_code(raw_code))
        if consumed is None:
            raise PairingCodeInvalidError("Pairing code is invalid, expired, or already used")

        node = await self._nodes.create(
            node_id=str(uuid4()),
            name=name,
            public_key=validated_public_key,
            tenant_id=consumed.tenant_id,
            created_by=consumed.created_by,
        )
        await self._pairing_codes.attach_node(consumed.id, node.id)
        registered = await self._register_offered_instances(node, instances)
        return JoinResult(node=node, instances=registered, identity=self._identity_trust)

    async def heartbeat(
        self, node: RegisteredNode, instances: list[OfferedInstance]
    ) -> tuple[RegisteredNode, list[RegisteredInstance]]:
        """Refresh ``last_seen_at`` and re-sync this node's offered instances."""
        refreshed = await self._nodes.touch_heartbeat(node.id)
        if refreshed is None:
            raise GuildJoinError(f"Node {node.id} was removed")
        registered = await self._register_offered_instances(refreshed, instances)
        return refreshed, registered

    async def leave(self, node: RegisteredNode) -> None:
        """Deregister a node's instances, then the node itself."""
        for instance in await self._owned_instances(node.id):
            await self._instance_repository.delete_instance(instance.id)
        await self._nodes.delete(node.id)

    async def _owned_instances(self, node_id: str) -> list[RegisteredInstance]:
        all_instances = await self._instance_repository.list_instances()
        return [i for i in all_instances if str(i.config.get("node_id", "")) == node_id]

    async def _register_offered_instances(
        self, node: RegisteredNode, instances: list[OfferedInstance]
    ) -> list[RegisteredInstance]:
        visibility = InstanceVisibility.TENANT if node.tenant_id else InstanceVisibility.SYSTEM
        registered: list[RegisteredInstance] = []
        for offered in instances:
            config = dict(offered.config)
            config["node_id"] = node.id
            if offered.ravn_base_url:
                config["ravn_base_url"] = offered.ravn_base_url
            slug = f"{_slugify(node.name)}-{offered.kind.value}"
            instance = await self._instance_service.upsert_seed_instance(
                kind=offered.kind,
                slug=slug,
                name=f"{node.name} ({offered.kind.value})",
                base_url=offered.base_url,
                visibility=visibility,
                tenant_id=node.tenant_id or None,
                config=config,
                tags=[f"node:{node.id}"],
            )
            registered.append(instance)
        return registered
