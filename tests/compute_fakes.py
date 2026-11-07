"""Explicit in-memory compute ports used only by tests."""

from contextlib import asynccontextmanager

from volundr.domain.compute import (
    LIVE_SLOT_STATES,
    ComputePoolFullError,
    LeaseState,
    Machine,
    MachineState,
    SlotLease,
    SlotState,
)


class LeaseRepository:
    """In-memory ledger with the Postgres adapter's slot semantics."""

    def __init__(self):
        self.leases = {}
        self.bindings = {}
        self.locked = None
        self.policies = {}

    async def reserve(self, lease, limit, binding=None):
        if binding is not None:
            for existing in self.bindings.values():
                if existing.session_id == binding.session_id and existing.state in LIVE_SLOT_STATES:
                    return self.leases[existing.allocation_id], existing
        active = [item for item in self.leases.values() if item.state != LeaseState.RELEASED]
        if len(active) >= limit:
            raise ComputePoolFullError()
        self.leases[lease.id] = lease
        if binding is not None:
            self.bindings[binding.id] = binding
        return lease, binding

    async def policy(self, pool_id, default):
        return self.policies.setdefault(pool_id, default)

    async def set_policy(self, pool_id, policy):
        self.policies[pool_id] = policy

    async def bind(self, machine, binding):
        assert self.locked == machine.id
        assert self.leases[machine.id].state == LeaseState.IDLE
        self.bindings[binding.id] = binding
        updated = self.leases[machine.id].model_copy(update={"idle_since": None})
        self.leases[machine.id] = updated
        return SlotLease(allocation=updated, binding=binding)

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
        if lease.state == LeaseState.RELEASED:
            for binding in list(self.bindings.values()):
                if binding.allocation_id == lease.id and binding.state in LIVE_SLOT_STATES:
                    self.bindings[binding.id] = binding.model_copy(
                        update={"state": SlotState.RETIRED}
                    )

    async def save_slot(self, binding):
        assert self.locked == binding.allocation_id
        assert binding.id in self.bindings
        self.bindings[binding.id] = binding

    async def get_slot(self, slot_id):
        return self.bindings.get(slot_id)

    async def slot(self, allocation_id, slot):
        return next(
            (
                item
                for item in self.bindings.values()
                if item.allocation_id == allocation_id
                and item.slot == slot
                and item.state in LIVE_SLOT_STATES
            ),
            None,
        )

    async def slots(self, allocation_id):
        return [item for item in self.bindings.values() if item.allocation_id == allocation_id]

    async def list_slots(self, pool_id, *, live_only=True):
        return [
            item
            for item in self.bindings.values()
            if item.pool_id == pool_id and (not live_only or item.state in LIVE_SLOT_STATES)
        ]

    async def active_for_session(self, pool_id, session_id):
        binding = next(
            (
                item
                for item in self.bindings.values()
                if item.pool_id == pool_id
                and item.session_id == session_id
                and item.state in LIVE_SLOT_STATES
            ),
            None,
        )
        if binding is None:
            return None
        return SlotLease(allocation=self.leases[binding.allocation_id], binding=binding)

    @asynccontextmanager
    async def operation(self, lease_id):
        assert self.locked is None
        self.locked = lease_id
        try:
            yield
        finally:
            self.locked = None


class Provider:
    async def profiles(self):
        from volundr.domain.compute import MachineProfile

        return (
            MachineProfile(name="small", revision=self.profile_revision, details={"Size": "Small"}),
        )

    def __init__(self):
        self.profile_revision = "revision-1"
        self.closed = False
        self.machines = {}
        self.create_error = False
        self.delete_error = False
        self.delete_pending = False
        self.created = []

    async def close(self):
        self.closed = True

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
