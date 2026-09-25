"""Tests for the `niuu join` domain service — pairing, join, heartbeat, leave.

Blockers this file specifically guards against regressing (see the security
review that added them):
  1. Instance hijack — a node cannot adopt another owner's instance by name.
  2. Replay watermark races (covered in test_node_signature.py instead).
  3. Join atomicity — a failed join must not burn the pairing code or leave
     a half-registered node.
  4. Admin revocation cascades to the node's instances.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from niuu.domain.models import (
    InstanceKind,
    InstanceVisibility,
    PairingCode,
    Principal,
    RegisteredInstance,
    RegisteredNode,
)
from niuu.domain.services.guild_join import (
    ALLOWED_OFFERED_CONFIG_KEYS,
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
from niuu.domain.services.token_scope import VALKYRIE_BUILD_TOKEN_USE
from niuu.ports.guild_join_repository import NodeConflictError
from niuu.ports.workload_identity import IssuedWorkloadToken


def _public_key() -> str:
    return base64.b64encode(os.urandom(32)).decode("ascii")


class FakeGuildBackend:
    """One in-memory fake standing in for PairingCodeRepository,
    GuildJoinRepository, NodeRepository, and InstanceRepository — all four
    share this object's state, mirroring how the real Postgres adapters
    share one database and how ``consume_and_register`` is genuinely atomic.
    """

    def __init__(self) -> None:
        self._codes: dict[str, PairingCode] = {}
        self._nodes: dict[str, RegisteredNode] = {}
        self._nodes_by_name: dict[tuple[str, str], str] = {}
        self._nodes_by_pubkey: dict[str, str] = {}
        self._instances: dict[str, RegisteredInstance] = {}
        self._lock = asyncio.Lock()
        self.consume_yields = False

    # -- PairingCodeRepository -------------------------------------------------

    async def create(
        self,
        *,
        code_hash,
        created_by,
        tenant_id,
        allow_plaintext,
        allow_untrusted_node_auth,
        expires_at,
    ) -> PairingCode:
        code = PairingCode(
            id=f"code-{len(self._codes) + 1}",
            code_hash=code_hash,
            created_by=created_by,
            tenant_id=tenant_id,
            expires_at=expires_at,
            created_at=datetime.now(UTC),
            allow_plaintext=allow_plaintext,
            allow_untrusted_node_auth=allow_untrusted_node_auth,
        )
        self._codes[code_hash] = code
        return code

    async def peek(self, code_hash: str) -> PairingCode | None:
        return self._codes.get(code_hash)

    # -- GuildJoinRepository -----------------------------------------------

    async def consume_and_register(self, *, code_hash, node_name, public_key, instances):
        if self.consume_yields:
            await asyncio.sleep(0)
        async with self._lock:
            row = self._codes.get(code_hash)
            if row is None or row.consumed_at is not None or row.expires_at <= datetime.now(UTC):
                return None
            name_key = (row.tenant_id, node_name)
            if name_key in self._nodes_by_name or public_key in self._nodes_by_pubkey:
                raise NodeConflictError(
                    f"Node name {node_name!r} or public key is already registered"
                )
            node_id = str(uuid4())
            node = RegisteredNode(
                id=node_id,
                name=node_name,
                public_key=public_key,
                tenant_id=row.tenant_id,
                created_by=row.created_by,
                created_at=datetime.now(UTC),
                allow_plaintext=row.allow_plaintext,
            )
            self._nodes[node_id] = node
            self._nodes_by_name[name_key] = node_id
            self._nodes_by_pubkey[public_key] = node_id
            consumed = replace(row, consumed_at=datetime.now(UTC), consumed_by_node_id=node_id)
            self._codes[code_hash] = consumed

            saved_instances: list[RegisteredInstance] = []
            for write in instances:
                now = datetime.now(UTC)
                instance = RegisteredInstance(
                    id=str(uuid4()),
                    kind=write.kind,
                    slug=write.slug,
                    name=write.name,
                    base_url=write.base_url,
                    visibility=write.visibility,
                    owner_id=None,
                    tenant_id=write.tenant_id,
                    enabled=True,
                    is_default=False,
                    config=write.config,
                    created_at=now,
                    updated_at=now,
                    node_id=node_id,
                )
                self._instances[instance.id] = instance
                saved_instances.append(instance)
            return consumed, node, saved_instances

    # -- NodeRepository ------------------------------------------------------

    async def get(self, node_id: str) -> RegisteredNode | None:
        return self._nodes.get(node_id)

    async def list_for_tenant(self, tenant_id: str) -> list[RegisteredNode]:
        return [n for n in self._nodes.values() if n.tenant_id == tenant_id]

    async def touch_heartbeat(self, node_id: str) -> RegisteredNode | None:
        node = self._nodes.get(node_id)
        if node is None:
            return None
        refreshed = replace(node, last_seen_at=datetime.now(UTC))
        self._nodes[node_id] = refreshed
        return refreshed

    async def try_advance_watermark(self, node_id: str, *, timestamp_ms: int) -> bool:
        node = self._nodes.get(node_id)
        if node is None:
            return False
        if node.last_request_at is not None and node.last_request_at >= timestamp_ms:
            return False
        self._nodes[node_id] = replace(node, last_request_at=timestamp_ms)
        return True

    async def delete(self, node_id: str) -> None:
        self._nodes.pop(node_id, None)
        for instance_id in [i.id for i in self._instances.values() if i.node_id == node_id]:
            self._instances.pop(instance_id, None)

    # -- InstanceRepository ---------------------------------------------------

    async def list_for_node(self, node_id: str) -> list[RegisteredInstance]:
        return [i for i in self._instances.values() if i.node_id == node_id]

    async def list_instances(self, kind: InstanceKind | None = None) -> list[RegisteredInstance]:
        values = list(self._instances.values())
        return values if kind is None else [i for i in values if i.kind == kind]

    async def get_instance(self, instance_id: str) -> RegisteredInstance | None:
        return self._instances.get(instance_id)

    async def save_instance(self, instance: RegisteredInstance) -> RegisteredInstance:
        self._instances[instance.id] = instance
        return instance

    async def delete_instance(self, instance_id: str) -> None:
        self._instances.pop(instance_id, None)

    async def record_health(self, *args, **kwargs) -> None:
        raise NotImplementedError


class FakeWorkloadIdentity:
    """Deterministic-but-unique-per-call token issuer (no real signing)."""

    def __init__(self, *, enabled: bool = True, ttl_seconds: int = 600) -> None:
        self._enabled = enabled
        self._ttl = ttl_seconds
        self._counter = 0
        self.issued_roles: list[list[str]] = []

    @property
    def enabled(self) -> bool:
        return self._enabled

    def issue_token(
        self, *, principal, workload_subject, workload_name, audiences, token_use="", claims=None
    ) -> IssuedWorkloadToken:
        self._counter += 1
        token = f"pairing-token-{self._counter}"
        self.issued_roles.append(list(principal.roles))
        assert token_use == VALKYRIE_BUILD_TOKEN_USE
        assert claims == {"scopes": ["node_join"]}
        expires_at = int((datetime.now(UTC) + timedelta(seconds=self._ttl)).timestamp())
        return IssuedWorkloadToken(token=token, expires_at=expires_at)


def _identity_trust(mode: str = "oidc") -> IdentityTrustConfig:
    return IdentityTrustConfig(mode=mode, issuers=[{"issuer": "https://idp.example.com"}])


def _admin(tenant_id: str = "tenant-a") -> Principal:
    return Principal(
        user_id="admin-1", email="admin@example.com", tenant_id=tenant_id, roles=["volundr:admin"]
    )


def _developer() -> Principal:
    return Principal(
        user_id="dev-1", email="dev@example.com", tenant_id="tenant-a", roles=["volundr:developer"]
    )


def _service(*, identity_trust_mode: str = "oidc", workload_identity=None, pairing_code_ttl=600.0):
    backend = FakeGuildBackend()
    workload_identity = workload_identity or FakeWorkloadIdentity()
    service = GuildJoinService(
        pairing_codes=backend,
        guild_join_repository=backend,
        nodes=backend,
        instance_repository=backend,
        workload_identity=workload_identity,
        identity_trust=_identity_trust(identity_trust_mode),
        pairing_code_ttl_seconds=pairing_code_ttl,
    )
    return service, backend, workload_identity


async def _mint_and_join(
    service, backend, *, node_name="spark-1", instances=None, node_auth_mode="oidc"
):
    minted = await service.mint_pairing_code(_admin())
    return await service.join(
        raw_code=minted.code,
        node_name=node_name,
        public_key=_public_key(),
        node_auth_mode=node_auth_mode,
        instances=instances or [],
    )


@pytest.mark.asyncio
async def test_mint_pairing_code_requires_admin() -> None:
    service, *_ = _service()
    with pytest.raises(GuildJoinAccessError):
        await service.mint_pairing_code(_developer())


@pytest.mark.asyncio
async def test_mint_pairing_code_is_unavailable_when_workload_identity_disabled() -> None:
    service, *_ = _service(workload_identity=FakeWorkloadIdentity(enabled=False))
    with pytest.raises(PairingCodeMintingUnavailableError, match="workload identity"):
        await service.mint_pairing_code(_admin())


@pytest.mark.asyncio
async def test_mint_pairing_code_strips_the_admins_roles() -> None:
    service, _backend, workload_identity = _service()
    await service.mint_pairing_code(_admin())
    assert workload_identity.issued_roles == [[]]


@pytest.mark.asyncio
async def test_mint_pairing_code_ttl_is_capped_by_the_dedicated_config_knob() -> None:
    service, backend, _wi = _service(
        workload_identity=FakeWorkloadIdentity(ttl_seconds=900), pairing_code_ttl=5.0
    )
    minted = await service.mint_pairing_code(_admin())
    assert minted.expires_at <= datetime.now(UTC) + timedelta(seconds=6)


# --- Blocker 1: instance hijack --------------------------------------------


@pytest.mark.asyncio
async def test_join_registers_node_and_offered_instances() -> None:
    service, backend, _wi = _service()
    result = await _mint_and_join(
        service,
        backend,
        instances=[OfferedInstance(kind=InstanceKind.VOLUNDR, base_url="http://127.0.0.1:8080")],
    )
    assert result.node.name == "spark-1"
    assert result.node.tenant_id == "tenant-a"
    assert len(result.instances) == 1
    assert result.instances[0].node_id == result.node.id


@pytest.mark.asyncio
async def test_a_node_cannot_hijack_an_existing_instance_by_choosing_its_slug() -> None:
    """The exact attack the review flagged: a node named to collide with an
    existing admin-registered instance's slug must never adopt that row."""
    service, backend, _wi = _service()
    victim = RegisteredInstance(
        id="victim-1",
        kind=InstanceKind.VOLUNDR,
        slug="prod-volundr",
        name="Production Forge",
        base_url="https://prod.example.com",
        visibility=InstanceVisibility.SYSTEM,
        owner_id=None,
        tenant_id="tenant-a",
        enabled=True,
        is_default=False,
        config={},
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        node_id=None,
    )
    await backend.save_instance(victim)

    result = await _mint_and_join(
        service,
        backend,
        node_name="prod",
        instances=[
            OfferedInstance(kind=InstanceKind.VOLUNDR, base_url="https://attacker.example.com")
        ],
    )

    # The victim row is untouched: different id, base_url, and no node_id.
    unchanged = await backend.get_instance("victim-1")
    assert unchanged.base_url == "https://prod.example.com"
    assert unchanged.node_id is None
    assert unchanged.slug == "prod-volundr"
    # The attacker's own instance is a separate row, keyed by the new node's id.
    assert len(result.instances) == 1
    assert result.instances[0].id != "victim-1"
    assert result.instances[0].node_id == result.node.id


@pytest.mark.asyncio
async def test_heartbeat_only_ever_touches_this_nodes_own_instances() -> None:
    service, backend, _wi = _service()
    joined = await _mint_and_join(
        service,
        backend,
        instances=[OfferedInstance(kind=InstanceKind.VOLUNDR, base_url="http://127.0.0.1:8080")],
    )
    other_admin_instance = RegisteredInstance(
        id="other-1",
        kind=InstanceKind.VOLUNDR,
        slug="other-volundr",
        name="Other",
        base_url="https://other.example.com",
        visibility=InstanceVisibility.SYSTEM,
        owner_id=None,
        tenant_id="tenant-a",
        enabled=True,
        is_default=False,
        config={},
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        node_id=None,
    )
    await backend.save_instance(other_admin_instance)

    await service.heartbeat(
        joined.node,
        [OfferedInstance(kind=InstanceKind.VOLUNDR, base_url="http://127.0.0.1:9999")],
    )

    untouched = await backend.get_instance("other-1")
    assert untouched.base_url == "https://other.example.com"


@pytest.mark.asyncio
async def test_offered_config_rejects_keys_outside_the_allowlist() -> None:
    service, backend, _wi = _service()
    with pytest.raises(GuildJoinError, match="allow_plaintext"):
        await _mint_and_join(
            service,
            backend,
            instances=[
                OfferedInstance(
                    kind=InstanceKind.VOLUNDR,
                    base_url="https://x.example.com",
                    config={"allow_plaintext": True},
                )
            ],
        )


@pytest.mark.asyncio
async def test_offered_config_rejects_transport_embedded() -> None:
    service, backend, _wi = _service()
    with pytest.raises(GuildJoinError):
        await _mint_and_join(
            service,
            backend,
            instances=[
                OfferedInstance(
                    kind=InstanceKind.VOLUNDR,
                    base_url="https://x.example.com",
                    config={"transport": "embedded"},
                )
            ],
        )


def test_allowed_offered_config_keys_excludes_transport_and_plaintext() -> None:
    assert "allow_plaintext" not in ALLOWED_OFFERED_CONFIG_KEYS
    assert "transport" not in ALLOWED_OFFERED_CONFIG_KEYS


@pytest.mark.asyncio
async def test_plaintext_requires_pairing_code_consent() -> None:
    service, backend, _wi = _service()
    minted = await service.mint_pairing_code(_admin())  # allow_plaintext defaults False
    with pytest.raises(GuildJoinError):
        await service.join(
            raw_code=minted.code,
            node_name="spark-1",
            public_key=_public_key(),
            node_auth_mode="oidc",
            instances=[OfferedInstance(kind=InstanceKind.VOLUNDR, base_url="http://10.0.0.5:8080")],
        )


@pytest.mark.asyncio
async def test_plaintext_allowed_when_pairing_code_consents() -> None:
    service, backend, _wi = _service()
    minted = await service.mint_pairing_code(_admin(), allow_plaintext=True)
    result = await service.join(
        raw_code=minted.code,
        node_name="spark-1",
        public_key=_public_key(),
        node_auth_mode="oidc",
        instances=[OfferedInstance(kind=InstanceKind.VOLUNDR, base_url="http://10.0.0.5:8080")],
    )
    assert result.instances[0].config["allow_plaintext"] is True


@pytest.mark.asyncio
async def test_untrusted_node_auth_refused_without_consent() -> None:
    service, backend, _wi = _service(identity_trust_mode="oidc")
    minted = await service.mint_pairing_code(_admin())
    with pytest.raises(UntrustedNodeError):
        await service.join(
            raw_code=minted.code,
            node_name="spark-1",
            public_key=_public_key(),
            node_auth_mode="none",
            instances=[],
        )


@pytest.mark.asyncio
async def test_untrusted_node_auth_allowed_with_explicit_consent() -> None:
    service, backend, _wi = _service(identity_trust_mode="oidc")
    minted = await service.mint_pairing_code(_admin(), allow_untrusted_node_auth=True)
    result = await service.join(
        raw_code=minted.code,
        node_name="spark-1",
        public_key=_public_key(),
        node_auth_mode="none",
        instances=[],
    )
    assert result.node.name == "spark-1"


@pytest.mark.asyncio
async def test_none_mode_guild_does_not_care_about_node_auth_mode() -> None:
    service, backend, _wi = _service(identity_trust_mode="none")
    minted = await service.mint_pairing_code(_admin())
    result = await service.join(
        raw_code=minted.code,
        node_name="spark-1",
        public_key=_public_key(),
        node_auth_mode="none",
        instances=[],
    )
    assert result.node.name == "spark-1"


# --- Blocker 3: join atomicity ----------------------------------------------


@pytest.mark.asyncio
async def test_join_rejects_an_already_consumed_code() -> None:
    service, backend, _wi = _service()
    minted = await service.mint_pairing_code(_admin())
    await service.join(
        raw_code=minted.code,
        node_name="a",
        public_key=_public_key(),
        node_auth_mode="oidc",
        instances=[],
    )
    with pytest.raises(PairingCodeInvalidError):
        await service.join(
            raw_code=minted.code,
            node_name="b",
            public_key=_public_key(),
            node_auth_mode="oidc",
            instances=[],
        )


@pytest.mark.asyncio
async def test_join_rejects_an_unknown_code() -> None:
    service, backend, _wi = _service()
    with pytest.raises(PairingCodeInvalidError):
        await service.join(
            raw_code="never-minted",
            node_name="a",
            public_key=_public_key(),
            node_auth_mode="oidc",
            instances=[],
        )


@pytest.mark.asyncio
async def test_join_rejects_an_expired_code() -> None:
    service, backend, _wi = _service()
    await backend.create(
        code_hash=hashlib.sha256(b"expired-code").hexdigest(),
        created_by="admin-1",
        tenant_id="tenant-a",
        allow_plaintext=False,
        allow_untrusted_node_auth=False,
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    with pytest.raises(PairingCodeInvalidError):
        await service.join(
            raw_code="expired-code",
            node_name="a",
            public_key=_public_key(),
            node_auth_mode="oidc",
            instances=[],
        )


@pytest.mark.asyncio
async def test_a_malformed_request_does_not_consume_the_code() -> None:
    """Validation (name/key format, config allowlist, transport) happens
    before the atomic consume — a bad request must leave the code usable."""
    service, backend, _wi = _service()
    minted = await service.mint_pairing_code(_admin())

    with pytest.raises(GuildJoinError):
        await service.join(
            raw_code=minted.code,
            node_name="",
            public_key=_public_key(),
            node_auth_mode="oidc",
            instances=[],
        )

    # The code still works — it was never consumed by the failed attempt.
    result = await service.join(
        raw_code=minted.code,
        node_name="spark-1",
        public_key=_public_key(),
        node_auth_mode="oidc",
        instances=[],
    )
    assert result.node.name == "spark-1"


@pytest.mark.asyncio
async def test_a_name_or_key_conflict_does_not_burn_the_code() -> None:
    """NodeConflictError rolls back the whole transaction, including the
    pairing-code consumption — a legitimate retry must still work."""
    service, backend, _wi = _service()
    existing_key = _public_key()
    await _mint_and_join(service, backend, node_name="taken-name", instances=[])

    minted = await service.mint_pairing_code(_admin())
    with pytest.raises(NodeRegistrationConflictError):
        await service.join(
            raw_code=minted.code,
            node_name="taken-name",
            public_key=_public_key(),
            node_auth_mode="oidc",
            instances=[],
        )

    # The code was NOT consumed by the conflicting attempt — retry with a
    # different name succeeds using the SAME code.
    result = await service.join(
        raw_code=minted.code,
        node_name="a-different-name",
        public_key=existing_key,
        node_auth_mode="oidc",
        instances=[],
    )
    assert result.node.name == "a-different-name"


@pytest.mark.asyncio
async def test_concurrent_joins_on_the_same_code_only_one_succeeds() -> None:
    service, backend, _wi = _service()
    minted = await service.mint_pairing_code(_admin())
    backend.consume_yields = True

    results = await asyncio.gather(
        service.join(
            raw_code=minted.code,
            node_name="a",
            public_key=_public_key(),
            node_auth_mode="oidc",
            instances=[],
        ),
        service.join(
            raw_code=minted.code,
            node_name="b",
            public_key=_public_key(),
            node_auth_mode="oidc",
            instances=[],
        ),
        return_exceptions=True,
    )

    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, PairingCodeInvalidError)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert len(backend._nodes) == 1


# --- heartbeat / leave -------------------------------------------------------


@pytest.mark.asyncio
async def test_heartbeat_refreshes_last_seen_and_resyncs_instances() -> None:
    service, backend, _wi = _service()
    joined = await _mint_and_join(service, backend, instances=[])
    assert joined.node.last_seen_at is None

    refreshed, instances = await service.heartbeat(
        joined.node,
        [OfferedInstance(kind=InstanceKind.RAVN, base_url="http://127.0.0.1:9000")],
    )

    assert refreshed.last_seen_at is not None
    assert len(instances) == 1
    assert instances[0].kind == InstanceKind.RAVN


@pytest.mark.asyncio
async def test_leave_removes_the_node() -> None:
    service, backend, _wi = _service()
    joined = await _mint_and_join(
        service,
        backend,
        instances=[OfferedInstance(kind=InstanceKind.VOLUNDR, base_url="http://127.0.0.1:8080")],
    )

    await service.leave(joined.node)

    assert await backend.get(joined.node.id) is None


# --- Blocker 4: admin listing / revocation -----------------------------------


@pytest.mark.asyncio
async def test_list_nodes_requires_admin() -> None:
    service, backend, _wi = _service()
    with pytest.raises(GuildJoinAccessError):
        await service.list_nodes(_developer())


@pytest.mark.asyncio
async def test_list_nodes_scopes_to_the_principals_tenant() -> None:
    service, backend, _wi = _service()
    await _mint_and_join(service, backend, node_name="mine")
    other_backend_admin = _admin(tenant_id="tenant-b")
    # Mint under a different tenant to prove cross-tenant isolation.
    minted = await service.mint_pairing_code(other_backend_admin)
    await service.join(
        raw_code=minted.code,
        node_name="theirs",
        public_key=_public_key(),
        node_auth_mode="oidc",
        instances=[],
    )

    nodes = await service.list_nodes(_admin(tenant_id="tenant-a"))
    assert [n.name for n in nodes] == ["mine"]


@pytest.mark.asyncio
async def test_revoke_node_requires_admin() -> None:
    service, backend, _wi = _service()
    joined = await _mint_and_join(service, backend)
    with pytest.raises(GuildJoinAccessError):
        await service.revoke_node(_developer(), joined.node.id)


@pytest.mark.asyncio
async def test_revoke_node_cascades_to_its_instances() -> None:
    service, backend, _wi = _service()
    joined = await _mint_and_join(
        service,
        backend,
        instances=[OfferedInstance(kind=InstanceKind.VOLUNDR, base_url="http://127.0.0.1:8080")],
    )
    instance_id = joined.instances[0].id

    revoked = await service.revoke_node(_admin(), joined.node.id)

    assert revoked is True
    assert await backend.get(joined.node.id) is None
    assert await backend.get_instance(instance_id) is None


@pytest.mark.asyncio
async def test_revoke_node_returns_false_for_a_different_tenants_node() -> None:
    service, backend, _wi = _service()
    joined = await _mint_and_join(service, backend)

    revoked = await service.revoke_node(_admin(tenant_id="tenant-other"), joined.node.id)

    assert revoked is False
    assert await backend.get(joined.node.id) is not None


@pytest.mark.asyncio
async def test_revoke_node_returns_false_for_an_unknown_node() -> None:
    service, backend, _wi = _service()
    assert await service.revoke_node(_admin(), "does-not-exist") is False
