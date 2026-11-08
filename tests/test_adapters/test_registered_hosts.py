"""RegisteredHostMachineProvider: claim instead of create, audit instead of delete."""

from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

import pytest

from tests.compute_host_fakes import (
    POOL,
    Connector,
    HostRepository,
    Inspector,
    make_host,
    other_host_key,
)
from volundr.adapters.outbound.registered_hosts import RegisteredHostMachineProvider
from volundr.domain.compute import (
    HostSelector,
    MachineBootstrap,
    MachineProviderError,
    MachineRequest,
    MachineState,
)
from volundr.domain.compute_hosts import HostHealth, HostService


def _provider(*hosts, inspector=None):
    repository = HostRepository(*hosts)
    connector = Connector()
    inspector = inspector or Inspector()
    provider = RegisteredHostMachineProvider(
        pool_id=POOL,
        profiles={"bm": {"label": "Bare metal"}},
        host_repository=repository,
        host_connectors={"direct-ssh": connector},
        host_inspector=inspector,
    )
    return provider, repository, connector, inspector


def _request(selector=None, profile="bm"):
    return MachineRequest(
        allocation_id=uuid4(),
        profile=profile,
        bootstrap=MachineBootstrap(),
        selector=selector or HostSelector(),
    )


class TestInventory:
    async def test_is_a_fixed_inventory_with_stable_profiles(self):
        provider, *_ = _provider()
        assert provider.fixed_inventory is True
        [first] = await provider.profiles()
        [second] = await provider.profiles()
        assert first == second
        assert first.name == "bm"
        assert first.details == {"kind": "registered host", "label": "Bare metal"}

    def test_needs_a_profile(self):
        with pytest.raises(ValueError):
            RegisteredHostMachineProvider(
                pool_id=POOL,
                profiles={},
                host_repository=HostRepository(),
                host_connectors={},
                host_inspector=Inspector(),
            )

    async def test_claimable_and_tag_sets_count_only_usable_hosts(self):
        gpu = make_host(name="gpu", tags=("gpu",), host_public_key=other_host_key(1))
        plain = make_host(name="plain", host_public_key=other_host_key(2))
        held = make_host(name="held", allocation_id=uuid4(), host_public_key=other_host_key(3))
        drained = make_host(
            name="drained", service=HostService.OUT, tags=("x",), host_public_key=other_host_key(4)
        )
        unsized = make_host(
            name="unsized", slots=None, tags=("y",), host_public_key=other_host_key(5)
        )
        provider, *_ = _provider(gpu, plain, held, drained, unsized)

        assert await provider.claimable("bm", HostSelector()) == 2
        assert await provider.claimable("bm", HostSelector(tags=("gpu",))) == 1
        assert await provider.claimable("other", HostSelector()) == 0
        assert await provider.tag_sets() == ((), ("gpu",))


class TestClaim:
    async def test_create_claims_a_host_and_reports_how_to_reach_it(self):
        host = make_host(tags=("gpu",))
        provider, repository, _, _ = _provider(host)
        request = _request(HostSelector(tags=("gpu",)))

        machine = await provider.create(request)

        assert repository.hosts[host.id].allocation_id == request.allocation_id
        assert machine.allocation_id == request.allocation_id
        assert machine.state is MachineState.RUNNING
        assert machine.slots == 4
        assert machine.tags == ("gpu",)
        assert machine.host_public_key == host.host_public_key
        assert machine.phase == "hrimfaxi"
        # Idempotent: the same allocation gets the same host back.
        assert (await provider.create(request)).resource_id == machine.resource_id

    async def test_create_without_a_free_host_is_loud(self):
        busy = make_host(allocation_id=uuid4())
        quarantined = make_host(
            name="q", health=HostHealth.QUARANTINED, host_public_key=other_host_key(1)
        )
        provider, *_ = _provider(busy, quarantined)
        with pytest.raises(MachineProviderError) as raised:
            await provider.create(_request(HostSelector(tags=("gpu",), match="any")))
        message = str(raised.value)
        assert "['gpu'] (match: any)" in message
        assert "1 in_service" in message
        assert "1 quarantined" in message
        assert "register or resume a host" in message

    async def test_create_with_no_hosts_says_so(self):
        provider, *_ = _provider()
        with pytest.raises(MachineProviderError, match="none registered"):
            await provider.create(_request())

    async def test_unknown_profile(self):
        provider, *_ = _provider(make_host())
        with pytest.raises(MachineProviderError, match="unknown"):
            await provider.create(_request(profile="vm"))

    async def test_get_and_list_show_held_hosts(self):
        allocation = uuid4()
        held = make_host(allocation_id=allocation)
        pending = make_host(
            name="p",
            allocation_id=uuid4(),
            health=HostHealth.PENDING,
            host_public_key=other_host_key(1),
        )
        free = make_host(name="free", host_public_key=other_host_key(2))
        provider, *_ = _provider(held, pending, free)

        assert (await provider.get(allocation)).resource_id == str(held.id)
        assert (await provider.get(pending.allocation_id)).state is MachineState.PROVISIONING
        assert await provider.get(uuid4()) is None
        assert {m.resource_id for m in await provider.list()} == {str(held.id), str(pending.id)}

    async def test_a_draining_host_is_not_accepting(self):
        allocation = uuid4()
        host = make_host(allocation_id=allocation, service=HostService.DRAINING)
        provider, *_ = _provider(host)
        assert (await provider.get(allocation)).accepting is False


class TestClean:
    async def test_a_clean_host_is_ready_again(self):
        allocation = uuid4()
        host = make_host(allocation_id=allocation)
        provider, repository, _, inspector = _provider(host)

        assert await provider.delete(allocation) is True

        stored = repository.hosts[host.id]
        assert stored.allocation_id is None
        assert stored.health is HostHealth.HEALTHY
        assert inspector.audits == [(host.id, (), True)]

    async def test_a_drained_host_goes_out_of_service(self):
        allocation = uuid4()
        provider, repository, *_ = _provider(
            make_host(allocation_id=allocation, service=HostService.DRAINING)
        )
        await provider.delete(allocation)
        [stored] = repository.hosts.values()
        assert stored.service is HostService.OUT

    async def test_leftovers_quarantine_loudly(self, caplog):
        allocation = uuid4()
        host = make_host(allocation_id=allocation)
        provider, repository, *_ = _provider(
            host, inspector=Inspector(findings=["process sleep (pid 9) still runs as uid 61001"])
        )
        with patch("volundr.adapters.outbound.registered_hosts.get_observability") as observe:
            assert await provider.delete(allocation) is True
        stored = repository.hosts[host.id]
        assert stored.health is HostHealth.QUARANTINED
        assert stored.allocation_id is None
        assert stored.findings == ("process sleep (pid 9) still runs as uid 61001",)
        observe.return_value.count.assert_called_once_with(
            "volundr.compute.host.quarantined", attributes={"pool": POOL}
        )
        assert "quarantined" in caplog.text

    async def test_an_unreachable_host_keeps_its_allocation_draining(self):
        allocation = uuid4()
        host = make_host(allocation_id=allocation)
        provider, repository, *_ = _provider(host, inspector=Inspector(error=OSError("refused")))
        with pytest.raises(OSError):
            await provider.delete(allocation)
        assert repository.hosts[host.id].allocation_id == allocation

    async def test_an_unknown_allocation_is_already_gone(self):
        provider, *_ = _provider()
        assert await provider.delete(uuid4()) is True

    async def test_close_closes_connectors(self):
        provider, _, connector, _ = _provider()
        await provider.close()
        assert connector.closed is True
