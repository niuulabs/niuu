"""Real PostgreSQL proof of concurrent admission and allocation operation locking."""

import asyncio
from uuid import uuid4

import pytest

from volundr.adapters.outbound.postgres_compute_leases import PostgresComputeLeaseRepository
from volundr.domain.compute import (
    ComputeCapacityError,
    ComputeLease,
    ComputeLeaseBusyError,
    LeaseState,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


def claim(pool_id, session_id=None):
    return ComputeLease(
        id=uuid4(),
        pool_id=pool_id,
        session_id=session_id or uuid4(),
        tenant_id="test-tenant",
        owner_id="test-owner",
        profile="small",
        request_fingerprint="test-bootstrap-hash",
    )


async def test_concurrent_admission_enforces_pool_budget_and_retry_identity(db_pool):
    pool_id = f"test-{uuid4()}"
    repo = PostgresComputeLeaseRepository(db_pool)
    try:
        results = await asyncio.gather(
            *(repo.reserve(claim(pool_id), 2) for _ in range(10)), return_exceptions=True
        )
        leases = [result for result in results if isinstance(result, ComputeLease)]
        assert len(leases) == 2
        assert sum(isinstance(result, ComputeCapacityError) for result in results) == 8
        retries = await asyncio.gather(
            *(repo.reserve(claim(pool_id, leases[0].session_id), 2) for _ in range(5))
        )
        assert {lease.id for lease in retries} == {leases[0].id}
        async with repo.operation(leases[0].id):
            await repo.save(leases[0].model_copy(update={"state": LeaseState.DRAINING}))
        with pytest.raises(ComputeCapacityError):
            await repo.reserve(claim(pool_id), 2)
        async with repo.operation(leases[0].id):
            await repo.save(leases[0].model_copy(update={"state": LeaseState.RELEASED}))
        assert (await repo.reserve(claim(pool_id), 2)).id not in {lease.id for lease in leases}
    finally:
        await db_pool.execute("DELETE FROM compute_leases WHERE pool_id = $1", pool_id)


async def test_operation_lock_is_shared_by_independent_repository_instances(db_pool):
    first, second = PostgresComputeLeaseRepository(db_pool), PostgresComputeLeaseRepository(db_pool)
    lease = await first.reserve(claim(f"test-{uuid4()}"), 1)
    try:
        async with first.operation(lease.id):
            with pytest.raises(ComputeLeaseBusyError):
                async with second.operation(lease.id):
                    pytest.fail("Two controllers owned the same allocation")
        async with second.operation(lease.id):
            await second.save(lease.model_copy(update={"state": LeaseState.DRAINING}))
        assert (await first.get(lease.id)).state == LeaseState.DRAINING
    finally:
        await db_pool.execute("DELETE FROM compute_leases WHERE id = $1", lease.id)


async def test_standby_target_and_binding_are_atomic_across_controllers(db_pool):
    from volundr.domain.compute import ComputePoolPolicy

    pool_id = f"warm-{uuid4()}"
    repos = [PostgresComputeLeaseRepository(db_pool) for _ in range(8)]
    policy = ComputePoolPolicy(profile="small", max_machines=2, warm_min=1, max_provisioning=2)
    await repos[0].set_policy(pool_id, policy)
    try:
        results = await asyncio.gather(
            *(
                repo.reserve(claim(pool_id).model_copy(update={"session_id": None}), 100)
                for repo in repos
            ),
            return_exceptions=True,
        )
        spares = [result for result in results if isinstance(result, ComputeLease)]
        assert len(spares) == 1
        spare = spares[0].model_copy(update={"state": LeaseState.IDLE})
        async with repos[0].operation(spare.id):
            await repos[0].save(spare)

        async def bind(repo):
            async with repo.operation(spare.id):
                return await repo.bind(
                    spare.model_copy(update={"session_id": uuid4(), "state": LeaseState.READY})
                )

        results = await asyncio.gather(*(bind(repo) for repo in repos), return_exceptions=True)
        assert sum(isinstance(result, ComputeLease) for result in results) == 1
        assert sum(isinstance(result, ComputeLeaseBusyError) for result in results) == 7
        await repos[0].set_policy(pool_id, policy.model_copy(update={"paused": True}))
        with pytest.raises(ComputeCapacityError):
            await repos[1].reserve(claim(pool_id), 100)
    finally:
        await db_pool.execute("DELETE FROM compute_leases WHERE pool_id = $1", pool_id)
        await db_pool.execute("DELETE FROM compute_pools WHERE pool_id = $1", pool_id)
