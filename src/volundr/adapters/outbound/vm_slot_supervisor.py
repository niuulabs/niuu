"""Create and remove one session slot's boundary on a session VM.

This module is a guest program: the VM runtime ships its source over the pinned
SSH channel and runs it as root (``python3 -c <source> <action>`` with the slot
spec as JSON on stdin), the way it runs every other guest step. It is never a
systemd unit, and it imports only the standard library, because the guest has
nothing else.

For slot ``n`` it owns:

* a Linux user and group with a fixed id (``niuu-slot-<n>``) and the slot's
  data and runtime directories (mode 0700, owned by that user), which hold its
  home, workspace and credential files;
* a holder container whose network and PID namespaces every container of the
  slot joins, attached as the named network namespace ``niuu-slot-<n>``;
* a veth pair into that namespace with its own address, NAT out of the VM's
  uplink, a DNAT from the slot's gateway to the slot's Forge callback forward
  on the VM's loopback, and filter rules that let the slot reach nothing else on
  the VM and no other slot;
* a cgroup v2 (a systemd slice) with the slot's CPU, memory and PID limits,
  holding every container of the slot.

``down`` removes all of it and then proves nothing is left; anything left makes
it fail, naming what remains. Every step is idempotent, so a failed ``up`` or
``down`` is retried by running it again.
"""

from __future__ import annotations

import base64
import grp
import json
import os
import pathlib
import pwd
import shlex
import shutil
import subprocess
import sys

#: Label of every container of a slot, next to the allocation label.
SLOT_LABEL = "compute.niuu.io/slot"
ALLOCATION_LABEL = "compute.niuu.io/allocation"
#: The interface name of the slot's end of the veth pair, inside its namespace.
SLOT_INTERFACE = "eth0"
#: stderr prefixes the runtime turns into a ``VmRuntimeStageError``.
STAGE_PREFIX = "NIUU_SLOT_STAGE:"
DETAIL_PREFIX = "NIUU_SLOT_DETAIL:"
#: The longest diagnostic ``SshGuestAccess`` passes on from a guest command.
DIAGNOSTIC_LIMIT = 800
_NETNS_DIR = pathlib.Path("/run/netns")
_SYSTEMD_CONTROL = pathlib.Path("/run/systemd/system.control")
_CGROUP_ROOT = pathlib.Path("/sys/fs/cgroup")


class SlotError(RuntimeError):
    """A slot could not be created, stopped or removed as specified."""


def run(argv: list[str], *, check: bool = True, input_text: str | None = None) -> str:
    """Run one command; a failure raises with the command, never silently."""
    completed = subprocess.run(argv, capture_output=True, text=True, input=input_text)
    if check and completed.returncode != 0:
        raise SlotError(
            f"{shlex.join(argv)} exited {completed.returncode}: {completed.stderr.strip()}"
        )
    return completed.stdout


def succeeds(argv: list[str]) -> bool:
    return subprocess.run(argv, capture_output=True).returncode == 0


class SlotSupervisor:
    """The slot described by one spec (built by ``vm_slot_layout.SlotLayout``)."""

    def __init__(self, spec: dict, *, runner=run, probe=succeeds) -> None:
        self.spec = spec
        self.slot = int(spec["slot"])
        self.allocation = str(spec["allocation_id"])
        self.name = str(spec["name"])
        self.uid = int(spec["uid"])
        self.data_root = self._slot_directory(spec["data_root"])
        self.run_root = self._slot_directory(spec["run_root"])
        self.netns = str(spec["netns"])
        self.link = str(spec["host_link"])
        self.gateway = str(spec["gateway"])
        self.address = str(spec["address"])
        self.prefix_length = int(spec["prefix_length"])
        self.platform_port = int(spec["platform_port"])
        self.platform_forward_port = int(spec["platform_forward_port"])
        self.slice = str(spec["slice"])
        # A top-level slice: a dash would nest it under a parent slice shared by slots.
        if "-" in self.slice or "/" in self.slice or not self.slice.endswith(".slice"):
            raise SlotError("The slot slice must be a top-level systemd slice name")
        self.limits = dict(spec["limits"])
        self.holder = str(spec["holder"])
        self.image = str(spec["image"])
        self.image_user = str(spec["image_user"])
        self.comment = self.name
        self._run = runner
        self._succeeds = probe

    def _slot_directory(self, value: str) -> pathlib.Path:
        path = pathlib.PurePosixPath(value)
        # Removed recursively by ``down``: refuse anything but ``.../slots/<n>``.
        if (
            not path.is_absolute()
            or ".." in path.parts
            or path.name != str(self.slot)
            or path.parent.name != "slots"
        ):
            raise SlotError(f"Slot directories must be absolute .../slots/{self.slot} paths")
        return pathlib.Path(path)

    # -- paths --------------------------------------------------------------

    @property
    def home(self) -> pathlib.Path:
        return self.data_root / "session" / "home"

    @property
    def workspace(self) -> pathlib.Path:
        return self.data_root / "session" / "workspace"

    @property
    def identity_directory(self) -> pathlib.Path:
        """``passwd``/``group`` for the slot's containers: the image's, with the slot's id."""
        return self.data_root / "identity"

    @property
    def cgroup(self) -> pathlib.Path:
        return _CGROUP_ROOT / self.slice

    @property
    def labels(self) -> list[str]:
        return [f"{ALLOCATION_LABEL}={self.allocation}", f"{SLOT_LABEL}={self.slot}"]

    # -- up -----------------------------------------------------------------

    def up(self) -> dict:
        self._ensure_identity()
        self._ensure_directories()
        self._ensure_image_identity()
        self._ensure_slice()
        pid = self._ensure_holder()
        self._ensure_netns(pid)
        uplink = self._uplink()
        self._ensure_link()
        self._ensure_rules(uplink)
        return {"slot": self.slot, "address": self.address, "gateway": self.gateway}

    def _ensure_identity(self) -> None:
        try:
            group = grp.getgrnam(self.name)
        except KeyError:
            group = None
        if group is not None and group.gr_gid != self.uid:
            raise SlotError(f"Group {self.name} exists with gid {group.gr_gid}, not {self.uid}")
        if group is None:
            self._require_free_id(grp.getgrgid, "gid")
            self._run(["groupadd", "--gid", str(self.uid), self.name])
        try:
            user = pwd.getpwnam(self.name)
        except KeyError:
            user = None
        if user is not None and (user.pw_uid, user.pw_gid) != (self.uid, self.uid):
            raise SlotError(
                f"User {self.name} exists as {user.pw_uid}:{user.pw_gid}, not {self.uid}"
            )
        if user is None:
            self._require_free_id(pwd.getpwuid, "uid")
            self._run(
                [
                    "useradd",
                    "--uid",
                    str(self.uid),
                    "--gid",
                    str(self.uid),
                    "--no-create-home",
                    "--home-dir",
                    str(self.home),
                    "--shell",
                    "/usr/sbin/nologin",
                    self.name,
                ]
            )

    def _require_free_id(self, lookup, kind: str) -> None:
        try:
            taken = lookup(self.uid)
        except KeyError:
            return
        owner = getattr(taken, "pw_name", None) or getattr(taken, "gr_name", "")
        raise SlotError(
            f"{kind} {self.uid} for {self.name} already belongs to {owner}; "
            "choose another slot_isolation.uid_base"
        )

    def _ensure_directories(self) -> None:
        for path in (
            self.data_root,
            self.data_root / "session",
            self.home,
            self.workspace,
            self.identity_directory,
            self.run_root,
        ):
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            if path.is_symlink():
                raise SlotError(f"Slot directory {path} is a symbolic link")
            os.chown(path, self.uid, self.uid)
            path.chmod(0o700)

    def _ensure_image_identity(self) -> None:
        """Give the image's session account the slot's id inside the slot's containers.

        Tools read the account database (``os.userInfo``, git's identity), so the
        slot's uid must have an entry there; the image's account keeps its name,
        home and shell and only its numbers change.
        """
        for name, field_count in (("passwd", 7), ("group", 4)):
            content = self._run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--network",
                    "none",
                    "--entrypoint",
                    "cat",
                    self.image,
                    f"/etc/{name}",
                ]
            )
            lines, found = [], False
            for line in content.splitlines():
                fields = line.split(":")
                if len(fields) == field_count and fields[0] == self.image_user:
                    found = True
                    fields[2] = str(self.uid)
                    if name == "passwd":
                        fields[3] = str(self.uid)
                lines.append(":".join(fields))
            if not found:
                raise SlotError(
                    f"Image {self.image} has no {name} entry {self.image_user!r}; "
                    "set slot_isolation.image_user"
                )
            target = self.identity_directory / name
            temporary = target.with_name(name + ".niuu-tmp")
            temporary.write_text("\n".join(lines) + "\n")
            temporary.chmod(0o644)
            os.replace(temporary, target)

    def _ensure_slice(self) -> None:
        cpu = self.limits.get("cpu")
        memory = self.limits.get("memory_mib")
        pids = self.limits.get("pids")
        self._run(
            [
                "systemctl",
                "set-property",
                "--runtime",
                self.slice,
                # An empty CPUQuota removes the quota: ``null`` is "no limit".
                f"CPUQuota={round(float(cpu) * 100)}%" if cpu is not None else "CPUQuota=",
                f"MemoryMax={int(memory)}M" if memory is not None else "MemoryMax=infinity",
                f"TasksMax={int(pids)}" if pids is not None else "TasksMax=infinity",
            ]
        )

    def _inspect(self, container: str) -> dict | None:
        if not self._succeeds(["docker", "container", "inspect", container]):
            return None
        return json.loads(self._run(["docker", "container", "inspect", container]))[0]

    def _ensure_holder(self) -> int:
        info = self._inspect(self.holder)
        if info is None:
            self._run(
                [
                    "docker",
                    "run",
                    "--detach",
                    "--init",
                    "--restart",
                    "unless-stopped",
                    "--name",
                    self.holder,
                    # The image's health check probes Skuld, which the holder never runs.
                    "--no-healthcheck",
                    # Never the image's own uid, which may be a VM account.
                    "--user",
                    f"{self.uid}:{self.uid}",
                    "--network",
                    "none",
                    "--cgroup-parent",
                    self.slice,
                    *[arg for label in self.labels for arg in ("--label", label)],
                    "--entrypoint",
                    "sleep",
                    self.image,
                    "infinity",
                ]
            )
            info = self._inspect(self.holder)
            if info is None:
                raise SlotError(f"Slot holder {self.holder} did not start")
        labels = info["Config"].get("Labels") or {}
        if labels.get(ALLOCATION_LABEL) != self.allocation or labels.get(SLOT_LABEL) != str(
            self.slot
        ):
            raise SlotError(f"Container {self.holder} belongs to another allocation or slot")
        if not info["State"].get("Running"):
            self._run(["docker", "start", self.holder])
            info = self._inspect(self.holder) or {"State": {}}
        pid = int(info["State"].get("Pid") or 0)
        if pid <= 0:
            raise SlotError(f"Slot holder {self.holder} is not running")
        return pid

    def _ensure_netns(self, pid: int) -> None:
        named = _NETNS_DIR / self.netns
        target = self._inode(f"/proc/{pid}/ns/net")
        if named.exists():
            if self._inode(str(named)) == target:
                return
            # The holder restarted: its namespace is new, so is everything in it.
            self._remove_link()
            self._run(["ip", "netns", "delete", self.netns])
        self._run(["ip", "netns", "attach", self.netns, str(pid)])

    @staticmethod
    def _inode(path: str) -> int:
        """A namespace's identity: the inode its ``/proc`` or bind-mounted file shows."""
        return os.stat(path).st_ino

    def _uplink(self) -> str:
        routes = json.loads(self._run(["ip", "-j", "route", "show", "default"]) or "[]")
        devices = sorted({route["dev"] for route in routes if route.get("dev")})
        if len(devices) != 1:
            raise SlotError(f"Slot NAT needs exactly one default-route interface, found {devices}")
        return devices[0]

    def _in_netns(self, *argv: str) -> list[str]:
        return ["ip", "netns", "exec", self.netns, *argv]

    def _ensure_link(self) -> None:
        cidr = f"{self.address}/{self.prefix_length}"
        if self._succeeds(["ip", "link", "show", "dev", self.link]):
            shown = self._run(
                self._in_netns("ip", "-o", "-4", "addr", "show", "dev", SLOT_INTERFACE)
            )
            if f" {cidr} " in shown:
                return
            self._remove_link()
        self._run(
            [
                "ip",
                "link",
                "add",
                self.link,
                "type",
                "veth",
                "peer",
                "name",
                SLOT_INTERFACE,
                "netns",
                self.netns,
            ]
        )
        self._run(["ip", "addr", "add", f"{self.gateway}/{self.prefix_length}", "dev", self.link])
        # The slot's callback DNATs to the VM's loopback, which a packet from
        # another interface may only reach when this interface allows it.
        self._run(["sysctl", "-qw", f"net.ipv4.conf.{self.link}.route_localnet=1"])
        self._run(["ip", "link", "set", self.link, "up"])
        self._run(self._in_netns("ip", "addr", "add", cidr, "dev", SLOT_INTERFACE))
        self._run(self._in_netns("ip", "link", "set", SLOT_INTERFACE, "up"))
        self._run(self._in_netns("ip", "link", "set", "lo", "up"))
        self._run(self._in_netns("ip", "route", "replace", "default", "via", self.gateway))
        self._run(["sysctl", "-qw", "net.ipv4.ip_forward=1"])

    def rules(self, uplink: str) -> list[tuple[str, str, list[str]]]:
        """Every netfilter rule of this slot, as ``(table, chain, match-and-target)``.

        Accept rules come first in each chain: ``_ensure_rules`` inserts them in
        reverse, so the drops stay below them.
        """
        tag = ["-m", "comment", "--comment", self.comment]
        address = f"{self.address}/32"
        return [
            ("nat", "POSTROUTING", ["-s", address, "-o", uplink, *tag, "-j", "MASQUERADE"]),
            (
                "nat",
                "PREROUTING",
                [
                    "-i",
                    self.link,
                    "-d",
                    f"{self.gateway}/32",
                    "-p",
                    "tcp",
                    "--dport",
                    str(self.platform_port),
                    *tag,
                    "-j",
                    "DNAT",
                    "--to-destination",
                    f"127.0.0.1:{self.platform_forward_port}",
                ],
            ),
            (
                "filter",
                "INPUT",
                [
                    "-i",
                    self.link,
                    "-d",
                    "127.0.0.1/32",
                    "-p",
                    "tcp",
                    "--dport",
                    str(self.platform_forward_port),
                    *tag,
                    "-j",
                    "ACCEPT",
                ],
            ),
            # Replies to the VM's own connections, such as Forge's tunnel to the broker.
            (
                "filter",
                "INPUT",
                [
                    "-i",
                    self.link,
                    "-m",
                    "conntrack",
                    "--ctstate",
                    "RELATED,ESTABLISHED",
                    *tag,
                    "-j",
                    "ACCEPT",
                ],
            ),
            ("filter", "INPUT", ["-i", self.link, *tag, "-j", "DROP"]),
            ("filter", "FORWARD", ["-i", self.link, "-o", uplink, *tag, "-j", "ACCEPT"]),
            (
                "filter",
                "FORWARD",
                [
                    "-i",
                    uplink,
                    "-o",
                    self.link,
                    "-m",
                    "conntrack",
                    "--ctstate",
                    "RELATED,ESTABLISHED",
                    *tag,
                    "-j",
                    "ACCEPT",
                ],
            ),
            # No other slot, no Docker network and nothing inbound.
            ("filter", "FORWARD", ["-i", self.link, *tag, "-j", "DROP"]),
            ("filter", "FORWARD", ["-o", self.link, *tag, "-j", "DROP"]),
        ]

    def _ensure_rules(self, uplink: str) -> None:
        for table, chain, rule in reversed(self.rules(uplink)):
            if self._succeeds(["iptables", "-w", "-t", table, "-C", chain, *rule]):
                continue
            self._run(["iptables", "-w", "-t", table, "-I", chain, "1", *rule])

    # -- down ---------------------------------------------------------------

    def containers(self) -> list[str]:
        listed = self._run(
            [
                "docker",
                "ps",
                "--all",
                "--quiet",
                *[arg for label in self.labels for arg in ("--filter", f"label={label}")],
            ]
        )
        return listed.split()

    def tagged_rules(self) -> list[tuple[str, list[str]]]:
        """This slot's rules as ``iptables-save`` prints them: ``(table, ["-A", chain, ...])``."""
        found = []
        for table in ("filter", "nat"):
            for line in self._run(["iptables-save", "-t", table]).splitlines():
                if not line.startswith("-A "):
                    continue
                tokens = shlex.split(line)
                if any(
                    token == "--comment"
                    and index + 1 < len(tokens)
                    and tokens[index + 1] == self.comment
                    for index, token in enumerate(tokens)
                ):
                    found.append((table, tokens))
        return found

    def _remove_link(self) -> None:
        if self._succeeds(["ip", "link", "show", "dev", self.link]):
            self._run(["ip", "link", "delete", self.link])

    def down(self) -> None:
        for container in self.containers():
            self._run(["docker", "rm", "--force", "--volumes", container])
        for table, tokens in self.tagged_rules():
            self._run(["iptables", "-w", "-t", table, "-D", *tokens[1:]])
        self._remove_link()
        if (_NETNS_DIR / self.netns).exists():
            self._run(["ip", "netns", "delete", self.netns])
        # Stopping the slice ends anything still running in the slot's cgroup.
        self._run(["systemctl", "stop", self.slice])
        if (_SYSTEMD_CONTROL / f"{self.slice}.d").exists():
            self._run(["systemctl", "revert", self.slice])
        for root in (self.data_root, self.run_root):
            if root.is_symlink():
                raise SlotError(f"Slot directory {root} is a symbolic link")
            if root.exists():
                shutil.rmtree(root)
        if self._account_exists(pwd.getpwnam):
            self._run(["userdel", self.name])
        if self._account_exists(grp.getgrnam):
            self._run(["groupdel", self.name])
        left = self.leftovers()
        if left:
            raise SlotError(f"Slot {self.slot} teardown left: {'; '.join(left)}")

    def _account_exists(self, lookup) -> bool:
        try:
            lookup(self.name)
        except KeyError:
            return False
        return True

    def leftovers(self) -> list[str]:
        """Everything of this slot that still exists on the VM."""
        left = []
        if self._account_exists(pwd.getpwnam):
            left.append(f"user {self.name}")
        if self._account_exists(grp.getgrnam):
            left.append(f"group {self.name}")
        for root in (self.data_root, self.run_root):
            if root.exists() or root.is_symlink():
                left.append(f"directory {root}")
        left += [f"container {container}" for container in self.containers()]
        if (_NETNS_DIR / self.netns).exists():
            left.append(f"network namespace {self.netns}")
        if self._succeeds(["ip", "link", "show", "dev", self.link]):
            left.append(f"interface {self.link}")
        left += [f"{table} rule {shlex.join(tokens)}" for table, tokens in self.tagged_rules()]
        if self._succeeds(["systemctl", "is-active", "--quiet", self.slice]):
            left.append(f"active slice {self.slice}")
        if (_SYSTEMD_CONTROL / f"{self.slice}.d").exists():
            left.append(f"slice settings {_SYSTEMD_CONTROL / (self.slice + '.d')}")
        if self.cgroup.exists():
            left.append(f"cgroup {self.cgroup}")
        return left

    # -- limits -------------------------------------------------------------

    def limit_events(self) -> dict[str, int]:
        """How often the slot hit its memory and PID limits (cgroup v2 event counters)."""
        directory = self.cgroup
        if not directory.exists():
            return {}
        counts: dict[str, int] = {}
        for name, keys in (("memory.events", ("oom_kill", "max")), ("pids.events", ("max",))):
            path = directory / name
            if not path.exists():
                continue
            for line in path.read_text().splitlines():
                key, _, value = line.partition(" ")
                if key in keys:
                    counts[f"{name.split('.')[0]}.{key}"] = int(value)
        return counts


def main(argv: list[str], stdin) -> int:
    if len(argv) != 2 or argv[1] not in {"up", "down", "verify", "limits"}:
        print("usage: vm_slot_supervisor up|down|verify|limits < spec.json", file=sys.stderr)
        return 2
    try:
        return _act(argv[1], SlotSupervisor(json.load(stdin)))
    except SlotError as exc:
        # The runtime shows the stage and this text; neither carries a credential.
        detail = base64.b64encode(diagnostic(str(exc)).encode()).decode()
        print(f"{STAGE_PREFIX}slot-{argv[1]}", file=sys.stderr)
        print(f"{DETAIL_PREFIX}{detail}", file=sys.stderr)
        return 1


def diagnostic(text: str) -> str:
    """One printable line the runtime accepts (it shows at most ``DIAGNOSTIC_LIMIT``)."""
    line = " ".join("".join(c if " " <= c <= "~" else " " for c in text).split())
    if len(line) <= DIAGNOSTIC_LIMIT:
        return line
    marker = " [truncated]"
    return line[: DIAGNOSTIC_LIMIT - len(marker)] + marker


def _act(action: str, supervisor: SlotSupervisor) -> int:
    if action == "up":
        print(json.dumps(supervisor.up()))
    elif action == "down":
        supervisor.down()
    elif action == "limits":
        print(json.dumps(supervisor.limit_events()))
    else:
        left = supervisor.leftovers()
        print(json.dumps({"leftovers": left}))
        return 1 if left else 0
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv, sys.stdin))
