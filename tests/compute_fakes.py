"""Explicit in-memory compute ports used only by tests."""

from contextlib import asynccontextmanager

from volundr.domain.compute import ComputeCapacityError, LeaseState, Machine, MachineState


class LeaseRepository:
    def __init__(self):
        self.leases = {}
        self.locked = None
        self.policies = {}

    async def reserve(self, lease, limit):
        active = [item for item in self.leases.values() if item.state != LeaseState.RELEASED]
        for existing in active:
            if lease.session_id is not None and existing.session_id == lease.session_id:
                return existing
        if len(active) >= limit:
            raise ComputeCapacityError()
        self.leases[lease.id] = lease
        return lease

    async def policy(self, pool_id, default):
        return self.policies.setdefault(pool_id, default)

    async def set_policy(self, pool_id, policy):
        self.policies[pool_id] = policy

    async def bind(self, lease):
        assert self.locked == lease.id
        assert self.leases[lease.id].state == LeaseState.IDLE
        self.leases[lease.id] = lease
        return lease

    async def quarantine(self, lease):
        self.leases.setdefault(lease.id, lease)

    async def get(self, lease_id):
        return self.leases.get(lease_id)

    async def list(self, pool_id, *, include_released=True):
        return [
            item
            for item in self.leases.values()
            if item.pool_id == pool_id and (include_released or item.state != LeaseState.RELEASED)
        ]

    async def save(self, lease):
        assert self.locked == lease.id
        self.leases[lease.id] = lease

    @asynccontextmanager
    async def operation(self, lease_id):
        assert self.locked is None
        self.locked = lease_id
        try:
            yield
        finally:
            self.locked = None


class Provider:
    def __init__(self):
        self.machines = {}
        self.create_error = False
        self.delete_error = False
        self.delete_pending = False
        self.created = []

    async def create(self, request):
        self.created.append(request.allocation_id)
        self.machines[request.allocation_id] = Machine(
            allocation_id=request.allocation_id,
            resource_id="provider-opaque-id",
            state=MachineState.PROVISIONING,
        )
        if self.create_error:
            raise RuntimeError("provider-secret-error")
        return self.machines[request.allocation_id]

    async def list(self):
        return list(self.machines.values())

    async def get(self, allocation_id):
        return self.machines.get(allocation_id)

    async def delete(self, allocation_id):
        if self.delete_error:
            raise RuntimeError("delete error")
        if self.delete_pending:
            return False
        self.machines.pop(allocation_id, None)
        return True


def pool_setup():
    from unittest.mock import AsyncMock

    from niuu.adapters.memory_credential_store import MemoryCredentialStore
    from tests.test_adapters.test_vm_pod_manager import ReadyProvider, Repository
    from volundr.domain.compute import ComputePoolPolicy, MachineBootstrap
    from volundr.domain.services.compute_leases import ComputeLeaseService
    from volundr.domain.services.compute_pool import ComputePoolService

    repo, provider, runtime = Repository(), ReadyProvider(), AsyncMock()
    runtime.machine_bootstrap = lambda defaults: defaults
    store = MemoryCredentialStore()
    leases = ComputeLeaseService(
        repo,
        provider,
        pool_id="pool",
        max_machines=2,
        bootstrap=MachineBootstrap(),
        bootstrap_store=store,
    )
    pool = ComputePoolService(
        repo,
        leases,
        provider,
        runtime,
        pool_id="pool",
        defaults=ComputePoolPolicy(profile="small", max_machines=2, warm_min=1),
        bootstrap=MachineBootstrap(),
        interval_seconds=0.001,
    )
    return pool, leases, repo, provider, runtime, store
