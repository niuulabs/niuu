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
            bound.model_copy(
                update={
                    "runtime_data_started": True,
                    "runtime_started": True,
                    "stop_requested": True,
                }
            )
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
            lease.model_copy(
                update={
                    "runtime_data_started": True,
                    "runtime_started": True,
                    "stop_requested": True,
                }
            )
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


@pytest.mark.parametrize("stored_revision", ["revision-1", ""])
async def test_profile_revision_change_retires_spares_but_preserves_bound_guests(
    setup, stored_revision
):
    pool, leases, repo, provider, _, _ = setup
    spare = await warm(setup)
    repo.leases[spare.id] = spare.model_copy(update={"profile_revision": stored_revision})
    bound = await leases.acquire(
        session_id=uuid4(), owner_id="owner", tenant_id="tenant", profile="small"
    )
    provider.profile_revision = "revision-2"
    await pool.maintain()
    assert repo.leases[spare.id].state == LeaseState.RELEASED
    assert bound.id in provider.machines
    replacement = next(
        lease
        for lease in repo.leases.values()
        if lease.session_id is None and lease.state != LeaseState.RELEASED
    )
    assert replacement.profile_revision == "revision-2"
    assert "profile_revision" not in str(await pool.snapshot())


async def test_reuse_preserves_machine_clears_binding_and_expires_idle(setup):
    pool, leases, repo, provider, runtime, store = setup
    runtime.supports_reuse = True
    await pool.configure((await pool.policy()).model_copy(update={"reuse_policy": "reuse"}))
    lease = await warm(setup)
    async with repo.operation(lease.id):
        bound = await leases.bind(lease, uuid4(), "one", "tenant", MachineBootstrap(), 120)
        await repo.save(
            bound.model_copy(
                update={
                    "runtime_data_started": True,
                    "runtime_started": True,
                    "stop_requested": True,
                }
            )
        )
    await pool.maintain()
    idle = repo.leases[lease.id]
    assert idle.state == LeaseState.IDLE
    assert idle.session_id is None and idle.owner_id == "" and idle.tenant_id == ""
    assert not idle.runtime_started and not idle.runtime_data_started and not idle.stop_requested
    assert idle.machine == lease.machine and lease.id in provider.machines
    runtime.stop.assert_awaited_once()
    runtime.recycle.assert_awaited_once()
    assert (
        await store.get_value("compute", bound.bootstrap_owner, bound.session_bootstrap_ref) is None
    )
    # A different session claims exactly the same guest, without old credentials.
    async with repo.operation(idle.id):
        second = await leases.bind(idle, uuid4(), "two", "tenant", MachineBootstrap(), 120)
        await repo.save(
            second.model_copy(
                update={
                    "runtime_data_started": True,
                    "runtime_started": True,
                    "stop_requested": True,
                }
            )
        )
    await pool.maintain()
    assert repo.leases[lease.id].state == LeaseState.IDLE
    # Keep surplus reused capacity until idle expiry; do not immediately delete it.
    await pool.configure((await pool.policy()).model_copy(update={"warm_min": 0}))
    await pool.maintain()
    assert lease.id in provider.machines
    async with repo.operation(lease.id):
        await repo.save(
            repo.leases[lease.id].model_copy(
                update={"idle_since": datetime.now(UTC) - timedelta(days=1)}
            )
        )
    await pool.maintain()
    assert repo.leases[lease.id].state == LeaseState.RELEASED
    assert lease.id not in provider.machines


async def test_failed_recycle_keeps_session_bound_until_retry(setup):
    pool, leases, repo, provider, runtime, _ = setup
    runtime.supports_reuse = True
    await pool.configure((await pool.policy()).model_copy(update={"reuse_policy": "reuse"}))
    lease = await warm(setup)
    async with repo.operation(lease.id):
        bound = await leases.bind(lease, uuid4(), "owner", "tenant", MachineBootstrap(), 120)
        await repo.save(
            bound.model_copy(
                update={
                    "runtime_data_started": True,
                    "runtime_started": True,
                    "stop_requested": True,
                }
            )
        )
    runtime.recycle.side_effect = RuntimeError("cleanup failed")
    with pytest.raises(RuntimeError, match="cleanup failed"):
        await pool.maintain()
    assert repo.leases[lease.id].session_id == bound.session_id
    assert repo.leases[lease.id].stop_requested
    assert lease.id in provider.machines
    runtime.recycle.side_effect = None
    await pool.maintain()
    assert repo.leases[lease.id].state == LeaseState.IDLE


async def test_reuse_requires_runtime_capability(setup):
    pool, _, _, _, runtime, _ = setup
    runtime.supports_reuse = False
    with pytest.raises(ValueError, match="does not support"):
        await pool.configure((await pool.policy()).model_copy(update={"reuse_policy": "reuse"}))


async def test_stale_stop_snapshot_cannot_stop_next_session_on_reused_vm(setup):
    pool, leases, repo, _, runtime, _ = setup
    runtime.supports_reuse = True
    await pool.configure((await pool.policy()).model_copy(update={"reuse_policy": "reuse"}))
    lease = await warm(setup)
    async with repo.operation(lease.id):
        first = await leases.bind(lease, uuid4(), "one", "tenant", MachineBootstrap(), 120)
        stale = first.model_copy(
            update={"runtime_data_started": True, "runtime_started": True, "stop_requested": True}
        )
        await repo.save(stale)
    await pool.maintain()
    async with repo.operation(lease.id):
        second = await leases.bind(
            repo.leases[lease.id], uuid4(), "two", "tenant", MachineBootstrap(), 120
        )
    runtime.stop.reset_mock()
    await pool._maintain_lease(stale, await pool.policy())
    runtime.stop.assert_not_called()
    assert repo.leases[lease.id].session_id == second.session_id


async def test_unstarted_allocation_can_be_deleted_without_bootstrap_secret(setup):
    pool, leases, repo, provider, runtime, store = setup
    bound = await leases.acquire(
        session_id=uuid4(),
        owner_id="owner",
        tenant_id="tenant",
        profile="small",
        bootstrap=MachineBootstrap(),
    )
    await store.delete("compute", bound.bootstrap_owner, bound.bootstrap_ref)
    async with repo.operation(bound.id):
        await repo.save(bound.model_copy(update={"stop_requested": True}))
    await pool.maintain()
    assert repo.leases[bound.id].state == LeaseState.RELEASED
    assert bound.id not in provider.machines
    runtime.stop.assert_not_called()
