"""Provider-neutral pool lifecycle using explicit test doubles for the ports."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from volundr.domain.compute import (
    ComputeCapacityError,
    ComputePoolPolicy,
    LeaseState,
    Machine,
    MachineBootstrap,
    MachineState,
)
from volundr.domain.services.compute_leases import ComputeLeaseService


@pytest.fixture
def setup():
    from tests.compute_fakes import pool_setup

    return pool_setup()


async def warm(setup):
    pool, _, repo, _, runtime, _ = setup
    await pool.maintain()
    await pool.maintain()
    lease = next(iter(repo.leases.values()))
    assert lease.state == LeaseState.IDLE
    assert lease.session_id is None
    runtime.warm.assert_awaited_once()
    return lease


async def test_warm_bind_preserves_identity_then_replaces_used_machine(setup):
    pool, leases, repo, provider, runtime, store = setup
    lease = await warm(setup)
    original = await leases.machine_bootstrap_for(lease)
    bound_bootstrap = MachineBootstrap(commands=(("session-only",),))
    session_id = uuid4()
    async with repo.operation(lease.id):
        bound = await leases.bind(lease, session_id, "owner", "tenant", bound_bootstrap, 120)
    assert bound.state == LeaseState.READY
    assert bound.machine == lease.machine
    assert await leases.machine_bootstrap_for(bound) == original
    assert await leases.bootstrap_for(bound) == bound_bootstrap
    with pytest.raises(ValueError, match="Stop the session"):
        await pool.dispose(bound.id)
    async with repo.operation(bound.id):
        await repo.save(
            bound.model_copy(update={"runtime_data_started": True, "stop_requested": True})
        )
    await pool.maintain()
    runtime.stop.assert_awaited_once()
    assert repo.leases[bound.id].state == LeaseState.RELEASED
    assert bound.id not in provider.machines
    assert (
        await store.get_value(
            "compute", bound.bootstrap_owner or "pool", bound.session_bootstrap_ref
        )
        is None
    )
    assert any(item.id != bound.id and item.session_id is None for item in repo.leases.values())


async def test_archive_failure_never_deletes_guest_and_retries_stop_intent(setup):
    pool, leases, repo, provider, runtime, _ = setup
    lease = await leases.acquire(session_id=uuid4(), owner_id="o", tenant_id="t", profile="small")
    async with repo.operation(lease.id):
        await repo.save(
            lease.model_copy(update={"runtime_data_started": True, "stop_requested": True})
        )
    runtime.stop.side_effect = RuntimeError("archive failed")
    with pytest.raises(RuntimeError, match="archive failed"):
        await pool.maintain()
    assert lease.id in provider.machines
    assert repo.leases[lease.id].stop_requested
    runtime.stop.side_effect = None
    await pool.maintain()
    assert repo.leases[lease.id].state == LeaseState.RELEASED


async def test_expire_spare_and_drain_preserves_bound_sessions(setup):
    pool, leases, repo, provider, _, _ = setup
    spare = await warm(setup)
    async with repo.operation(spare.id):
        await repo.save(
            spare.model_copy(update={"idle_since": datetime.now(UTC) - timedelta(days=1)})
        )
    await pool.maintain()
    assert repo.leases[spare.id].state == LeaseState.RELEASED
    active = await leases.acquire(session_id=uuid4(), owner_id="o", tenant_id="t", profile="small")
    await pool.configure((await pool.policy()).model_copy(update={"drain": True}))
    await pool.maintain()
    assert active.id in provider.machines
    assert all(
        lease.session_id is not None or lease.state == LeaseState.RELEASED
        for lease in repo.leases.values()
    )


async def test_orphans_quarantined_and_counted_without_destructive_guessing(setup):
    pool, _, repo, provider, runtime, _ = setup
    orphan_id = uuid4()
    provider.machines[orphan_id] = Machine(
        allocation_id=orphan_id, resource_id="opaque", state=MachineState.RUNNING
    )
    await pool.maintain()
    assert repo.leases[orphan_id].state == LeaseState.QUARANTINED
    assert orphan_id in provider.machines
    snapshot = await pool.snapshot()
    assert snapshot["counts"]["quarantined"] == 1
    assert "bootstrap_ref" not in snapshot["allocations"][0]
    runtime.stop.assert_not_awaited()
    await pool.dispose(orphan_id)
    assert orphan_id not in provider.machines
    with pytest.raises(LookupError):
        await pool.dispose(uuid4())


async def test_persistent_deadline_survives_new_service_and_expired_warm_guest_cleans_up(setup):
    pool, leases, repo, provider, runtime, _ = setup
    await pool.maintain()
    lease = next(iter(repo.leases.values()))
    expired = lease.model_copy(
        update={"provision_deadline": datetime.now(UTC) - timedelta(seconds=1)}
    )
    async with repo.operation(lease.id):
        await repo.save(expired)
    await pool.configure((await pool.policy()).model_copy(update={"paused": True}))
    await pool.maintain()
    assert repo.leases[lease.id].state == LeaseState.RELEASED
    runtime.warm.assert_not_awaited()
    bound = await leases.acquire(session_id=uuid4(), owner_id="o", tenant_id="t", profile="small")
    async with repo.operation(bound.id):
        await repo.save(bound.model_copy(update={"provision_deadline": expired.provision_deadline}))
    recreated = ComputeLeaseService(
        repo, provider, pool_id="pool", max_machines=2, bootstrap=MachineBootstrap()
    )
    assert (await recreated.reconcile(bound.id)).state == LeaseState.FAILED
    assert bound.id in provider.machines


async def test_maintenance_recovers_interrupted_deletion_and_backpressure(setup):
    pool, _, repo, provider, _, _ = setup
    lease = await warm(setup)
    provider.delete_pending = True
    await pool.dispose(lease.id)
    assert repo.leases[lease.id].state == LeaseState.DRAINING
    provider.delete_pending = False
    await pool.maintain()
    assert repo.leases[lease.id].state == LeaseState.RELEASED
    await pool.configure((await pool.policy()).model_copy(update={"warm_min": 2}))
    pool.leases.acquire = AsyncMock(side_effect=ComputeCapacityError())
    await pool.maintain()
    assert pool.last_reconciled is not None


def test_policy_rejects_impossible_capacity():
    with pytest.raises(ValueError, match="Warm minimum"):
        ComputePoolPolicy(profile="small", max_machines=1, warm_min=2)
    with pytest.raises(ValueError):
        ComputePoolPolicy(profile="small", max_machines=0)
