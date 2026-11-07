"""A ``MachineProvider`` over registered hosts: claim instead of create, clean instead of delete.

A host is never created or destroyed here. ``create`` holds one prepared, healthy host for
the allocation; ``delete`` clears the allocation's markers, audits the host for anything a
slot left behind, and ends the hold. A clean host is ready again; a host with findings is
quarantined, loudly, and offered to no one until an operator clears it.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping
from uuid import UUID

from niuu.observability import get_observability
from volundr.domain.compute import (
    HostSelector,
    Machine,
    MachineProfile,
    MachineProvider,
    MachineProviderError,
    MachineRequest,
    MachineState,
)
from volundr.domain.compute_hosts import (
    HostConnector,
    HostHealth,
    HostInspector,
    HostService,
    RegisteredHost,
    RegisteredHostRepository,
)

logger = logging.getLogger(__name__)


class RegisteredHostMachineProvider(MachineProvider):
    def __init__(
        self,
        *,
        pool_id: str,
        profiles: dict[str, dict],
        host_repository: RegisteredHostRepository,
        host_connectors: Mapping[str, HostConnector],
        host_inspector: HostInspector,
    ) -> None:
        if not profiles:
            raise ValueError("Configure at least one registered-host profile")
        self._pool_id = pool_id
        self._profiles = {
            name: {str(key): str(value) for key, value in (details or {}).items()}
            for name, details in profiles.items()
        }
        self._repository = host_repository
        self._connectors = host_connectors
        self._inspector = host_inspector

    @property
    def fixed_inventory(self) -> bool:
        return True

    async def profiles(self) -> tuple[MachineProfile, ...]:
        return tuple(
            MachineProfile(
                name=name,
                revision=hashlib.sha256(
                    json.dumps({"name": name, "details": details}, sort_keys=True).encode()
                ).hexdigest(),
                details={"kind": "registered host", **details},
            )
            for name, details in self._profiles.items()
        )

    async def claimable(self, profile: str, selector: HostSelector) -> int:
        return sum(
            host.claimable and host.profile == profile and selector.accepts(host.tags)
            for host in await self._repository.list(self._pool_id)
        )

    async def tag_sets(self) -> tuple[tuple[str, ...], ...]:
        """Tag sets of hosts that take sessions now or once claimed."""
        return tuple(
            sorted(
                {
                    host.tags
                    for host in await self._repository.list(self._pool_id)
                    if host.accepting and host.slots is not None
                }
            )
        )

    async def create(self, request: MachineRequest) -> Machine:
        if request.profile not in self._profiles:
            raise MachineProviderError(f"Registered-host profile {request.profile!r} is unknown")
        host = await self._repository.claim(
            self._pool_id, request.allocation_id, request.profile, request.selector
        )
        if host is None:
            hosts = [
                h
                for h in await self._repository.list(self._pool_id)
                if h.profile == request.profile
            ]
            states = ", ".join(
                f"{count} {state}"
                for state, count in sorted(_count(h.display_state().value for h in hosts).items())
            )
            raise MachineProviderError(
                f"No registered host of profile {request.profile!r} matching tags "
                f"{list(request.selector.tags)} (match: {request.selector.match}) is free "
                f"({states or 'none registered'}); register or resume a host"
            )
        return self._machine(host, request.allocation_id)

    async def get(self, allocation_id: UUID) -> Machine | None:
        host = await self._repository.held_by(allocation_id)
        return self._machine(host, allocation_id) if host is not None else None

    async def list(self) -> list[Machine]:
        return [
            self._machine(host, host.allocation_id)
            for host in await self._repository.list(self._pool_id)
            if host.allocation_id is not None
        ]

    async def delete(self, allocation_id: UUID) -> bool:
        host = await self._repository.held_by(allocation_id)
        if host is None:
            return True
        # An audit that cannot reach the host raises: the allocation stays draining and its
        # deletion is retried (loudly, by the lease service) until the host is cleaned or an
        # operator removes it. Capacity is never freed on a host nobody checked.
        findings = await self._inspector.audit(host, live_slots=(), clean=True)
        health = HostHealth.QUARANTINED if findings else HostHealth.HEALTHY
        service = HostService.OUT if host.service is HostService.DRAINING else host.service
        if findings:
            get_observability().count(
                "volundr.compute.host.quarantined", attributes={"pool": self._pool_id}
            )
            logger.error(
                "Registered host %s (%s) is quarantined after its allocation %s ended: %s",
                host.name,
                host.id,
                allocation_id,
                "; ".join(findings),
            )
        await self._repository.release(
            allocation_id,
            host.model_copy(update={"health": health, "service": service, "findings": findings}),
        )
        return True

    async def close(self) -> None:
        for connector in self._connectors.values():
            await connector.close()

    def _machine(self, host: RegisteredHost, allocation_id: UUID) -> Machine:
        machine = self._inspector.machine(host, allocation_id)
        if host.health is HostHealth.PENDING:
            return machine.model_copy(update={"state": MachineState.PROVISIONING})
        return machine


def _count(values) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts
