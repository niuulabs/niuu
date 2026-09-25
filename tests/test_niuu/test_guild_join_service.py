"""Tests for the `niuu join` domain service — pairing, join, heartbeat, leave."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta

import pytest

from identity.adapters.authorization import AllowAllAuthorizationAdapter
from niuu.domain.models import (
    InstanceKind,
    PairingCode,
    Principal,
    RegisteredInstance,
    RegisteredNode,
)
from niuu.domain.services.guild_join import (
    GuildJoinAccessError,
    GuildJoinService,
    IdentityTrustConfig,
    OfferedInstance,
    PairingCodeInvalidError,
)
from niuu.domain.services.instances import InstanceService
from niuu.domain.services.token_scope import VALKYRIE_BUILD_TOKEN_USE
from niuu.ports.workload_identity import IssuedWorkloadToken


class InMemoryInstanceRepository:
    """Mirrors the fake in tests/test_niuu/test_instance_service.py."""

    def __init__(self) -> None:
        self.instances: dict[str, RegisteredInstance] = {}
        self.deleted_ids: list[str] = []

    async def list_instances(self, kind: InstanceKind | None = None) -> list[RegisteredInstance]:
        values = list(self.instances.values())
        if kind is None:
            return values
        return [i for i in values if i.kind == kind]

    async def get_instance(self, instance_id: str) -> RegisteredInstance | None:
        return self.instances.get(instance_id)

    async def save_instance(self, instance: RegisteredInstance) -> RegisteredInstance:
        self.instances[instance.id] = instance
        return instance

    async def delete_instance(self, instance_id: str) -> None:
        self.deleted_ids.append(instance_id)
        self.instances.pop(instance_id, None)

    async def record_health(self, *args, **kwargs) -> None:
        raise NotImplementedError


class FakePairingCodeRepository:
    """Single-UPDATE-style atomic consume, with an injectable yield point.

    ``consume_yields`` lets a test force two concurrent ``consume`` calls to
    interleave right between "read" and "write" — the same hazard a real
    ``UPDATE ... WHERE consumed_at IS NULL`` closes by doing both in one
    statement. This fake closes it with an asyncio.Lock instead, so the test
    exercises "exactly one caller ever gets the row back", not the SQL.
    """

    def __init__(self) -> None:
        self._rows: dict[str, PairingCode] = {}
        self._lock = asyncio.Lock()
        self.consume_yields = False

    async def create(self, *, code_hash, created_by, tenant_id, expires_at) -> PairingCode:
        code = PairingCode(
            id=f"code-{len(self._rows) + 1}",
            code_hash=code_hash,
            created_by=created_by,
            tenant_id=tenant_id,
            expires_at=expires_at,
            created_at=datetime.now(UTC),
        )
        self._rows[code_hash] = code
        return code

    async def consume(self, code_hash: str) -> PairingCode | None:
        row = self._rows.get(code_hash)
        if row is None:
            return None
        if self.consume_yields:
            await asyncio.sleep(0)
        async with self._lock:
            row = self._rows.get(code_hash)
            if row is None or row.consumed_at is not None:
                return None
            if row.expires_at <= datetime.now(UTC):
                return None
            consumed = PairingCode(
                id=row.id,
                code_hash=row.code_hash,
                created_by=row.created_by,
                tenant_id=row.tenant_id,
                expires_at=row.expires_at,
                created_at=row.created_at,
                consumed_at=datetime.now(UTC),
            )
            self._rows[code_hash] = consumed
            return consumed

    async def attach_node(self, pairing_code_id: str, node_id: str) -> None:
        for code_hash, row in self._rows.items():
            if row.id == pairing_code_id:
                self._rows[code_hash] = PairingCode(
                    id=row.id,
                    code_hash=row.code_hash,
                    created_by=row.created_by,
                    tenant_id=row.tenant_id,
                    expires_at=row.expires_at,
                    created_at=row.created_at,
                    consumed_at=row.consumed_at,
                    consumed_by_node_id=node_id,
                )


class FakeNodeRepository:
    def __init__(self) -> None:
        self.nodes: dict[str, RegisteredNode] = {}
        self.deleted_ids: list[str] = []

    async def create(self, *, node_id, name, public_key, tenant_id, created_by) -> RegisteredNode:
        node = RegisteredNode(
            id=node_id,
            name=name,
            public_key=public_key,
            tenant_id=tenant_id,
            created_by=created_by,
            created_at=datetime.now(UTC),
        )
        self.nodes[node_id] = node
        return node

    async def get(self, node_id: str) -> RegisteredNode | None:
        return self.nodes.get(node_id)

    async def touch_heartbeat(self, node_id: str) -> RegisteredNode | None:
        node = self.nodes.get(node_id)
        if node is None:
            return None
        refreshed = RegisteredNode(
            id=node.id,
            name=node.name,
            public_key=node.public_key,
            tenant_id=node.tenant_id,
            created_by=node.created_by,
            created_at=node.created_at,
            last_seen_at=datetime.now(UTC),
            last_request_at=node.last_request_at,
        )
        self.nodes[node_id] = refreshed
        return refreshed

    async def record_request(self, node_id: str, *, timestamp: int) -> None:
        node = self.nodes[node_id]
        self.nodes[node_id] = RegisteredNode(
            id=node.id,
            name=node.name,
            public_key=node.public_key,
            tenant_id=node.tenant_id,
            created_by=node.created_by,
            created_at=node.created_at,
            last_seen_at=node.last_seen_at,
            last_request_at=timestamp,
        )

    async def delete(self, node_id: str) -> None:
        self.deleted_ids.append(node_id)
        self.nodes.pop(node_id, None)


class FakeWorkloadIdentity:
    """Deterministic-but-unique-per-call token issuer (no real signing)."""

    def __init__(self, *, enabled: bool = True, ttl_seconds: int = 600) -> None:
        self._enabled = enabled
        self._ttl = ttl_seconds
        self._counter = 0

    @property
    def enabled(self) -> bool:
        return self._enabled

    def issue_token(
        self,
        *,
        principal,
        workload_subject,
        workload_name,
        audiences,
        token_use="",
        claims=None,
    ) -> IssuedWorkloadToken:
        self._counter += 1
        token = f"pairing-token-{self._counter}"
        assert token_use == VALKYRIE_BUILD_TOKEN_USE
        assert claims == {"scopes": ["node_join"]}
        expires_at = int((datetime.now(UTC) + timedelta(seconds=self._ttl)).timestamp())
        return IssuedWorkloadToken(token=token, expires_at=expires_at)


#: Valid base64-encoded raw 32-byte Ed25519 public keys, for join() validation.
_PUBLIC_KEY_A = "D/EffI7pfpn+KOrUw3+4wGiQMEZR/oiF7o+HgdvUuD8="
_PUBLIC_KEY_B = "q313qSWH7jWwbsjvnYAnQDcntJejl9o0WV+XiG0dKfM="


def _identity_trust() -> IdentityTrustConfig:
    return IdentityTrustConfig(mode="oidc", issuers=[{"issuer": "https://idp.example.com"}])


def _admin() -> Principal:
    return Principal(
        user_id="admin-1", email="admin@example.com", tenant_id="tenant-a", roles=["volundr:admin"]
    )


def _developer() -> Principal:
    return Principal(
        user_id="dev-1", email="dev@example.com", tenant_id="tenant-a", roles=["volundr:developer"]
    )


def _service():
    pairing = FakePairingCodeRepository()
    nodes = FakeNodeRepository()
    instance_repo = InMemoryInstanceRepository()
    instance_service = InstanceService(instance_repo, authorization=AllowAllAuthorizationAdapter())
    workload_identity = FakeWorkloadIdentity()
    service = GuildJoinService(
        pairing_codes=pairing,
        nodes=nodes,
        instance_service=instance_service,
        instance_repository=instance_repo,
        workload_identity=workload_identity,
        identity_trust=_identity_trust(),
    )
    return service, pairing, nodes, instance_repo


@pytest.mark.asyncio
async def test_mint_pairing_code_requires_admin() -> None:
    service, *_ = _service()
    with pytest.raises(GuildJoinAccessError):
        await service.mint_pairing_code(_developer())


@pytest.mark.asyncio
async def test_mint_pairing_code_returns_the_workload_token() -> None:
    service, pairing, *_ = _service()
    minted = await service.mint_pairing_code(_admin())
    assert minted.code == "pairing-token-1"
    stored = pairing._rows[hashlib.sha256(minted.code.encode()).hexdigest()]
    assert stored.created_by == "admin-1"
    assert stored.tenant_id == "tenant-a"
    assert stored.consumed_at is None


@pytest.mark.asyncio
async def test_join_registers_node_and_offered_instances() -> None:
    service, *_ = _service()
    minted = await service.mint_pairing_code(_admin())

    result = await service.join(
        raw_code=minted.code,
        node_name="spark-1",
        public_key=_PUBLIC_KEY_A,
        instances=[
            OfferedInstance(kind=InstanceKind.VOLUNDR, base_url="http://127.0.0.1:8080"),
        ],
    )

    assert result.node.name == "spark-1"
    assert result.node.tenant_id == "tenant-a"
    assert result.node.created_by == "admin-1"
    assert len(result.instances) == 1
    assert result.instances[0].config["node_id"] == result.node.id
    assert result.identity.mode == "oidc"


@pytest.mark.asyncio
async def test_join_rejects_an_already_consumed_code() -> None:
    service, *_ = _service()
    minted = await service.mint_pairing_code(_admin())
    await service.join(raw_code=minted.code, node_name="a", public_key=_PUBLIC_KEY_B, instances=[])

    with pytest.raises(PairingCodeInvalidError):
        await service.join(
            raw_code=minted.code, node_name="b", public_key=_PUBLIC_KEY_B, instances=[]
        )


@pytest.mark.asyncio
async def test_join_rejects_an_unknown_code() -> None:
    service, *_ = _service()
    with pytest.raises(PairingCodeInvalidError):
        await service.join(
            raw_code="never-minted", node_name="a", public_key=_PUBLIC_KEY_B, instances=[]
        )


@pytest.mark.asyncio
async def test_join_rejects_an_expired_code() -> None:
    service, pairing, *_ = _service()
    await pairing.create(
        code_hash=hashlib.sha256(b"expired-code").hexdigest(),
        created_by="admin-1",
        tenant_id="tenant-a",
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    with pytest.raises(PairingCodeInvalidError):
        await service.join(
            raw_code="expired-code", node_name="a", public_key=_PUBLIC_KEY_B, instances=[]
        )


@pytest.mark.asyncio
async def test_concurrent_joins_on_the_same_code_only_one_succeeds() -> None:
    service, pairing, nodes, _ = _service()
    minted = await service.mint_pairing_code(_admin())
    pairing.consume_yields = True

    results = await asyncio.gather(
        service.join(raw_code=minted.code, node_name="a", public_key=_PUBLIC_KEY_B, instances=[]),
        service.join(raw_code=minted.code, node_name="b", public_key=_PUBLIC_KEY_B, instances=[]),
        return_exceptions=True,
    )

    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, PairingCodeInvalidError)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert len(nodes.nodes) == 1


@pytest.mark.asyncio
async def test_heartbeat_refreshes_last_seen_and_resyncs_instances() -> None:
    service, *_ = _service()
    minted = await service.mint_pairing_code(_admin())
    joined = await service.join(
        raw_code=minted.code, node_name="spark-1", public_key=_PUBLIC_KEY_B, instances=[]
    )
    assert joined.node.last_seen_at is None

    refreshed, instances = await service.heartbeat(
        joined.node,
        [OfferedInstance(kind=InstanceKind.RAVN, base_url="http://127.0.0.1:9000")],
    )

    assert refreshed.last_seen_at is not None
    assert len(instances) == 1
    assert instances[0].kind == InstanceKind.RAVN


@pytest.mark.asyncio
async def test_leave_removes_only_this_nodes_instances() -> None:
    service, _, nodes, instance_repo = _service()
    minted = await service.mint_pairing_code(_admin())
    joined = await service.join(
        raw_code=minted.code,
        node_name="spark-1",
        public_key=_PUBLIC_KEY_B,
        instances=[OfferedInstance(kind=InstanceKind.VOLUNDR, base_url="http://127.0.0.1:8080")],
    )
    # An unrelated, manually seeded instance must survive this node's leave.
    from datetime import UTC as _UTC

    from niuu.domain.models import InstanceVisibility

    unrelated = RegisteredInstance(
        id="unrelated-1",
        kind=InstanceKind.VOLUNDR,
        slug="unrelated",
        name="Unrelated",
        base_url="http://127.0.0.1:7000",
        visibility=InstanceVisibility.SYSTEM,
        owner_id=None,
        tenant_id=None,
        enabled=True,
        is_default=False,
        config={},
        created_at=datetime.now(_UTC),
        updated_at=datetime.now(_UTC),
    )
    await instance_repo.save_instance(unrelated)

    await service.leave(joined.node)

    assert joined.node.id not in nodes.nodes
    assert "unrelated-1" in instance_repo.instances
    assert not any(
        i.config.get("node_id") == joined.node.id for i in instance_repo.instances.values()
    )
