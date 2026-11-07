"""The registered-host inventory's SQL adapter against a mocked asyncpg pool."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import asyncpg
import pytest

from tests.compute_host_fakes import make_host
from volundr.adapters.outbound.postgres_compute_hosts import PostgresRegisteredHostRepository
from volundr.domain.compute import HostSelector
from volundr.domain.compute_hosts import (
    HostConflictError,
    HostHealth,
    HostPairingCode,
    HostService,
)


def _code(**changes):
    values = {
        "id": uuid4(),
        "code_hash": "a" * 64,
        "pool_id": "hosts",
        "tenant_id": "tenant",
        "created_by": "admin",
        "execution": "host@1",
        "profile": "bm",
        "connector": "direct-ssh",
        "expires_at": datetime.now(UTC) + timedelta(minutes=10),
    }
    values.update(changes)
    return HostPairingCode(**values)


@pytest.fixture
def setup():
    conn = AsyncMock()
    conn.transaction = MagicMock()
    conn.transaction.return_value.__aenter__ = AsyncMock()
    conn.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
    pool = AsyncMock()
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return PostgresRegisteredHostRepository(pool), pool, conn


class TestPairingCodes:
    async def test_create_and_read(self, setup):
        repo, pool, _ = setup
        code = _code(tags=("b", "a"))
        await repo.create_pairing_code(code)
        args = pool.execute.call_args.args
        assert "INSERT INTO compute_host_pairing_codes" in args[0]
        assert args[1:4] == (code.id, code.code_hash, "hosts")
        assert json.loads(args[5])["tags"] == ["a", "b"]

        pool.fetchval.return_value = code.model_dump_json()
        assert await repo.pairing_code(code.code_hash) == code
        assert "consumed_at IS NULL AND revoked_at IS NULL" in pool.fetchval.call_args.args[0]
        pool.fetchval.return_value = None
        assert await repo.pairing_code("b" * 64) is None

        pool.fetch.return_value = [{"data": code.model_dump_json()}]
        assert await repo.pairing_codes("hosts") == [code]

    async def test_revoke(self, setup):
        repo, pool, _ = setup
        pool.execute.return_value = "UPDATE 1"
        assert await repo.revoke_pairing_code("hosts", uuid4()) is True
        pool.execute.return_value = "UPDATE 0"
        assert await repo.revoke_pairing_code("hosts", uuid4()) is False

    async def test_consume_and_register_in_one_transaction(self, setup):
        repo, _, conn = setup
        host = make_host(health=HostHealth.PENDING)
        conn.fetchval.return_value = uuid4()
        assert await repo.consume_and_register("a" * 64, host) == host
        insert = conn.execute.call_args.args
        assert "INSERT INTO compute_hosts" in insert[0]
        assert insert[1:7] == (
            host.id,
            "hosts",
            host.name,
            host.host_public_key,
            "pending",
            "active",
        )

    async def test_an_unusable_code_registers_nothing(self, setup):
        repo, _, conn = setup
        conn.fetchval.return_value = None
        assert await repo.consume_and_register("a" * 64, make_host()) is None
        conn.execute.assert_not_called()

    async def test_a_taken_name_or_key_rolls_back(self, setup):
        repo, _, conn = setup
        conn.fetchval.return_value = uuid4()
        conn.execute.side_effect = asyncpg.UniqueViolationError("dup")
        with pytest.raises(HostConflictError):
            await repo.consume_and_register("a" * 64, make_host())

    async def test_a_raced_tunnel_port_says_to_join_again(self, setup):
        repo, _, conn = setup
        conn.fetchval.return_value = uuid4()
        conflict = asyncpg.UniqueViolationError("dup")
        conflict.constraint_name = "idx_compute_hosts_tunnel_port"
        conn.execute.side_effect = conflict
        with pytest.raises(HostConflictError, match="tunnel port"):
            await repo.consume_and_register("a" * 64, make_host())


class TestHosts:
    async def test_a_host_by_its_key(self, setup):
        repo, pool, _ = setup
        host = make_host()
        pool.fetchval.return_value = host.model_dump_json()
        assert await repo.with_public_key(host.host_public_key) == host
        sql, key = pool.fetchval.call_args.args
        assert "WHERE host_public_key = $1" in sql
        assert key == host.host_public_key
        pool.fetchval.return_value = None
        assert await repo.with_public_key(host.host_public_key) is None

    async def test_one_connection_field_across_every_host(self, setup):
        repo, pool, _ = setup
        pool.fetch.return_value = [{"value": "20000"}, {"value": "20003"}]
        assert await repo.connection_values("tunnel_port") == [20000, 20003]
        sql, field = pool.fetch.call_args.args
        assert "data->'connection' ? $1" in sql
        assert "pool_id" not in sql  # the endpoint's loopback is the Forge's, not a pool's
        assert field == "tunnel_port"

    async def test_reads(self, setup):
        repo, pool, _ = setup
        host = make_host()
        pool.fetchval.return_value = host.model_dump_json()
        assert await repo.get(host.id) == host
        assert await repo.held_by(uuid4()) == host
        pool.fetchval.return_value = None
        assert await repo.get(host.id) is None
        assert await repo.held_by(uuid4()) is None
        pool.fetch.return_value = [{"data": host.model_dump_json()}]
        assert await repo.list("hosts") == [host]

    async def test_save_writes_only_operator_fields(self, setup):
        repo, pool, _ = setup
        host = make_host(tags=("gpu",), service=HostService.DRAINING, findings=("x",))
        pool.execute.return_value = "UPDATE 1"
        await repo.save(host)
        sql, host_id, name, service, operator = pool.execute.call_args.args
        assert "data = data || $4::jsonb" in sql
        assert (host_id, name, service) == (host.id, host.name, "draining")
        assert json.loads(operator) == {
            "name": host.name,
            "tags": ["gpu"],
            "slots": 4,
            "service": "draining",
        }

    async def test_save_errors(self, setup):
        repo, pool, _ = setup
        pool.execute.return_value = "UPDATE 0"
        with pytest.raises(LookupError):
            await repo.save(make_host())
        pool.execute.side_effect = asyncpg.UniqueViolationError("dup")
        with pytest.raises(HostConflictError):
            await repo.save(make_host())

    async def test_record_check(self, setup):
        repo, pool, _ = setup
        host = make_host()
        checked = datetime.now(UTC)
        stored = host.model_copy(
            update={
                "health": HostHealth.QUARANTINED,
                "findings": ("y",),
                "last_checked_at": checked,
            }
        )
        pool.fetchval.return_value = stored.model_dump_json()
        result = await repo.record_check(
            host.id,
            facts={"cpus": 4},
            findings=("y",),
            health=HostHealth.QUARANTINED,
            checked_at=checked,
        )
        assert result == stored
        sql, host_id, health, payload = pool.fetchval.call_args.args
        assert (host_id, health) == (host.id, "quarantined")
        assert json.loads(payload) == {
            "facts": {"cpus": 4},
            "findings": ["y"],
            "health": "quarantined",
            "last_checked_at": checked.isoformat(),
        }
        pool.fetchval.return_value = None
        with pytest.raises(LookupError):
            await repo.record_check(
                host.id, facts={}, findings=(), health=HostHealth.HEALTHY, checked_at=checked
            )

    async def test_claim_returns_an_existing_hold_first(self, setup):
        repo, _, conn = setup
        host = make_host(allocation_id=uuid4())
        conn.fetchval.return_value = host.model_dump_json()
        assert await repo.claim("hosts", host.allocation_id, "bm", HostSelector()) == host
        assert conn.fetchval.await_count == 1

    @pytest.mark.parametrize(
        ("selector", "tags", "match"),
        [
            (HostSelector(), [], "all"),
            (HostSelector(tags=("gpu", "arm64")), ["arm64", "gpu"], "all"),
            (HostSelector(tags=("gpu",), match="any"), ["gpu"], "any"),
        ],
    )
    async def test_claim_filters_by_profile_and_tags(self, setup, selector, tags, match):
        repo, _, conn = setup
        allocation = uuid4()
        host = make_host(allocation_id=allocation)
        conn.fetchval.side_effect = [None, host.model_dump_json()]
        assert await repo.claim("hosts", allocation, "bm", selector) == host
        sql, pool_id, allocation_id, profile, tag_args, match_arg = conn.fetchval.call_args.args
        assert "FOR UPDATE SKIP LOCKED" in sql
        assert "health = 'healthy' AND service = 'active'" in sql
        assert (pool_id, allocation_id, profile, tag_args, match_arg) == (
            "hosts",
            allocation,
            "bm",
            tags,
            match,
        )

    async def test_claim_with_nothing_free(self, setup):
        repo, _, conn = setup
        conn.fetchval.side_effect = [None, None]
        assert await repo.claim("hosts", uuid4(), "bm", HostSelector()) is None

    async def test_release_clears_the_hold(self, setup):
        repo, pool, _ = setup
        allocation = uuid4()
        host = make_host(allocation_id=allocation, health=HostHealth.QUARANTINED)
        await repo.release(allocation, host)
        sql, allocation_id, health, service, data = pool.execute.call_args.args
        assert "SET allocation_id = NULL" in sql
        assert (allocation_id, health, service) == (allocation, "quarantined", "active")
        assert json.loads(data)["allocation_id"] is None

    async def test_delete_only_an_unheld_host(self, setup):
        repo, pool, _ = setup
        pool.execute.return_value = "DELETE 1"
        assert await repo.delete(uuid4()) is True
        assert "allocation_id IS NULL" in pool.execute.call_args.args[0]
        pool.execute.return_value = "DELETE 0"
        assert await repo.delete(uuid4()) is False
