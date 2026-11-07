"""Host facts, the leftover audit, and how Forge dials a registered host."""

from __future__ import annotations

import shutil
import subprocess
from uuid import uuid4

import pytest

from tests.compute_host_fakes import Connector, make_host
from volundr.adapters.outbound.host_checks import (
    AUDIT_SH,
    FACTS_SH,
    LEAVE_PATH,
    SshHostInspector,
    parse_check_output,
    summarize_findings,
)
from volundr.adapters.outbound.host_connectors import DirectSshConnector
from volundr.domain.compute import MachineState
from volundr.domain.compute_hosts import HostDisconnectedError, HostHealth, HostService
from volundr.domain.guest_access import GuestCommandResult


class Access:
    def __init__(self, stdout=b""):
        self.stdout = stdout
        self.calls = []

    async def execute(self, lease, bootstrap, command, **kwargs):
        self.calls.append((lease, command, kwargs))
        return GuestCommandResult(0, self.stdout)


def _inspector(access, *, uid_base=61000):
    return SshHostInspector(
        access_for=lambda host: access,
        uid_base_for=lambda host: uid_base,
        connectors={"direct-ssh": Connector()},
        min_free_disk_mib=2048,
        command_timeout_seconds=30,
    )


class TestParsing:
    def test_facts_and_findings(self):
        parsed = parse_check_output(
            "fact cpus 48\nfact cgroup_v2 true\nfact os Ubuntu 24.04 LTS\n"
            "finding user niuu-slot-3 still exists\nnoise\nfinding \nfact \n"
        )
        assert parsed.facts == {"cpus": 48, "cgroup_v2": True, "os": "Ubuntu 24.04 LTS"}
        assert parsed.findings == ("user niuu-slot-3 still exists",)

    def test_summary_keeps_whole_findings_and_counts_the_rest(self):
        assert summarize_findings(()) is None
        assert summarize_findings(("a", "b")) == "a; b"
        long = tuple(f"finding number {i} " + "x" * 80 for i in range(10))
        summary = summarize_findings(long)
        assert len(summary) <= 512
        assert summary.endswith("finding(s) on the host list")
        assert summary.startswith("finding number 0 ")

    def test_summary_of_one_huge_finding(self):
        assert summarize_findings(("x" * 600,)) == "1 finding(s) on the host list"


class TestInspector:
    async def test_facts_run_the_facts_program_as_root(self):
        access = Access(b"fact cpus 8\nfact memory_mib 16384\n")
        host = make_host()
        facts = await _inspector(access).facts(host)
        assert facts == {"cpus": 8, "memory_mib": 16384}
        [(lease, command, kwargs)] = access.calls
        assert command == "sudo -n sh -s --"
        assert kwargs["data"] == FACTS_SH.encode()
        assert kwargs["timeout_seconds"] == 30
        assert lease.id == host.id
        assert lease.machine.addresses == ("192.168.90.31",)

    async def test_audit_passes_the_slot_layout_and_returns_findings(self):
        access = Access(b"finding /tmp/x is owned by uid 61002\n")
        host = make_host(slots=4)
        findings = await _inspector(access).audit(host, live_slots=[3, 1], clean=False)
        assert findings == ("/tmp/x is owned by uid 61002",)
        [(_, command, kwargs)] = access.calls
        assert command == "sudo -n sh -s -- 61000 4 1,3 2048 check"
        assert kwargs["data"] == AUDIT_SH.encode()

    async def test_clean_audit_of_an_empty_host(self):
        access = Access()
        await _inspector(access).audit(make_host(), live_slots=(), clean=True)
        assert access.calls[0][1].endswith(" 2048 clean")

    async def test_a_host_with_live_slots_is_never_cleaned(self):
        with pytest.raises(ValueError):
            await _inspector(Access()).audit(make_host(), live_slots=[0], clean=True)

    async def test_a_runtime_without_slots_audits_no_slot_uids(self):
        access = Access()
        await _inspector(access, uid_base=None).audit(make_host(), live_slots=(), clean=False)
        assert access.calls[0][1] == "sudo -n sh -s -- 0 0 '' 2048 check"

    async def test_leave_runs_the_hosts_own_script(self):
        access = Access()
        await _inspector(access).leave(make_host())
        assert access.calls[0][1] == f"sudo -n {LEAVE_PATH}"

    def test_machine_record_carries_what_ssh_and_placement_need(self):
        allocation = uuid4()
        host = make_host(
            tags=("gpu",),
            service=HostService.DRAINING,
            findings=("a",),
        )
        machine = _inspector(Access()).machine(host, allocation)
        assert machine.allocation_id == allocation
        assert machine.state is MachineState.RUNNING
        assert machine.host_public_key == host.host_public_key
        assert machine.tags == ("gpu",)
        assert machine.accepting is False
        assert machine.status_detail == "a"

    def test_constructor_refuses_bad_bounds(self):
        with pytest.raises(ValueError):
            SshHostInspector(
                access_for=lambda h: None,
                uid_base_for=lambda h: None,
                connectors={},
                min_free_disk_mib=-1,
                command_timeout_seconds=1,
            )


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX sh")
@pytest.mark.parametrize("program", [FACTS_SH, AUDIT_SH])
def test_programs_are_valid_posix_sh(program):
    subprocess.run(["sh", "-n"], input=program.encode(), check=True)


class TestDirectSsh:
    def test_join_setup_narrows_forges_key(self):
        connector = DirectSshConnector(
            allowed_sources=["192.168.90.240", "10.0.0.0/8"],
            permit_open=["127.0.0.1:18100"],
            permit_listen=["127.0.0.1:18200"],
        )
        setup = connector.join_setup(make_host())
        assert setup.authorized_key_options == (
            'restrict,port-forwarding,from="192.168.90.240,10.0.0.0/8",'
            'permitopen="127.0.0.1:18100",permitlisten="127.0.0.1:18200"'
        )
        assert (connector.kind, connector.label) == ("direct_ssh", "Forge connects over SSH")

    def test_no_sources_is_an_explicit_choice(self):
        setup = DirectSshConnector(allowed_sources=[]).join_setup(make_host())
        assert setup.authorized_key_options == "restrict,port-forwarding"

    def test_refuses_bad_config(self):
        with pytest.raises(ValueError):
            DirectSshConnector(allowed_sources=["not-an-address"])
        with pytest.raises(ValueError):
            DirectSshConnector(allowed_sources=[], ssh_port=0)

    async def test_admit_takes_the_reported_address(self):
        connector = DirectSshConnector(allowed_sources=[], ssh_port=2222)
        assert await connector.admit(make_host(), {"address": " 192.168.90.35 "}) == {
            "address": "192.168.90.35",
            "port": 2222,
        }
        with pytest.raises(ValueError, match="no address"):
            await connector.admit(make_host(), {})
        with pytest.raises(ValueError):
            await connector.admit(make_host(), {"address": "example.com"})

    async def test_dial_and_status_follow_the_last_check(self):
        connector = DirectSshConnector(allowed_sources=[])
        host = make_host(connection={"address": "10.1.2.3", "port": 22})
        assert connector.dial(host).address == "10.1.2.3"
        status = await connector.status(host)
        assert (status.connected, status.target, status.last_error) == (True, "10.1.2.3:22", None)
        down = await connector.status(
            host.model_copy(update={"health": HostHealth.UNREACHABLE, "findings": ("refused",)})
        )
        assert (down.connected, down.last_error) == (False, "refused")
        pending = await connector.status(host.model_copy(update={"health": HostHealth.PENDING}))
        assert pending.connected is False
        await connector.revoke(host)
        await connector.close()


class DisconnectedConnector(Connector):
    async def require_connection(self, host):
        raise HostDisconnectedError("the host's tunnel is down")


class TestDisconnectedHosts:
    def _inspector(self, access):
        return SshHostInspector(
            access_for=lambda host: access,
            uid_base_for=lambda host: 61000,
            connectors={"direct-ssh": DisconnectedConnector()},
            min_free_disk_mib=2048,
            command_timeout_seconds=30,
        )

    async def test_a_check_says_the_tunnel_is_down_without_dialing(self):
        access = Access()
        with pytest.raises(HostDisconnectedError, match="tunnel is down"):
            await self._inspector(access).facts(make_host())
        assert access.calls == []

    async def test_leave_says_the_tunnel_is_down_without_dialing(self):
        access = Access()
        with pytest.raises(HostDisconnectedError):
            await self._inspector(access).leave(make_host())
        assert access.calls == []
