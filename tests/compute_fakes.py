"""Explicit in-memory compute ports used only by tests."""

from contextlib import asynccontextmanager

from volundr.domain.compute import ComputeCapacityError, LeaseState, Machine, MachineState


class LeaseRepository:
    def __init__(self):
        self.leases = {}
        self.locked = None

    async def reserve(self, lease, limit):
        active = [item for item in self.leases.values() if item.state != LeaseState.RELEASED]
        for existing in active:
            if existing.session_id == lease.session_id:
                return existing
        if len(active) >= limit:
            raise ComputeCapacityError()
        self.leases[lease.id] = lease
        return lease

    async def get(self, lease_id):
        return self.leases.get(lease_id)

    async def list(self, pool_id):
        return [item for item in self.leases.values() if item.pool_id == pool_id]

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

    async def get(self, allocation_id):
        return self.machines.get(allocation_id)

    async def delete(self, allocation_id):
        if self.delete_error:
            raise RuntimeError("delete error")
        if self.delete_pending:
            return False
        self.machines.pop(allocation_id, None)
        return True
