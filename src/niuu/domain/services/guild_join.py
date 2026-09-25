"""Domain service orchestrating `niuu join` — pairing codes, nodes, instances.

See ``.claude/rules/architecture.md`` (the ``node_join`` scoped-workload-token
exception, and the Ed25519 node-signing exception) and
``docs/operator/joining-machines.md`` for the end-to-end flow.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from niuu.domain.models import (
    InstanceKind,
    InstanceVisibility,
    Principal,
    RegisteredInstance,
    RegisteredNode,
)
from niuu.domain.services.token_scope import NODE_JOIN_SCOPE, VALKYRIE_BUILD_TOKEN_USE
from niuu.domain.transport_security import configured_dial_urls, insecure_transport_reason
from niuu.ports.guild_join_repository import GuildJoinRepository, InstanceWrite, NodeConflictError
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


class PairingCodeMintingUnavailableError(GuildJoinError):
    """Raised when workload identity exchange is not configured.

    Mapped to 503 (not 500): this is a configuration gap with a known
    remedy, never an unexpected server error.
    """


class NodeRegistrationConflictError(GuildJoinError):
    """Raised when a node name or public key is already registered."""


class UntrustedNodeError(GuildJoinError):
    """Raised when a node with no identity verification of its own
    (``host_auth.mode: none``) tries to join an ``oidc`` Guild without the
    pairing code's explicit ``allow_untrusted_node_auth`` consent."""


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


def _hash_code(raw_code: str) -> str:
    return hashlib.sha256(raw_code.encode()).hexdigest()


#: Raw Ed25519 public keys are exactly 32 bytes.
_ED25519_PUBLIC_KEY_LENGTH = 32

#: Config keys a node may set on the instances it offers. Deliberately an
#: allowlist, not a denylist: "transport" (which can fake embedded-target
#: routing) and "allow_plaintext" (which Guild alone grants, from the
#: pairing code's own consent — see below) must never be reachable from a
#: node's own payload, and a growing feature surface should have to add
#: itself here rather than default to permitted.
ALLOWED_OFFERED_CONFIG_KEYS = frozenset(
    {"tls_fingerprint", "cluster", "namespace", "labels", "deploymentLabels", "kubernetesLabels"}
)


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


def _validate_offered_config(config: dict) -> None:
    disallowed = set(config) - ALLOWED_OFFERED_CONFIG_KEYS
    if disallowed:
        raise GuildJoinError(
            f"Offered instance config may not set {sorted(disallowed)}; allowed keys are "
            f"{sorted(ALLOWED_OFFERED_CONFIG_KEYS)}"
        )


def _resolve_instance_config(offered: OfferedInstance, *, pairing_allow_plaintext: bool) -> dict:
    """Build the final config for one offered instance, and enforce transport policy.

    ``allow_plaintext`` is never taken from ``offered.config`` (see
    ``_validate_offered_config``) — it is set here, by Guild, only when the
    admin who minted the pairing code consented to it. Raises
    :class:`niuu.domain.services.guild_join.GuildJoinError`-family... actually
    raises the transport policy's own remedy message via ``GuildJoinError``.
    """
    _validate_offered_config(offered.config)
    config = dict(offered.config)
    if offered.ravn_base_url:
        config["ravn_base_url"] = offered.ravn_base_url
    if pairing_allow_plaintext:
        config["allow_plaintext"] = True
    for url in configured_dial_urls(offered.base_url, config):
        reason = insecure_transport_reason(url, allow_plaintext=pairing_allow_plaintext)
        if reason:
            raise GuildJoinError(reason)
    return config


class GuildJoinService:
    """Mints pairing codes and admits/heartbeats/removes joined nodes."""

    def __init__(
        self,
        *,
        pairing_codes: PairingCodeRepository,
        guild_join_repository: GuildJoinRepository,
        nodes: NodeRepository,
        instance_repository: InstanceRepository,
        workload_identity: WorkloadTokenIssuer,
        identity_trust: IdentityTrustConfig,
        pairing_code_ttl_seconds: float,
    ) -> None:
        self._pairing_codes = pairing_codes
        self._guild_join_repository = guild_join_repository
        self._nodes = nodes
        self._instance_repository = instance_repository
        self._workload_identity = workload_identity
        self._identity_trust = identity_trust
        self._pairing_code_ttl_seconds = pairing_code_ttl_seconds

    async def mint_pairing_code(
        self,
        principal: Principal,
        *,
        allow_plaintext: bool = False,
        allow_untrusted_node_auth: bool = False,
    ) -> MintedPairingCode:
        """Mint a single-use pairing code, admin/owner only.

        The code is itself a scoped workload JWT (``scopes=["node_join"]``),
        so the join route is admitted by the existing
        ``require_scope("node_join")`` machinery; this method additionally
        records the code's hash so it can be spent exactly once. Minted with
        an EMPTY roles claim — a pairing code authenticates "this is a valid
        code", never "act as this admin".
        """
        if "volundr:admin" not in principal.roles:
            raise GuildJoinAccessError("Only admins may mint a node pairing code")
        if not self._workload_identity.enabled:
            raise PairingCodeMintingUnavailableError(
                "Cannot mint a node pairing code: workload identity exchange is disabled. "
                "Set workload_identity.enabled: true to use `niuu join`."
            )

        roleless = Principal(
            user_id=principal.user_id,
            email=principal.email,
            tenant_id=principal.tenant_id,
            roles=[],
        )
        issued = self._workload_identity.issue_token(
            principal=roleless,
            workload_subject=principal.user_id,
            workload_name="guild-node-pairing",
            audiences=["volundr-api"],
            token_use=VALKYRIE_BUILD_TOKEN_USE,
            claims={"scopes": [NODE_JOIN_SCOPE]},
        )
        # The JWT's own exp may be longer (workload_identity.token_ttl_seconds,
        # shared with every other scoped credential) — the pairing code's
        # actual, shorter, single-use window is enforced independently by
        # this stored row's own expires_at, which the atomic consume checks.
        expires_at = min(
            datetime.fromtimestamp(issued.expires_at, UTC),
            datetime.now(UTC) + timedelta(seconds=self._pairing_code_ttl_seconds),
        )
        await self._pairing_codes.create(
            code_hash=_hash_code(issued.token),
            created_by=principal.user_id,
            tenant_id=principal.tenant_id,
            allow_plaintext=allow_plaintext,
            allow_untrusted_node_auth=allow_untrusted_node_auth,
            expires_at=expires_at,
        )
        return MintedPairingCode(code=issued.token, expires_at=expires_at)

    async def join(
        self,
        *,
        raw_code: str,
        node_name: str,
        public_key: str,
        node_auth_mode: str,
        instances: list[OfferedInstance],
    ) -> JoinResult:
        """Validate, then atomically consume the code and register the node.

        Every check that does not require a database write happens first —
        a malformed request must not burn a single-use code, and a request
        that fails write-time validation (name/key conflict) leaves the
        code unconsumed too, because the whole write is one transaction
        (see ``GuildJoinRepository.consume_and_register``).
        """
        name = node_name.strip()
        if not name:
            raise GuildJoinError("Node name is required")
        validated_public_key = _validate_public_key(public_key)

        peeked = await self._pairing_codes.peek(_hash_code(raw_code))
        if peeked is None:
            raise PairingCodeInvalidError("Pairing code is invalid, expired, or already used")
        if not peeked.tenant_id:
            raise GuildJoinError("Pairing code has no tenant; registration cannot proceed")
        if (
            self._identity_trust.mode == "oidc"
            and node_auth_mode == "none"
            and not peeked.allow_untrusted_node_auth
        ):
            raise UntrustedNodeError(
                "This Guild requires OIDC identity verification, but the joining node reports "
                "host_auth.mode: none — Guild would forward real user bearer tokens to an "
                "instance that trusts every caller. Mint the pairing code with "
                "allow_untrusted_node_auth to override."
            )

        writes = [
            self._offered_instance_write(
                offered,
                tenant_id=peeked.tenant_id,
                node_name=name,
                allow_plaintext=peeked.allow_plaintext,
            )
            for offered in instances
        ]

        try:
            registration = await self._guild_join_repository.consume_and_register(
                code_hash=_hash_code(raw_code),
                node_name=name,
                public_key=validated_public_key,
                instances=writes,
            )
        except NodeConflictError as exc:
            raise NodeRegistrationConflictError(str(exc)) from exc
        if registration is None:
            raise PairingCodeInvalidError("Pairing code is invalid, expired, or already used")

        _code, node, registered_instances = registration
        return JoinResult(node=node, instances=registered_instances, identity=self._identity_trust)

    def _offered_instance_write(
        self,
        offered: OfferedInstance,
        *,
        tenant_id: str,
        node_name: str,
        allow_plaintext: bool,
    ) -> InstanceWrite:
        config = _resolve_instance_config(offered, pairing_allow_plaintext=allow_plaintext)
        slug = f"node-{uuid4().hex}-{offered.kind.value}"
        return InstanceWrite(
            kind=offered.kind,
            slug=slug,
            name=f"{node_name} ({offered.kind.value})",
            base_url=offered.base_url,
            visibility=InstanceVisibility.TENANT,
            tenant_id=tenant_id,
            config=config,
        )

    async def heartbeat(
        self, node: RegisteredNode, instances: list[OfferedInstance]
    ) -> tuple[RegisteredNode, list[RegisteredInstance]]:
        """Refresh ``last_seen_at`` and re-sync this node's offered instances.

        Matched by ``node_id`` — the real ownership column — never by a
        slug derived from the node's name, so a heartbeat can only ever
        touch rows this exact node already owns.
        """
        refreshed = await self._nodes.touch_heartbeat(node.id)
        if refreshed is None:
            raise GuildJoinError(f"Node {node.id} was removed")
        if not refreshed.tenant_id:
            raise GuildJoinError("Node has no tenant; registration cannot proceed")

        owned = await self._instance_repository.list_for_node(node.id)
        existing_by_kind = {i.kind: i for i in owned}
        registered: list[RegisteredInstance] = []
        for offered in instances:
            config = _resolve_instance_config(
                offered, pairing_allow_plaintext=refreshed.allow_plaintext
            )
            existing = existing_by_kind.get(offered.kind)
            if existing is not None:
                updated = replace(
                    existing,
                    base_url=offered.base_url,
                    config=config,
                    updated_at=datetime.now(UTC),
                )
                registered.append(await self._instance_repository.save_instance(updated))
                continue
            new_instance = RegisteredInstance(
                id=str(uuid4()),
                kind=offered.kind,
                slug=f"node-{uuid4().hex}-{offered.kind.value}",
                name=f"{refreshed.name} ({offered.kind.value})",
                base_url=offered.base_url,
                visibility=InstanceVisibility.TENANT,
                owner_id=None,
                tenant_id=refreshed.tenant_id,
                enabled=True,
                is_default=False,
                config=config,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
                node_id=node.id,
            )
            registered.append(await self._instance_repository.save_instance(new_instance))
        return refreshed, registered

    async def leave(self, node: RegisteredNode) -> None:
        """Deregister a node. Its instances cascade at the database layer."""
        await self._nodes.delete(node.id)

    async def list_nodes(self, principal: Principal) -> list[RegisteredNode]:
        """Admin-only listing of nodes joined under the principal's tenant."""
        if "volundr:admin" not in principal.roles:
            raise GuildJoinAccessError("Only admins may list joined nodes")
        return await self._nodes.list_for_tenant(principal.tenant_id)

    async def revoke_node(self, principal: Principal, node_id: str) -> bool:
        """Admin-only revocation — deletes the node; instances cascade.

        Returns ``False`` when no node with that id exists in the
        principal's tenant (the caller maps that to 404).
        """
        if "volundr:admin" not in principal.roles:
            raise GuildJoinAccessError("Only admins may revoke a joined node")
        node = await self._nodes.get(node_id)
        if node is None or node.tenant_id != principal.tenant_id:
            return False
        await self._nodes.delete(node_id)
        return True
