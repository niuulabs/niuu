"""The machine record Forge dials carries the platform the host recorded at join."""

from __future__ import annotations

import pytest

from tests.compute_host_fakes import Connector, make_host
from volundr.adapters.outbound.host_checks import SshHostInspector
from volundr.domain.compute import HostPlatform


def _inspector() -> SshHostInspector:
    return SshHostInspector(
        access_for=lambda host: None,
        uid_base_for=lambda host: 61000,
        connectors={"direct-ssh": Connector()},
        min_free_disk_mib=2048,
        command_timeout_seconds=30,
    )


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_machine_record_carries_the_hosts_platform(platform):
    host = make_host(facts={"platform": platform})

    machine = _inspector().machine(host)

    assert machine.platform is HostPlatform(platform)
    assert machine.platform is host.platform
