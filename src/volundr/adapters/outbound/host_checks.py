"""Facts and the leftover audit of a registered host, run over its pinned SSH connection.

Both programs are POSIX ``sh`` run as root (``sudo -n sh -s``), so a host needs nothing but
what a stock systemd Linux has. Each prints lines ``fact <key> <value>`` or
``finding <text>``; anything else is ignored. A finding is something an operator must fix
before the host takes sessions again; the audit never fixes anything itself except the
allocation markers it is told to clear.

The slot layout matches ``volundr.adapters.outbound.vm_slot_supervisor``: user and group
``niuu-slot-<n>`` with uid ``uid_base + n``, directories ``/var/lib/niuu/slots/<n>`` and
``/run/niuu/slots/<n>``, netns ``niuu-slot-<n>``, link ``niuuslot<n>`` and slice
``niuu_slot_<n>.slice``.
"""

from __future__ import annotations

import shlex
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from uuid import UUID

from pydantic import JsonValue

from volundr.domain.compute import Machine, MachineBootstrap, MachineState
from volundr.domain.compute_hosts import HostConnector, HostInspector, RegisteredHost
from volundr.domain.guest_access import GuestAccess

#: Installed by join.sh; removes Forge's key, the sshd drop-in, the host key and the host's
#: Forge configuration.
LEAVE_PATH = "/usr/local/sbin/niuu-host-leave"

#: Reads what the capacity step and the host list show. Shared with join.sh, which sends the
#: same facts at join.
FACTS_SH = r"""
set -u
echo "fact cpus $(getconf _NPROCESSORS_ONLN 2>/dev/null || nproc)"
echo "fact memory_mib $(awk '/^MemTotal:/ {print int($2/1024)}' /proc/meminfo)"
echo "fact disk_free_mib $(df -Pm / | awk 'NR==2 {print $4}')"
echo "fact kernel $(uname -r)"
echo "fact arch $(uname -m)"
if [ "$(stat -fc %T /sys/fs/cgroup 2>/dev/null)" = cgroup2fs ]; then
  echo "fact cgroup_v2 true"
else
  echo "fact cgroup_v2 false"
fi
gpus=0
for device in /dev/nvidia[0-9]*; do
  [ -e "$device" ] && gpus=$((gpus + 1))
done
echo "fact gpus $gpus"
if [ -r /etc/os-release ]; then
  # shellcheck source=/dev/null
  . /etc/os-release
  echo "fact os ${PRETTY_NAME:-${NAME:-unknown}}"
fi
"""

#: Arguments: uid_base, slot count, live slot indexes (comma-separated, may be empty),
#: free-disk floor in MiB, and "clean" to clear the allocation's bootstrap markers first.
AUDIT_SH = r"""
set -u
uid_base=$1; slots=$2; live=",$3,"; floor_mib=$4; mode=$5
if [ "$mode" = clean ]; then
  rm -f /var/lib/niuu/bootstrap.sha256 /var/lib/niuu/bootstrap-state.json
fi
is_live() { case "$live" in *",$1,"*) return 0 ;; esac; return 1; }
# Every slot user, in or beyond the configured count: a leftover user is a finding.
for user in $(getent passwd | awk -F: '/^niuu-slot-[0-9]+:/ {print $1}'); do
  n=${user#niuu-slot-}
  is_live "$n" && continue
  echo "finding user $user still exists"
done
n=0
while [ "$n" -lt "$slots" ]; do
  if ! is_live "$n"; then
    uid=$((uid_base + n))
    for pid in $(pgrep -u "$uid" 2>/dev/null); do
      echo "finding process $(ps -o comm= -p "$pid" 2>/dev/null) (pid $pid) still runs as uid $uid"
    done
    for dir in /tmp /var/tmp /dev/shm /run/user /var/lib/niuu/slots /run/niuu/slots; do
      [ -d "$dir" ] || continue
      find "$dir" -xdev -uid "$uid" 2>/dev/null | while read -r path; do
        echo "finding $path is owned by uid $uid"
      done
    done
    [ -e "/var/lib/niuu/slots/$n" ] && echo "finding /var/lib/niuu/slots/$n still exists"
    [ -e "/run/niuu/slots/$n" ] && echo "finding /run/niuu/slots/$n still exists"
    ip netns list 2>/dev/null | grep -q "^niuu-slot-$n\b" && \
      echo "finding network namespace niuu-slot-$n still exists"
    ip link show "niuuslot$n" >/dev/null 2>&1 && echo "finding link niuuslot$n still exists"
    systemctl list-units --all --plain --no-legend "niuu_slot_$n.slice" 2>/dev/null | \
      grep -q . && echo "finding slice niuu_slot_$n.slice still exists"
    ipcs -c 2>/dev/null | awk -v u="$uid" '$3 == u || $4 == u {f=1} END {exit !f}' && \
      echo "finding IPC objects created by uid $uid still exist"
    [ -e "/var/spool/cron/crontabs/niuu-slot-$n" ] && \
      echo "finding a crontab for niuu-slot-$n still exists"
    [ -e "/var/lib/systemd/linger/niuu-slot-$n" ] && \
      echo "finding linger is enabled for niuu-slot-$n"
  fi
  n=$((n + 1))
done
for group in sudo wheel docker adm; do
  members=$(getent group "$group" | awk -F: '{print $4}')
  for member in $(echo "$members" | tr ',' ' '); do
    case "$member" in niuu-slot-*) echo "finding $member is in group $group" ;; esac
  done
done
if [ -r /etc/nix/nix.conf ]; then
  trusted=$(awk -F= '/^[[:space:]]*trusted-users[[:space:]]*=/ {print $2}' /etc/nix/nix.conf)
  for user in $trusted; do
    case "$user" in root) ;; *) echo "finding nix trusted-users includes $user" ;; esac
  done
fi
free_mib=$(df -Pm / | awk 'NR==2 {print $4}')
if [ "$free_mib" -lt "$floor_mib" ]; then
  echo "finding only ${free_mib} MiB free on /, below the floor of ${floor_mib} MiB"
fi
"""


@dataclass(frozen=True)
class CheckOutput:
    facts: dict[str, JsonValue]
    findings: tuple[str, ...]


def parse_check_output(output: str) -> CheckOutput:
    facts: dict[str, JsonValue] = {}
    findings: list[str] = []
    for line in output.splitlines():
        kind, _, rest = line.partition(" ")
        if kind == "finding" and rest.strip():
            findings.append(rest.strip())
        elif kind == "fact":
            key, _, value = rest.partition(" ")
            if key:
                facts[key] = _fact_value(value.strip())
    return CheckOutput(facts, tuple(findings))


#: ``Machine.status_detail`` is a 512-character column; the host row keeps every finding.
_STATUS_DETAIL_LIMIT = 512


def summarize_findings(findings: tuple[str, ...]) -> str | None:
    """Findings for the machine record: whole findings that fit, and how many did not."""
    if not findings:
        return None
    kept: list[str] = []
    for index, finding in enumerate(findings):
        more = len(findings) - index - 1
        suffix = f"; and {more} more on the host list" if more else ""
        candidate = "; ".join([*kept, finding]) + suffix
        if len(candidate) > _STATUS_DETAIL_LIMIT:
            break
        kept.append(finding)
    omitted = len(findings) - len(kept)
    text = "; ".join(kept)
    if omitted:
        tail = f"{omitted} finding(s) on the host list"
        text = f"{text}; and {tail}" if text else tail
    return text[:_STATUS_DETAIL_LIMIT]


def _fact_value(value: str) -> JsonValue:
    if value in {"true", "false"}:
        return value == "true"
    if value.isdigit():
        return int(value)
    return value


@dataclass(frozen=True)
class _HostRef:
    """The allocation-shaped view guest access needs, for a host outside any allocation."""

    id: UUID
    pool_id: str
    profile: str
    machine: Machine


class SshHostInspector(HostInspector):
    """Runs the facts read and the audit through the guest access of the host's execution."""

    def __init__(
        self,
        *,
        access_for: Callable[[RegisteredHost], GuestAccess],
        uid_base_for: Callable[[RegisteredHost], int | None],
        connectors: Mapping[str, HostConnector],
        min_free_disk_mib: int,
        command_timeout_seconds: float,
    ) -> None:
        if min_free_disk_mib < 0 or command_timeout_seconds <= 0:
            raise ValueError(
                "min_free_disk_mib must not be negative and command_timeout_seconds positive"
            )
        self._access_for = access_for
        self._uid_base_for = uid_base_for
        self._connectors = connectors
        self._floor = min_free_disk_mib
        self._timeout = command_timeout_seconds

    async def facts(self, host: RegisteredHost) -> dict[str, JsonValue]:
        return (await self._run(host, FACTS_SH, ())).facts

    async def audit(
        self, host: RegisteredHost, *, live_slots: Iterable[int], clean: bool
    ) -> tuple[str, ...]:
        live = sorted(set(live_slots))
        if clean and live:
            raise ValueError("A host with live slots is audited, never cleaned")
        # A runtime without separated slots has no slot uids; leftover slot users are still
        # found by name.
        uid_base = self._uid_base_for(host)
        args = (
            str(uid_base or 0),
            str(host.slots or 0) if uid_base is not None else "0",
            ",".join(str(index) for index in live),
            str(self._floor),
            "clean" if clean else "check",
        )
        return (await self._run(host, AUDIT_SH, args)).findings

    def machine(self, host: RegisteredHost, allocation_id: UUID | None = None) -> Machine:
        """The machine record guest access dials: the connector decides how to reach it."""
        dial = self._connectors[host.connector].dial(host)
        return Machine(
            allocation_id=allocation_id or host.id,
            resource_id=str(host.id),
            state=MachineState.RUNNING,
            addresses=(dial.address,),
            ssh_port=dial.port,
            ssh_proxy_command=dial.proxy_command,
            host_public_key=host.host_public_key,
            slots=host.slots,
            tags=host.tags,
            accepting=host.accepting,
            phase=host.name,
            status_detail=summarize_findings(host.findings),
        )

    async def leave(self, host: RegisteredHost) -> None:
        await self._access_for(host).execute(
            self._ref(host),
            MachineBootstrap(),
            shlex.join(["sudo", "-n", LEAVE_PATH]),
            timeout_seconds=self._timeout,
        )

    def _ref(self, host: RegisteredHost) -> _HostRef:
        return _HostRef(
            id=host.id, pool_id=host.pool_id, profile=host.profile, machine=self.machine(host)
        )

    async def _run(self, host: RegisteredHost, program: str, args: tuple[str, ...]) -> CheckOutput:
        ref = self._ref(host)
        command = shlex.join(["sudo", "-n", "sh", "-s", "--", *args])
        result = await self._access_for(host).execute(
            ref,
            MachineBootstrap(),
            command,
            data=program.encode(),
            capture_stdout=True,
            timeout_seconds=self._timeout,
        )
        return parse_check_output(result.stdout.decode(errors="replace"))
