"""Real PostgreSQL proof that a host joins with its pairing code, exactly once."""

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from tests.compute_host_fakes import make_host
from volundr.adapters.outbound.postgres_compute_hosts import PostgresRegisteredHostRepository
from volundr.domain.compute_hosts import HostPairingCode

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


def _code(pool_id: str) -> HostPairingCode:
    return HostPairingCode(
        id=uuid4(),
        code_hash=hashlib.sha256(uuid4().bytes).hexdigest(),
        pool_id=pool_id,
        tenant_id="tenant",
        created_by="admin",
        execution="host@1",
        profile="bm",
        connector="direct-ssh",
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )


async def test_a_code_registers_its_host_once_and_records_who_used_it(db_pool):
    pool_id = f"join-{uuid4().hex[:8]}"
    repository = PostgresRegisteredHostRepository(db_pool)
    code = _code(pool_id)
    await repository.create_pairing_code(code)
    host = make_host(pool_id=pool_id, name=f"h{uuid4().hex[:8]}")
    try:
        joined = await repository.consume_and_register(code.code_hash, host)

        assert joined is not None and joined.id == host.id
        row = await db_pool.fetchrow(
            "SELECT consumed_by_host_id, data FROM compute_host_pairing_codes WHERE id = $1",
            code.id,
        )
        assert row["consumed_by_host_id"] == host.id
        assert HostPairingCode.model_validate_json(row["data"]).consumed_by_host_id == host.id
        # Single use: the same code registers nothing more.
        again = make_host(pool_id=pool_id, name=f"h{uuid4().hex[:8]}")
        assert await repository.consume_and_register(code.code_hash, again) is None
    finally:
        await db_pool.execute("DELETE FROM compute_hosts WHERE pool_id = $1", pool_id)
        await db_pool.execute("DELETE FROM compute_host_pairing_codes WHERE pool_id = $1", pool_id)
