"""Host facts, the leftover audit, and how Forge dials a registered host."""

from __future__ import annotations

import shutil
import subprocess
from uuid import uuid4

import pytest

from tests.compute_host_fakes import Connector, make_host
from volundr.adapters.outbound.host_checks import (
    AUDIT_SH,
    DARWIN_AUDIT_SH,
    DARWIN_FACTS_SH,
    FACTS_SH,
    LEAVE_PATH,
    SshHostInspector,
    parse_check_output,
    slot_audit_arguments,
    summarize_findings,
)
from volundr.adapters.outbound.host_connectors import DirectSshConnector
from volundr.domain.compute import MachineState
from volundr.domain.compute_hosts import (
    HostDisconnectedError,
    HostHealth,
    HostPlatform,
    HostService,
)
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

    async def test_a_mac_gets_the_macos_programs(self):
        access = Access(b"fact platform darwin\nfact cpus 10\n")
        host = make_host(slots=1, facts={"platform": "darwin"})
        inspector = _inspector(access)
        assert await inspector.facts(host) == {"platform": "darwin", "cpus": 10}
        await inspector.audit(host, live_slots=(), clean=False)
        assert [kwargs["data"] for _, _, kwargs in access.calls] == [
            DARWIN_FACTS_SH.encode(),
            DARWIN_AUDIT_SH.encode(),
        ]
        assert access.calls[1][1] == "sudo -n sh -s -- 61000 1 '' 2048 check"

    def test_the_linux_facts_name_the_platform(self):
        assert "fact platform $(uname -s" in FACTS_SH

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
@pytest.mark.parametrize("program", [FACTS_SH, AUDIT_SH, DARWIN_FACTS_SH, DARWIN_AUDIT_SH])
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


def _shims(directory, programs):
    """Executables that stand in for a Mac's tools, so the macOS audit runs anywhere."""
    directory.mkdir(exist_ok=True)
    for name, body in programs.items():
        path = directory / name
        path.write_text("#!/bin/sh\n" + body + "\n")
        path.chmod(0o755)
    return directory


class TestSlotAuditArguments:
    def test_one_slot_is_audited_alone_with_no_floor_and_no_clean_up(self):
        assert slot_audit_arguments(uid_base=61000, slot=3) == (
            "61000",
            "4",
            "",
            "0",
            "check",
            "3",
        )

    def test_slot_zero_is_a_slot(self):
        assert slot_audit_arguments(uid_base=70000, slot=0) == (
            "70000",
            "1",
            "",
            "0",
            "check",
            "0",
        )

    def test_a_negative_slot_is_refused(self):
        with pytest.raises(ValueError, match="never negative"):
            slot_audit_arguments(uid_base=61000, slot=-1)


#: Executables that stand in for a Linux host's tools. Slots 0 and 1 both left everything.
_LINUX_LEFTOVERS = {
    "getent": 'case "$1" in\n'
    '  passwd) printf "root:x:0:0:root:/root:/bin/sh\\nniuu-slot-0:x:61000:61000::/:/bin/false\\n'
    'niuu-slot-1:x:61001:61001::/:/bin/false\\n" ;;\n'
    '  group) printf "%s:x:1:\\n" "$2" ;;\n'
    "esac",
    "pgrep": 'case "$2" in 61000) echo 4000 ;; 61001) echo 4001 ;; esac',
    "ps": "echo sleeper",
    # One directory is enough: the audit looks in each of them.
    "find": '[ "$1" = /tmp ] && echo "/tmp/left-by-$4"; true',
    "ip": 'case "$1 $2" in\n'
    '  "netns list") printf "niuu-slot-0 (id: 0)\\nniuu-slot-1\\n" ;;\n'
    "  *) exit 0 ;;\nesac",
    "systemctl": 'echo "$5 loaded active"',
    "ipcs": 'printf "m 1 61000 --rw\\nm 2 61001 --rw\\n"',
}


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX sh")
class TestLinuxSlotAudit:
    def _run(self, tmp_path, *args):
        shims = _shims(tmp_path / "bin", _LINUX_LEFTOVERS)
        result = subprocess.run(
            ["sh", "-s", "--", *args],
            input=AUDIT_SH.encode(),
            capture_output=True,
            env={"PATH": f"{shims}:/usr/bin:/bin"},
            check=True,
        )
        findings = parse_check_output(result.stdout.decode()).findings
        # What the machine running the test has in /etc/nix is not what is under test.
        return tuple(f for f in findings if not f.startswith("nix trusted-users"))

    def _slots_named(self, findings):
        """The slots (0 or 1) any finding is about, by the names and uids a finding carries."""
        return {
            slot
            for slot in ("0", "1")
            if any(
                marker in finding
                for finding in findings
                for marker in (f"-slot-{slot}", f"slot{slot}", f"slot_{slot}", f"6100{slot}")
            )
        }

    def test_every_dead_slot_is_audited_when_none_is_named(self, tmp_path):
        findings = self._run(tmp_path, "61000", "2", "", "0", "check")

        assert "user niuu-slot-0 still exists" in findings
        assert "user niuu-slot-1 still exists" in findings
        assert self._slots_named(findings) == {"0", "1"}

    def test_naming_a_slot_audits_that_slot_alone(self, tmp_path):
        findings = self._run(tmp_path, "61000", "2", "", "0", "check", "1")

        assert findings == (
            "user niuu-slot-1 still exists",
            "process sleeper (pid 4001) still runs as uid 61001",
            "/tmp/left-by-61001 is owned by uid 61001",
            "network namespace niuu-slot-1 still exists",
            "link niuuslot1 still exists",
            "slice niuu_slot_1.slice still exists",
            "IPC objects created by uid 61001 still exist",
        )

    def test_the_other_slot_is_audited_alone_too(self, tmp_path):
        findings = self._run(tmp_path, "61000", "2", "", "0", "check", "0")

        assert "user niuu-slot-0 still exists" in findings
        assert self._slots_named(findings) == {"0"}

    def test_a_named_slot_that_is_live_is_left_alone(self, tmp_path):
        assert self._run(tmp_path, "61000", "2", "1", "0", "check", "1") == ()

    def test_without_a_named_slot_a_live_slot_is_still_skipped(self, tmp_path):
        findings = self._run(tmp_path, "61000", "2", "0", "0", "check")

        assert self._slots_named(findings) == {"1"}

    def test_the_per_slot_audit_arguments_run_the_program(self, tmp_path):
        arguments = slot_audit_arguments(uid_base=61000, slot=1)

        findings = self._run(tmp_path, *arguments)

        assert "user niuu-slot-1 still exists" in findings
        assert self._slots_named(findings) == {"1"}


_TAB = "\t"
_LEFTOVERS = {
    "uname": "echo Darwin",
    "dscl": 'case "$3" in /Users) printf "root\\nniuu-slot-0\\n" ;; '
    '/Groups) printf "admin\\nniuu-slot-0\\n" ;; esac',
    "ps": 'printf "    0     1 /sbin/launchd\\n61000  4242 /opt/agent/bin/my tool\\n"',
    "launchctl": 'case "$2" in\n'
    "  system/world.niuu.slot.0) exit 0 ;;\n"
    f'  user/61000) printf "user/61000 = {{\\n{_TAB}services = {{\\n'
    f"{_TAB}{_TAB}   0  - {_TAB}com.apple.trustd.agent\\n"
    f"{_TAB}{_TAB}   0  - {_TAB}com.example.left.behind\\n"
    f'{_TAB}}}\\n}}\\n" ;;\n'
    "  *) exit 113 ;;\nesac",
    "ipcs": 'printf "T ID KEY MODE OWNER GROUP CREATOR CGROUP\\n'
    'm 65536 0x0 --rw------- 61000 61000 61000 61000\\n"',
    "dseditgroup": '[ "$5" = admin ] && [ "$4" = niuu-slot-0 ]',
    # The temp directories are a Mac's own; where they exist, nothing in them is owned.
    "find": "true",
    "df": 'printf "Filesystem 1M-blocks Used Available Capacity Mounted on\\n'
    '/dev/disk3s1s1 948584 16294 257684 6%% /\\n"',
}


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX sh")
class TestMacAudit:
    def _run(self, tmp_path, programs, *args):
        shims = _shims(tmp_path / "bin", programs)
        result = subprocess.run(
            ["sh", "-s", "--", *args],
            input=DARWIN_AUDIT_SH.encode(),
            capture_output=True,
            env={"PATH": f"{shims}:/usr/bin:/bin"},
            check=True,
        )
        return parse_check_output(result.stdout.decode()).findings

    def test_everything_a_finished_session_left_is_a_finding(self, tmp_path):
        findings = self._run(tmp_path, _LEFTOVERS, "61000", "1", "", "2048", "check")
        assert findings == (
            "user niuu-slot-0 still exists",
            "group niuu-slot-0 still exists",
            "process /opt/agent/bin/my tool (pid 4242) still runs as uid 61000",
            "launchd job world.niuu.slot.0 is still loaded",
            "launchd job com.example.left.behind is still loaded in user/61000",
            "IPC objects created by uid 61000 still exist",
            "niuu-slot-0 is in group admin",
        )

    def test_a_live_slot_is_left_alone_but_its_groups_are_not(self, tmp_path):
        findings = self._run(tmp_path, _LEFTOVERS, "61000", "1", "0", "2048", "check")
        assert findings == ("niuu-slot-0 is in group admin",)

    def test_a_clean_mac(self, tmp_path):
        clean = {
            **_LEFTOVERS,
            "dscl": 'printf "root\\n"',
            "ps": 'printf "    0     1 /sbin/launchd\\n"',
            "launchctl": "exit 113",
            "ipcs": "true",
            "dseditgroup": "exit 67",
        }
        assert self._run(tmp_path, clean, "61000", "1", "", "2048", "check") == ()
        assert self._run(tmp_path, clean, "61000", "1", "", "999999", "check") == (
            "only 257684 MiB free on /, below the floor of 999999 MiB",
        )

    def test_more_than_one_slot_is_a_finding(self, tmp_path):
        clean = {**_LEFTOVERS, "dscl": "true", "ps": "true", "launchctl": "exit 1", "ipcs": "true"}
        clean["dseditgroup"] = "exit 67"
        assert self._run(tmp_path, clean, "61000", "2", "", "2048", "check") == (
            "macOS hosts run one slot, but this one is set to 2",
        )

    def test_a_host_that_is_not_a_mac(self, tmp_path):
        findings = self._run(tmp_path, {"uname": "echo Linux"}, "61000", "1", "", "2048", "check")
        assert findings == (
            "this host is not macOS (uname -s says Linux); remove it and join it again",
        )


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX sh")
def test_mac_facts_read_sysctl_and_sw_vers(tmp_path):
    shims = _shims(
        tmp_path / "bin",
        {
            "uname": 'case "$1" in -s) echo Darwin ;; -r) echo 25.5.0 ;; -m) echo arm64 ;; esac',
            "sysctl": 'case "$2" in hw.ncpu) echo 14 ;; hw.memsize) echo 38654705664 ;; esac',
            "df": _LEFTOVERS["df"],
            "sw_vers": 'case "$1" in -productName) echo macOS ;; *) echo 26.5.1 ;; esac',
        },
    )
    result = subprocess.run(
        ["sh", "-s"],
        input=DARWIN_FACTS_SH.encode(),
        capture_output=True,
        env={"PATH": f"{shims}:/usr/bin:/bin"},
        check=True,
    )
    assert parse_check_output(result.stdout.decode()).facts == {
        "platform": "darwin",
        "cpus": 14,
        "memory_mib": 36864,
        "disk_free_mib": 257684,
        "kernel": "25.5.0",
        "arch": "arm64",
        "cgroup_v2": False,
        "gpus": 0,
        "os": "macOS 26.5.1",
    }


def test_direct_ssh_sets_up_linux_and_macos_alike():
    assert DirectSshConnector(allowed_sources=[]).platforms == {
        HostPlatform.LINUX,
        HostPlatform.DARWIN,
    }


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
