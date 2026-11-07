"""The slot supervisor against a simulated VM: every command it runs is recorded.

``FakeVm`` keeps the state the real commands would change (accounts, the holder
container, the network namespace, links, netfilter rules, the slice), so a test
can run ``up`` and ``down`` end to end and ask what is left.
"""

import base64
import io
import json
import os
import pathlib
import shlex
import shutil
import subprocess
import sys
from collections import namedtuple
from pathlib import Path

import pytest

from volundr.adapters.outbound import vm_slot_supervisor as module
from volundr.adapters.outbound.vm_slot_supervisor import SlotError, SlotSupervisor

Account = namedtuple("Account", "pw_name pw_uid pw_gid")
Group = namedtuple("Group", "gr_name gr_gid")
ALLOCATION = "6c1f6a1e-2f53-4e7e-9d1c-6b4a3c2f1e10"
IMAGE_PASSWD = "root:x:0:0:root:/root:/bin/bash\nskuld:x:1000:1000::/home/skuld:/bin/bash\n"
IMAGE_GROUP = "root:x:0:\nskuld:x:1000:\n"


class FakeVm:
    """The parts of a VM the supervisor reads and changes."""

    def __init__(self, root: Path):
        self.root = root
        self.users: dict[str, Account] = {}
        self.groups: dict[str, Group] = {}
        self.holder: dict | None = None
        self.containers: list[str] = []
        self.links: set[str] = set()
        self.slot_addresses: set[str] = set()
        self.rules: list[tuple[str, str, tuple[str, ...]]] = []
        self.foreign_rules: list[str] = []
        self.slice_active = False
        self.routes = [{"dst": "default", "dev": "enp1s0"}]
        self.image_passwd = IMAGE_PASSWD
        self.commands: list[list[str]] = []
        self.fail: dict[str, str] = {}
        self.netns_inode = 7
        self.holder_inode = 7
        self.chowned: list[tuple[str, int, int]] = []
        # The slot's systemd units, in a slot that runs no container daemon.
        self.units: list[str] = []
        self.reset: list[str] = []

    # -- account database -----------------------------------------------

    def getpwnam(self, name):
        return self.users[name]

    def getpwuid(self, uid):
        for account in self.users.values():
            if account.pw_uid == uid:
                return account
        raise KeyError(uid)

    def getgrnam(self, name):
        return self.groups[name]

    def getgrgid(self, gid):
        for group in self.groups.values():
            if group.gr_gid == gid:
                return group
        raise KeyError(gid)

    # -- commands --------------------------------------------------------

    def probe(self, argv: list[str]) -> bool:
        self.commands.append(["probe", *argv])
        match argv:
            case ["docker", "container", "inspect", _]:
                return self.holder is not None
            case ["ip", "link", "show", "dev", link]:
                return link in self.links
            case ["iptables", "-w", "-t", table, "-C", chain, *rule]:
                return (table, chain, tuple(rule)) in self.rules
            case ["systemctl", "is-active", "--quiet", _]:
                return self.slice_active
        raise AssertionError(f"unexpected probe {argv}")

    def run(self, argv: list[str], *, check: bool = True, input_text=None) -> str:
        self.commands.append(argv)
        for prefix, message in self.fail.items():
            if shlex.join(argv).startswith(prefix):
                raise SlotError(message)
        netns = self.root / "netns"
        match argv:
            case ["groupadd", "--gid", gid, name]:
                self.groups[name] = Group(name, int(gid))
            case ["useradd", "--uid", uid, "--gid", gid, *_, name]:
                self.users[name] = Account(name, int(uid), int(gid))
            case ["userdel", name]:
                del self.users[name]
            case ["groupdel", name]:
                del self.groups[name]
            case ["docker", "run", "--rm", "--network", "none", "--entrypoint", "cat", _, path]:
                return self.image_passwd if path == "/etc/passwd" else IMAGE_GROUP
            case ["docker", "run", "--detach", *rest]:
                labels = dict(
                    rest[index + 1].split("=", 1)
                    for index, arg in enumerate(rest)
                    if arg == "--label"
                )
                self.holder = {
                    "Config": {"Labels": labels},
                    "State": {"Running": True, "Pid": 4242},
                }
                self.containers.append("holder-id")
            case ["docker", "container", "inspect", _]:
                return json.dumps([self.holder])
            case ["docker", "start", _]:
                self.holder["State"] = {"Running": True, "Pid": 4343}
            case ["docker", "ps", *_]:
                return "\n".join(self.containers)
            case ["docker", "rm", "--force", "--volumes", container]:
                self.containers.remove(container)
                self.holder = None
            case ["ip", "netns", "attach", name, _]:
                netns.mkdir(exist_ok=True)
                (netns / name).write_text("")
                self.netns_inode = self.holder_inode
            case ["ip", "netns", "add", name]:
                netns.mkdir(exist_ok=True)
                (netns / name).write_text("")
            case ["ip", "netns", "delete", name]:
                (netns / name).unlink()
            case ["ip", "-j", "route", "show", "default"]:
                return json.dumps(self.routes)
            case ["ip", "link", "add", link, *_]:
                self.links.add(link)
            case ["ip", "link", "delete", link]:
                self.links.discard(link)
                self.slot_addresses.clear()
            case ["ip", "netns", "exec", _, "ip", "addr", "add", cidr, *_]:
                self.slot_addresses.add(cidr)
            case ["ip", "netns", "exec", _, "ip", "-o", "-4", "addr", "show", *_]:
                return "".join(
                    f"2: eth0 inet {cidr} scope global\n" for cidr in self.slot_addresses
                )
            case ["iptables", "-w", "-t", table, "-I", chain, "1", *rule]:
                self.rules.insert(0, (table, chain, tuple(rule)))
            case ["iptables", "-w", "-t", table, "-D", chain, *rule]:
                self.rules.remove((table, chain, tuple(rule)))
            case ["iptables-save", "-t", table]:
                lines = [f"*{table}", *self.foreign_rules]
                lines += [shlex.join(["-A", c, *r]) for t, c, r in self.rules if t == table]
                return "\n".join([*lines, "COMMIT"]) + "\n"
            case ["systemctl", "set-property", "--runtime", name, *_]:
                (self.root / "control" / f"{name}.d").mkdir(parents=True, exist_ok=True)
                (self.root / "cgroup" / name).mkdir(parents=True, exist_ok=True)
            case ["systemctl", "list-units", *_]:
                return "".join(f"{unit} loaded active running Pod\n" for unit in self.units)
            case ["systemctl", "stop", name]:
                self.slice_active = False
                if name in self.units:
                    self.units.remove(name)
            case ["systemctl", "reset-failed", name]:
                self.reset.append(name)
            case ["systemctl", "revert", name]:
                (self.root / "control" / f"{name}.d").rmdir()
                (self.root / "cgroup" / name).rmdir()
        return ""

    def ran(self, *prefix: str) -> list[list[str]]:
        return [argv for argv in self.commands if argv[: len(prefix)] == list(prefix)]


def _spec(root: Path, **overrides) -> dict:
    spec = {
        "slot": 3,
        "allocation_id": ALLOCATION,
        "name": "niuu-slot-3",
        "uid": 61003,
        "data_root": str(root / "var" / "slots" / "3"),
        "run_root": str(root / "run" / "slots" / "3"),
        "agent_root": str(root / "etc" / "slots" / "3"),
        "netns": "niuu-slot-3",
        "host_link": "niuuslot3",
        "gateway": "10.249.0.13",
        "address": "10.249.0.14",
        "prefix_length": 30,
        "platform_port": 18080,
        "platform_forward_port": 18103,
        "slice": "niuu_slot_3.slice",
        "limits": {"cpu": 1.5, "memory_mib": 2048, "pids": 512},
        "holder": "niuu-slot-3-holder",
        "image": "registry/skuld@sha256:abc",
        "image_user": "skuld",
    }
    spec.update(overrides)
    return spec


@pytest.fixture
def vm(tmp_path, monkeypatch):
    fake = FakeVm(tmp_path)
    monkeypatch.setattr(module, "pwd", fake)
    monkeypatch.setattr(module, "grp", fake)
    monkeypatch.setattr(module, "_NETNS_DIR", tmp_path / "netns")
    monkeypatch.setattr(module, "_SYSTEMD_CONTROL", tmp_path / "control")
    monkeypatch.setattr(module, "_CGROUP_ROOT", tmp_path / "cgroup")
    monkeypatch.setattr(
        module.os, "fchown", lambda fd, uid, gid: fake.chowned.append((_fd_path(fd), uid, gid))
    )
    return fake


def _fd_path(fd: int) -> str:
    """The path an open descriptor names (Linux and macOS)."""
    if sys.platform == "darwin":
        import fcntl

        raw = fcntl.fcntl(fd, fcntl.F_GETPATH, bytes(1024))
        path = raw.split(b"\0", 1)[0].decode()
    else:
        path = os.readlink(f"/proc/self/fd/{fd}")
    # macOS reports /private/var for tmp paths reached through /var.
    return path.removeprefix("/private") if path.startswith("/private/var/") else path


def _real(path) -> str:
    resolved = str(pathlib.Path(path).resolve())
    return resolved.removeprefix("/private") if resolved.startswith("/private/var/") else resolved


def _supervisor(vm: FakeVm, **overrides) -> SlotSupervisor:
    supervisor = SlotSupervisor(_spec(vm.root, **overrides), runner=vm.run, probe=vm.probe)
    supervisor._inode = lambda path: vm.netns_inode if "/netns/" in path else vm.holder_inode
    return supervisor


def test_up_creates_the_whole_slot_boundary(vm):
    supervisor = _supervisor(vm)

    assert supervisor.up() == {"slot": 3, "address": "10.249.0.14", "gateway": "10.249.0.13"}

    assert vm.groups["niuu-slot-3"] == Group("niuu-slot-3", 61003)
    assert vm.users["niuu-slot-3"] == Account("niuu-slot-3", 61003, 61003)
    useradd = vm.ran("useradd")[0]
    assert "/usr/sbin/nologin" in useradd and str(supervisor.home) in useradd
    for path in (supervisor.data_root, supervisor.home, supervisor.workspace, supervisor.run_root):
        assert path.is_dir() and path.stat().st_mode & 0o777 == 0o700
        assert (_real(path), 61003, 61003) in vm.chowned
    # The slot's root agents' state: root's, readable by the slot's group, closed to others.
    assert supervisor.agent_root.is_dir() and supervisor.agent_root.stat().st_mode & 0o777 == 0o750
    assert (_real(supervisor.agent_root), 0, 61003) in vm.chowned
    passwd = (supervisor.identity_directory / "passwd").read_text()
    assert "skuld:x:61003:61003::/home/skuld:/bin/bash" in passwd
    assert "root:x:0:0:root:/root:/bin/bash" in passwd
    assert "skuld:x:61003:" in (supervisor.identity_directory / "group").read_text()
    assert vm.ran("systemctl", "set-property")[0][-3:] == [
        "CPUQuota=150%",
        "MemoryMax=2048M",
        "TasksMax=512",
    ]
    holder = vm.ran("docker", "run", "--detach")[0]
    joined = shlex.join(holder)
    assert "--network none" in joined and "--cgroup-parent niuu_slot_3.slice" in joined
    assert "--user 61003:61003" in joined and "--no-healthcheck" in joined
    assert f"--label compute.niuu.io/allocation={ALLOCATION}" in joined
    assert "--label compute.niuu.io/slot=3" in joined
    assert vm.ran("ip", "netns", "attach") == [["ip", "netns", "attach", "niuu-slot-3", "4242"]]
    assert vm.links == {"niuuslot3"} and vm.slot_addresses == {"10.249.0.14/30"}
    assert ["sysctl", "-qw", "net.ipv4.conf.niuuslot3.route_localnet=1"] in vm.commands
    assert [
        "ip",
        "netns",
        "exec",
        "niuu-slot-3",
        "ip",
        "route",
        "replace",
        "default",
        "via",
        "10.249.0.13",
    ] in vm.commands


def test_rules_keep_accepts_above_drops_and_tag_every_rule(vm):
    supervisor = _supervisor(vm)
    supervisor.up()

    assert [(t, c, r) for t, c, r in vm.rules] == [
        (t, c, tuple(r)) for t, c, r in supervisor.rules("enp1s0")
    ]
    assert all(
        ("--comment", "niuu-slot-3") == r[r.index("--comment") :][:2] for _, _, r in vm.rules
    )
    input_targets = [r[-1] for t, c, r in vm.rules if c == "INPUT"]
    assert input_targets == ["ACCEPT", "ACCEPT", "DROP"]
    forward = [r for t, c, r in vm.rules if c == "FORWARD"]
    assert [r[-1] for r in forward] == ["ACCEPT", "ACCEPT", "DROP", "DROP"]
    dnat = next(r for t, c, r in vm.rules if c == "PREROUTING")
    assert dnat[-2:] == ("--to-destination", "127.0.0.1:18103")
    assert "10.249.0.13/32" in dnat and "18080" in dnat


def test_up_again_changes_nothing(vm):
    supervisor = _supervisor(vm)
    supervisor.up()
    vm.commands.clear()

    supervisor.up()

    for kind in ("groupadd", "useradd", "ip netns attach", "ip link add", "iptables -w -t nat -I"):
        assert not [argv for argv in vm.commands if shlex.join(argv).startswith(kind)], kind
    assert not vm.ran("docker", "run", "--detach")


def test_unlimited_resources_clear_every_limit(vm):
    _supervisor(vm, limits={"cpu": None, "memory_mib": None, "pids": None}).up()

    assert vm.ran("systemctl", "set-property")[0][-3:] == [
        "CPUQuota=",
        "MemoryMax=infinity",
        "TasksMax=infinity",
    ]


@pytest.mark.parametrize(
    ("prepare", "message"),
    [
        (lambda vm: vm.groups.update({"niuu-slot-3": Group("niuu-slot-3", 5)}), "gid 5"),
        (lambda vm: vm.groups.update({"other": Group("other", 61003)}), "uid_base"),
        (
            lambda vm: vm.users.update({"niuu-slot-3": Account("niuu-slot-3", 61003, 1)}),
            "61003:1",
        ),
        (lambda vm: vm.users.update({"ubuntu": Account("ubuntu", 61003, 61003)}), "ubuntu"),
        (lambda vm: setattr(vm, "image_passwd", "root:x:0:0::/root:/bin/sh\n"), "image_user"),
        (lambda vm: vm.routes.append({"dst": "default", "dev": "enp2s0"}), "default-route"),
    ],
)
def test_up_refuses_a_vm_that_does_not_fit_the_slot(vm, prepare, message):
    prepare(vm)

    with pytest.raises(SlotError, match=message):
        _supervisor(vm).up()


def test_up_refuses_a_holder_of_another_slot(vm):
    vm.holder = {
        "Config": {"Labels": {module.ALLOCATION_LABEL: ALLOCATION, module.SLOT_LABEL: "4"}},
        "State": {"Running": True, "Pid": 1},
    }

    with pytest.raises(SlotError, match="another allocation or slot"):
        _supervisor(vm).up()


def test_up_restarts_a_stopped_holder_and_replumbs_its_new_namespace(vm):
    supervisor = _supervisor(vm)
    supervisor.up()
    vm.holder["State"] = {"Running": False, "Pid": 0}
    vm.holder_inode = 9
    vm.commands.clear()

    supervisor.up()

    assert vm.ran("docker", "start") == [["docker", "start", "niuu-slot-3-holder"]]
    assert vm.ran("ip", "link", "delete") == [["ip", "link", "delete", "niuuslot3"]]
    assert vm.ran("ip", "netns", "delete") and vm.ran("ip", "netns", "attach")[0][-1] == "4343"
    assert vm.ran("ip", "link", "add") and vm.slot_addresses == {"10.249.0.14/30"}


def test_up_recreates_a_link_without_the_slots_address(vm):
    supervisor = _supervisor(vm)
    supervisor.up()
    vm.slot_addresses.clear()
    vm.commands.clear()

    supervisor.up()

    assert vm.ran("ip", "link", "delete") and vm.ran("ip", "link", "add")


def test_a_holder_that_never_runs_fails_up(vm):
    supervisor = _supervisor(vm)
    supervisor._inspect = lambda container: None

    with pytest.raises(SlotError, match="did not start"):
        supervisor.up()


def test_a_holder_without_a_process_fails_up(vm):
    supervisor = _supervisor(vm)
    supervisor.up()
    vm.holder["State"] = {"Running": True, "Pid": 0}

    with pytest.raises(SlotError, match="not running"):
        supervisor.up()


def test_down_removes_everything_and_proves_it(vm):
    supervisor = _supervisor(vm)
    supervisor.up()
    vm.slice_active = True
    (supervisor.workspace / "file").write_text("archived already")
    vm.foreign_rules = ["-A FORWARD -i niuuslot31 -m comment --comment niuu-slot-31 -j DROP"]

    supervisor.down()

    assert supervisor.leftovers() == []
    assert vm.rules == [] and vm.links == set() and vm.users == {} and vm.groups == {}
    assert not supervisor.data_root.exists() and not supervisor.run_root.exists()
    assert not supervisor.agent_root.exists()
    assert vm.ran("docker", "rm") == [["docker", "rm", "--force", "--volumes", "holder-id"]]
    # Another slot's rule whose tag merely starts the same is never touched.
    assert vm.foreign_rules and not [a for a in vm.commands if "niuu-slot-31" in a]


def test_down_of_an_absent_slot_is_a_verified_no_op(vm):
    _supervisor(vm).down()

    assert not vm.ran("userdel") and not vm.ran("ip", "netns", "delete")
    assert vm.ran("systemctl", "stop") == [["systemctl", "stop", "niuu_slot_3.slice"]]


def test_down_fails_loudly_naming_what_is_left(vm):
    supervisor = _supervisor(vm)
    supervisor.up()
    vm.run = _skipping(vm, "userdel")
    supervisor._run = vm.run

    with pytest.raises(SlotError, match="teardown left: user niuu-slot-3"):
        supervisor.down()


def _skipping(vm: FakeVm, command: str):
    original = vm.run

    def run(argv, **kwargs):
        if argv[0] == command:
            vm.commands.append(argv)
            return ""
        return original(argv, **kwargs)

    return run


def test_a_failing_teardown_command_stops_down(vm):
    supervisor = _supervisor(vm)
    supervisor.up()
    vm.fail["userdel"] = "userdel niuu-slot-3 exited 8: user is currently used by process 7"

    with pytest.raises(SlotError, match="currently used"):
        supervisor.down()


def test_leftovers_list_every_kind_of_slot_state(vm):
    supervisor = _supervisor(vm)
    supervisor.up()
    vm.slice_active = True

    left = supervisor.leftovers()

    for expected in (
        "user niuu-slot-3",
        "group niuu-slot-3",
        f"directory {supervisor.data_root}",
        f"directory {supervisor.run_root}",
        f"directory {supervisor.agent_root}",
        "container holder-id",
        "network namespace niuu-slot-3",
        "interface niuuslot3",
        "active slice niuu_slot_3.slice",
        "slice settings",
        "cgroup",
    ):
        assert any(item.startswith(expected) for item in left), expected
    assert sum(item.startswith(("filter rule", "nat rule")) for item in left) == 9


# -- a slot without a container daemon (the host runtime on registered hosts) ---------------------


UNITS = ["niuu-slot-3-sidecar-agent.service", "niuu-slot-3-session-1.scope"]


def _daemonless(vm: FakeVm, **overrides) -> SlotSupervisor:
    return _supervisor(vm, holder=None, **overrides)


def test_a_slot_without_a_holder_is_daemonless(vm):
    assert _daemonless(vm).daemonless is True
    assert _supervisor(vm).daemonless is False
    assert _daemonless(vm).unit_prefix == "niuu-slot-3-"


def test_up_without_a_holder_keeps_a_namespace_of_its_own_and_runs_no_container_engine(vm):
    supervisor = _daemonless(vm)

    assert supervisor.up() == {"slot": 3, "address": "10.249.0.14", "gateway": "10.249.0.13"}

    assert vm.ran("ip", "netns", "add") == [["ip", "netns", "add", "niuu-slot-3"]]
    assert not vm.ran("docker") and not vm.ran("ip", "netns", "attach")
    assert (vm.root / "netns" / "niuu-slot-3").exists()
    # No image is read for the slot's identity files: the pod containers read their own.
    assert not (supervisor.identity_directory / "passwd").exists()
    assert vm.users["niuu-slot-3"] == Account("niuu-slot-3", 61003, 61003)
    assert vm.links == {"niuuslot3"} and vm.slot_addresses == {"10.249.0.14/30"}
    assert vm.ran("systemctl", "set-property")


def test_up_again_without_a_holder_keeps_its_namespace(vm):
    supervisor = _daemonless(vm)
    supervisor.up()
    vm.commands.clear()

    supervisor.up()

    assert not vm.ran("ip", "netns", "add") and not vm.ran("docker")
    assert not vm.ran("useradd") and not vm.ran("ip", "link", "add")


def test_the_slots_units_are_listed_by_their_prefix_services_and_scopes(vm):
    vm.units = [*UNITS]
    supervisor = _daemonless(vm)

    assert supervisor.units() == UNITS
    assert vm.ran("systemctl", "list-units") == [
        [
            "systemctl",
            "list-units",
            "--all",
            "--plain",
            "--no-legend",
            "--type=service,scope",
            "niuu-slot-3-*",
        ]
    ]


def test_a_daemonless_slot_has_no_containers_to_list(vm):
    assert _daemonless(vm).containers() == []
    assert not vm.ran("docker")


def test_down_without_a_holder_stops_and_resets_every_unit_then_proves_it(vm):
    supervisor = _daemonless(vm)
    supervisor.up()
    vm.units = [*UNITS]

    supervisor.down()

    for unit in UNITS:
        assert ["systemctl", "stop", unit] in vm.commands
        assert ["systemctl", "reset-failed", unit] in vm.commands
    assert vm.units == [] and supervisor.leftovers() == []
    assert vm.rules == [] and vm.links == set() and vm.users == {}
    assert not vm.ran("docker")


def test_down_resets_a_unit_without_failing_when_it_was_never_failed(vm):
    supervisor = _daemonless(vm)
    supervisor.up()
    vm.units = [UNITS[0]]
    original = vm.run

    def run(argv, **kwargs):
        # ``systemctl reset-failed`` exits non-zero for a unit that is not failed.
        if argv[:2] == ["systemctl", "reset-failed"]:
            assert kwargs["check"] is False
        return original(argv, **kwargs)

    supervisor._run = run

    supervisor.down()

    assert supervisor.leftovers() == []


def test_a_unit_that_will_not_stop_fails_down_naming_it(vm):
    supervisor = _daemonless(vm)
    supervisor.up()
    vm.units = [UNITS[0]]
    original = vm.run

    def run(argv, **kwargs):
        if argv == ["systemctl", "stop", UNITS[0]]:
            vm.commands.append(argv)
            return ""
        return original(argv, **kwargs)

    supervisor._run = run

    with pytest.raises(SlotError, match=f"teardown left: unit {UNITS[0]}"):
        supervisor.down()


def test_leftovers_of_a_daemonless_slot_list_its_loaded_units_not_containers(vm):
    supervisor = _daemonless(vm)
    supervisor.up()
    vm.units = [*UNITS]

    left = supervisor.leftovers()

    assert [item for item in left if item.startswith("unit ")] == [f"unit {u}" for u in UNITS]
    assert not [item for item in left if item.startswith("container ")]
    assert "network namespace niuu-slot-3" in left


def test_a_slot_with_a_holder_never_lists_units(vm):
    supervisor = _supervisor(vm)
    supervisor.up()

    supervisor.leftovers()
    supervisor.down()

    assert not vm.ran("systemctl", "list-units") and not vm.ran("systemctl", "reset-failed")


def test_down_refuses_a_symlinked_slot_directory(vm, tmp_path):
    supervisor = _supervisor(vm)
    target = tmp_path / "elsewhere"
    target.mkdir()
    supervisor.data_root.parent.mkdir(parents=True)
    supervisor.data_root.symlink_to(target)

    with pytest.raises(SlotError, match="symbolic link"):
        supervisor.down()
    assert target.exists()


@pytest.mark.parametrize("child", ["session", "identity"])
def test_up_refuses_a_slot_directory_the_slot_turned_into_a_link(vm, tmp_path, child):
    supervisor = _supervisor(vm)
    supervisor.up()
    target = tmp_path / "victim"
    target.mkdir()
    replaced = supervisor.data_root / child
    shutil.rmtree(replaced)
    replaced.symlink_to(target)

    with pytest.raises(SlotError, match="symbolic link"):
        supervisor.up()
    # Nothing was owned or written through the link.
    assert all(_real(target) != path for path, _, _ in vm.chowned)
    assert list(target.iterdir()) == []


def test_up_refuses_an_agent_directory_reached_through_a_link(vm, tmp_path):
    supervisor = _supervisor(vm)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    supervisor.agent_root.parent.parent.mkdir(parents=True)
    supervisor.agent_root.parent.symlink_to(elsewhere)

    with pytest.raises(SlotError, match="symbolic link"):
        supervisor.up()
    assert list(elsewhere.iterdir()) == []


def test_up_refuses_a_symlinked_slot_directory(vm, tmp_path, monkeypatch):
    supervisor = _supervisor(vm)
    target = tmp_path / "elsewhere"
    target.mkdir()
    supervisor.data_root.parent.mkdir(parents=True)
    supervisor.data_root.symlink_to(target)

    with pytest.raises(SlotError, match="symbolic link"):
        supervisor.up()


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"data_root": "relative/slots/3"}, "slots/3"),
        ({"run_root": "/run/niuu/slots/4"}, "slots/3"),
        ({"agent_root": "/etc/niuu/agents/3"}, "slots/3"),
        ({"data_root": "/var/lib/niuu/other/3"}, "slots/3"),
        ({"data_root": "/var/lib/niuu/slots/../3"}, "slots/3"),
        ({"slice": "niuu-slot-3.slice"}, "top-level"),
        ({"slice": "niuu_slot_3"}, "top-level"),
    ],
)
def test_spec_refuses_paths_and_slices_it_could_not_remove_safely(tmp_path, override, message):
    with pytest.raises(SlotError, match=message):
        SlotSupervisor(_spec(tmp_path, **override))


def test_limit_events_count_memory_and_pid_limit_hits(vm):
    supervisor = _supervisor(vm)
    assert supervisor.limit_events() == {}
    supervisor.cgroup.mkdir(parents=True)
    (supervisor.cgroup / "memory.events").write_text("low 0\nhigh 0\nmax 4\noom 1\noom_kill 2\n")
    (supervisor.cgroup / "pids.events").write_text("max 3\n")

    assert supervisor.limit_events() == {"memory.max": 4, "memory.oom_kill": 2, "pids.max": 3}


def test_limit_events_without_counters_report_none(vm):
    supervisor = _supervisor(vm)
    supervisor.cgroup.mkdir(parents=True)

    assert supervisor.limit_events() == {}


def test_run_raises_with_the_command_and_its_error():
    assert module.run([sys.executable, "-c", "print('out')"]) == "out\n"
    assert module.succeeds([sys.executable, "-c", "pass"])
    assert not module.succeeds([sys.executable, "-c", "raise SystemExit(3)"])
    with pytest.raises(SlotError, match=r"exited 3: boom"):
        module.run([sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(3)"])


def test_diagnostic_is_one_printable_line_and_marks_truncation():
    assert module.diagnostic("a\nb\tc\x00d") == "a b c d"
    long = module.diagnostic("x" * 2000)
    assert len(long) == module.DIAGNOSTIC_LIMIT and long.endswith(" [truncated]")


def test_main_reports_a_failure_as_a_stage_and_decodable_detail(vm, capsys, monkeypatch):
    def failing(spec):
        raise SlotError("Slot 3 teardown left: user niuu-slot-3")

    monkeypatch.setattr(module, "SlotSupervisor", failing)

    assert module.main(["-c", "down"], io.StringIO("{}")) == 1

    lines = capsys.readouterr().err.splitlines()
    assert lines[0] == f"{module.STAGE_PREFIX}slot-down"
    detail = lines[1].removeprefix(module.DETAIL_PREFIX)
    assert base64.b64decode(detail).decode() == "Slot 3 teardown left: user niuu-slot-3"


def test_main_runs_each_action(vm, capsys, monkeypatch):
    spec = json.dumps(_spec(vm.root))

    def build(loaded):
        supervisor = SlotSupervisor(loaded, runner=vm.run, probe=vm.probe)
        supervisor._inode = lambda path: 7
        return supervisor

    monkeypatch.setattr(module, "SlotSupervisor", build)

    assert module.main(["-c", "up"], io.StringIO(spec)) == 0
    assert json.loads(capsys.readouterr().out)["address"] == "10.249.0.14"
    assert module.main(["-c", "verify"], io.StringIO(spec)) == 1
    assert json.loads(capsys.readouterr().out)["leftovers"]
    assert module.main(["-c", "limits"], io.StringIO(spec)) == 0
    assert json.loads(capsys.readouterr().out) == {}
    assert module.main(["-c", "down"], io.StringIO(spec)) == 0
    assert module.main(["-c", "verify"], io.StringIO(spec)) == 0
    assert json.loads(capsys.readouterr().out.splitlines()[-1]) == {"leftovers": []}
    assert module.main(["-c"], io.StringIO(spec)) == 2
    assert module.main(["-c", "explode"], io.StringIO(spec)) == 2


def test_the_shipped_source_is_a_standalone_program():
    """The guest has no Niuu packages: the module must run from its source alone."""
    import inspect

    source = inspect.getsource(module)
    completed = subprocess.run(
        [sys.executable, "-I", "-c", source, "nonsense"], capture_output=True, text=True
    )

    assert completed.returncode == 2
    assert "usage: vm_slot_supervisor" in completed.stderr
    assert "import volundr" not in source and "from volundr" not in source


def test_a_namespace_is_identified_by_its_inode(tmp_path):
    assert SlotSupervisor._inode(str(tmp_path)) == tmp_path.stat().st_ino
