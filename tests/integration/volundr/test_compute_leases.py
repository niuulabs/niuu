"""Real PostgreSQL proof of concurrent admission, slot placement and operation locking."""

import asyncio
from uuid import uuid4

import asyncpg
import pytest

from volundr.adapters.outbound.postgres_compute_leases import PostgresComputeLeaseRepository
from volundr.domain.compute import (
    ComputeCapacityError,
    ComputeLease,
    ComputeLeaseBusyError,
    ComputePoolFullError,
    ComputePoolPolicy,
    ComputeSlot,
    LeaseState,
    SlotLease,
    SlotState,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


def machine(pool_id, slots=1, **update):
    return ComputeLease(
        id=uuid4(),
        pool_id=pool_id,
        profile="small",
        request_fingerprint="test-bootstrap-hash",
        slots=slots,
        **update,
    )


def slot(lease, session_id=None, index=0, **update):
    return ComputeSlot(
        id=uuid4(),
        allocation_id=lease.id,
        slot=index,
        pool_id=lease.pool_id,
        session_id=session_id or uuid4(),
        tenant_id="test-tenant",
        owner_id="test-owner",
        **update,
    )


def claim(pool_id, session_id=None):
    lease = machine(pool_id)
    return lease, slot(lease, session_id)


async def clean(db_pool, pool_id):
    # compute_slots rows follow their machine (ON DELETE CASCADE).
    await db_pool.execute("DELETE FROM compute_leases WHERE pool_id = $1", pool_id)
    await db_pool.execute("DELETE FROM compute_pools WHERE pool_id = $1", pool_id)


async def reserve_claim(repo, pool_id, limit, session_id=None):
    lease, binding = claim(pool_id, session_id)
    return await repo.reserve(lease, limit, binding)


async def idle_spare(db_pool, pool_id, slots=1):
    """An empty, warm machine, written past admission so the policy under test stays simple."""
    spare = machine(pool_id, slots=slots, state=LeaseState.IDLE)
    await db_pool.execute(
        "INSERT INTO compute_leases (id, pool_id, session_id, state, data) "
        "VALUES ($1, $2, NULL, $3, $4::jsonb)",
        spare.id,
        pool_id,
        spare.state.value,
        spare.model_dump_json(),
    )
    return spare


async def test_concurrent_admission_enforces_pool_budget_and_retry_identity(db_pool):
    pool_id = f"test-{uuid4()}"
    repo = PostgresComputeLeaseRepository(db_pool)
    try:
        results = await asyncio.gather(
            *(reserve_claim(repo, pool_id, 2) for _ in range(10)), return_exceptions=True
        )
        leases = [result for result in results if isinstance(result, tuple)]
        assert len(leases) == 2
        assert sum(isinstance(result, ComputePoolFullError) for result in results) == 8
        first, first_slot = leases[0]
        retries = await asyncio.gather(
            *(reserve_claim(repo, pool_id, 2, first_slot.session_id) for _ in range(5))
        )
        assert {lease.id for lease, _ in retries} == {first.id}
        async with repo.operation(first.id):
            await repo.save(first.model_copy(update={"state": LeaseState.DRAINING}))
        with pytest.raises(ComputeCapacityError):
            await reserve_claim(repo, pool_id, 2)
        async with repo.operation(first.id):
            await repo.save(first.model_copy(update={"state": LeaseState.RELEASED}))
        # Releasing the machine retires its live binding in the same commit.
        assert (await repo.get_slot(first_slot.id)).state is SlotState.RETIRED
        assert await repo.active_for_session(pool_id, first_slot.session_id) is None
        again, _ = await reserve_claim(repo, pool_id, 2)
        assert again.id not in {lease.id for lease, _ in leases}
    finally:
        await clean(db_pool, pool_id)


async def test_a_reserved_machine_and_its_binding_are_one_atomic_claim(db_pool):
    pool_id = f"test-{uuid4()}"
    repo = PostgresComputeLeaseRepository(db_pool)
    lease, binding = claim(pool_id)
    try:
        reserved, reserved_slot = await repo.reserve(lease, 1, binding)
        view = await repo.active_for_session(pool_id, binding.session_id)
        assert view == SlotLease(allocation=reserved, binding=reserved_slot)
        assert await repo.slot(lease.id, 0) == binding
        assert await repo.slots(lease.id) == [binding]
        assert await repo.list_slots(pool_id) == [binding]
        with pytest.raises(ValueError, match="different compute claim"):
            await repo.reserve(
                lease, 1, binding.model_copy(update={"id": uuid4(), "tenant_id": "someone-else"})
            )
    finally:
        await clean(db_pool, pool_id)


async def test_operation_lock_is_shared_by_independent_repository_instances(db_pool):
    first, second = PostgresComputeLeaseRepository(db_pool), PostgresComputeLeaseRepository(db_pool)
    pool_id = f"test-{uuid4()}"
    lease, _ = await first.reserve(machine(pool_id), 1)
    try:
        async with first.operation(lease.id):
            with pytest.raises(ComputeLeaseBusyError):
                async with second.operation(lease.id):
                    pytest.fail("Two controllers owned the same allocation")
        async with second.operation(lease.id):
            await second.save(lease.model_copy(update={"state": LeaseState.DRAINING}))
        assert (await first.get(lease.id)).state == LeaseState.DRAINING
    finally:
        await clean(db_pool, pool_id)


async def test_standby_target_and_binding_are_atomic_across_controllers(db_pool):
    pool_id = f"warm-{uuid4()}"
    repos = [PostgresComputeLeaseRepository(db_pool) for _ in range(8)]
    policy = ComputePoolPolicy(profile="small", max_machines=2, warm_min=1, max_provisioning=2)
    await repos[0].set_policy(pool_id, policy)
    try:
        results = await asyncio.gather(
            *(repo.reserve(machine(pool_id), 100) for repo in repos), return_exceptions=True
        )
        spares = [result[0] for result in results if isinstance(result, tuple)]
        assert len(spares) == 1
        spare = spares[0].model_copy(update={"state": LeaseState.IDLE})
        async with repos[0].operation(spare.id):
            await repos[0].save(spare)

        async def bind(repo):
            async with repo.operation(spare.id):
                return await repo.bind(spare, slot(spare))

        results = await asyncio.gather(*(bind(repo) for repo in repos), return_exceptions=True)
        assert sum(isinstance(result, SlotLease) for result in results) == 1
        assert sum(isinstance(result, ComputeLeaseBusyError) for result in results) == 7
        await repos[0].set_policy(pool_id, policy.model_copy(update={"paused": True}))
        with pytest.raises(ComputeCapacityError):
            await repos[1].reserve(machine(pool_id), 100)
    finally:
        await clean(db_pool, pool_id)


async def test_two_slots_take_two_sessions_and_a_third_is_refused(db_pool):
    pool_id = f"slots-{uuid4()}"
    repo = PostgresComputeLeaseRepository(db_pool)
    policy = ComputePoolPolicy(profile="small", max_machines=1, slots_per_machine=2)
    await repo.set_policy(pool_id, policy)
    try:
        spare = await idle_spare(db_pool, pool_id, slots=2)
        sessions = [uuid4(), uuid4(), uuid4()]
        async with repo.operation(spare.id):
            zero = await repo.bind(spare, slot(spare, sessions[0], 0))
            one = await repo.bind(zero.allocation, slot(spare, sessions[1], 1))
            with pytest.raises(ComputeLeaseBusyError, match="no usable slot"):
                await repo.bind(one.allocation, slot(spare, sessions[2], 0))
        assert {s.slot for s in await repo.list_slots(pool_id)} == {0, 1}
        assert (await repo.get(spare.id)).state is LeaseState.IDLE
        assert (await repo.get(spare.id)).idle_since is None
    finally:
        await clean(db_pool, pool_id)


async def test_a_session_holds_one_live_slot_and_the_indexes_back_the_checks(db_pool):
    pool_id = f"index-{uuid4()}"
    repo = PostgresComputeLeaseRepository(db_pool)
    await repo.set_policy(
        pool_id, ComputePoolPolicy(profile="small", max_machines=2, slots_per_machine=2)
    )
    try:
        first, second = await idle_spare(db_pool, pool_id, 2), await idle_spare(db_pool, pool_id, 2)
        session = uuid4()
        async with repo.operation(first.id):
            await repo.bind(first, slot(first, session))
        async with repo.operation(second.id):
            with pytest.raises(ComputeLeaseBusyError, match="already owns"):
                await repo.bind(second, slot(second, session))

        # The same rules hold at the table, for writers that skip the repository.
        rival = slot(second, session)
        with pytest.raises(asyncpg.UniqueViolationError):
            await _insert(db_pool, rival)
        occupied = slot(first, uuid4(), 0)
        with pytest.raises(asyncpg.UniqueViolationError):
            await _insert(db_pool, occupied)
        # A released slot no longer occupies its index.
        ended = slot(second, uuid4(), 0, state=SlotState.RELEASED)
        await _insert(db_pool, ended)
        again = slot(second, uuid4(), 0)
        await _insert(db_pool, again)
    finally:
        await clean(db_pool, pool_id)


async def _insert(db_pool, binding):
    await db_pool.execute(
        "INSERT INTO compute_slots (id, allocation_id, slot, pool_id, session_id, state, data) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)",
        binding.id,
        binding.allocation_id,
        binding.slot,
        binding.pool_id,
        binding.session_id,
        binding.state.value,
        binding.model_dump_json(),
    )


async def test_releasing_a_machine_retires_every_live_binding_and_keeps_the_ended_ones(db_pool):
    pool_id = f"release-{uuid4()}"
    repo = PostgresComputeLeaseRepository(db_pool)
    await repo.set_policy(
        pool_id, ComputePoolPolicy(profile="small", max_machines=1, slots_per_machine=2)
    )
    try:
        spare = await idle_spare(db_pool, pool_id, 2)
        async with repo.operation(spare.id):
            live = await repo.bind(spare, slot(spare, uuid4(), 0))
        finished = slot(spare, uuid4(), 1, state=SlotState.RELEASED)
        await _insert(db_pool, finished)
        async with repo.operation(spare.id):
            await repo.save(live.allocation.model_copy(update={"state": LeaseState.RELEASED}))
        assert (await repo.get_slot(live.binding.id)).state is SlotState.RETIRED
        assert (await repo.get_slot(finished.id)).state is SlotState.RELEASED
        assert await repo.list_slots(pool_id) == []
        assert len(await repo.list_slots(pool_id, live_only=False)) == 2
    finally:
        await clean(db_pool, pool_id)


async def test_a_slot_can_only_be_saved_by_the_operation_that_owns_its_machine(db_pool):
    pool_id = f"owner-{uuid4()}"
    repo = PostgresComputeLeaseRepository(db_pool)
    lease, binding = claim(pool_id)
    await repo.reserve(lease, 1, binding)
    try:
        with pytest.raises(RuntimeError, match="ownership"):
            await repo.save_slot(binding)
        async with repo.operation(lease.id):
            await repo.save_slot(binding.model_copy(update={"state": SlotState.BUSY}))
            ghost = binding.model_copy(update={"id": uuid4()})
            with pytest.raises(LookupError):
                await repo.save_slot(ghost)
        assert (await repo.get_slot(binding.id)).state is SlotState.BUSY
    finally:
        await clean(db_pool, pool_id)
