"""Run a session directly on its machine ("host mode"), inside its slot's boundary.

The session has no image of its own: Skuld and the agent CLI run as the slot's user
against the machine's own filesystem and toolchain (and its Nix store), not in a
container. What keeps sessions apart is the slot the supervisor creates
(``vm_slot_supervisor``): its user, network namespace and cgroup. The runtime starts
the session over SSH, as every other guest step, and needs **no container daemon**, so
it runs on registered hosts (which are not disposable) as well as on VMs:

* the slot is a named network namespace and a systemd slice with the slot's limits;
* the pod's init containers and sidecars (the credential agents) run from their
  images' unpacked roots as systemd units in that slice and namespace
  (``linux_guest_programs``), never in a container engine;
* a small guest program, started in a delegated systemd scope of the slot's slice,
  joins the slot's namespace, opens its own mount and PID namespaces, mounts what the
  launch names (secrets, workspace, home, a private ``/tmp``) and runs Skuld as the
  slot's user. It is PID 1 of that namespace, so ending it ends every process of the
  session.

Every slot can run containers rootless with Podman: the slot's user gets its own
subordinate uid and gid range, a delegated cgroup inside the slot's (so a container's
limits sit under the slot's), its own runtime directory, and its storage in its home.
It never gets a root daemon or sudo.

Images are fetched from their registries by digest and unpacked once per digest on the
machine (``linux_guest_programs.IMAGE``), each claimed for the slot that runs from it,
and the ones nothing uses are removed when a session starts and when one stops
(``linux_guest_programs.PRUNE``). Skuld's own image is bind-mounted read-only
at its paths in the session's mount namespace; its Python must be relocatable (uv's
python-build-standalone, as in ``ghcr.io/niuulabs/openshell``): a distribution's
interpreter only runs inside its own root.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import PurePosixPath
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from volundr.adapters.outbound import linux_guest_programs as programs
from volundr.adapters.outbound.host_checks import AUDIT_SH
from volundr.adapters.outbound.host_session_programs import (
    AGENT_COMMANDS,
    FALLBACK_TOOLS,
    SESSION_BOOT,
    SLOT_STORAGE,
)
from volundr.adapters.outbound.ssh_vm_runtime import HostModeRuntime
from volundr.adapters.outbound.vm_hosted_unit import FORGE_SESSIONS_DIRECTORY, HostedUnitError
from volundr.adapters.outbound.vm_slot_layout import (
    FORGE_SESSION_DIRECTORY,
    GUEST_AGENT_DIRECTORY,
    SESSION_DATA,
    SESSION_LAUNCH,
    SESSION_RUNTIME,
    SESSION_SECRETS,
    SlotLayout,
)
from volundr.domain.compute import AllocationRef, MachineBootstrap, SlotLease
from volundr.domain.models import Session
from volundr.domain.vm_runtime import SessionImages

logger = logging.getLogger(__name__)

#: Where pod images are unpacked on the machine, one directory per digest.
HOST_IMAGE_CACHE = programs.IMAGE_CACHE
#: Where a session sees its unpacked image (wrappers under ``agents``/``tools``).
HOST_IMAGE_VIEW = "/opt/niuu-skuld"
#: Root-only state of the host sessions: their PID files and Skuld's output.
HOST_SESSION_STATE = "/run/niuu/host-sessions"
HOST_SESSION_LOGS = "/var/log/niuu/host-sessions"
#: Root-only: the pod containers' writable layers (tmpfs while they run).
HOST_POD_WORK = "/run/niuu/host-pods"
#: What a container's /etc/resolv.conf is: the machine's upstream servers, since the
#: slot's namespace cannot reach a stub resolver on the machine's loopback.
_RESOLV_CONF = "/run/systemd/resolve/resolv.conf"
#: What the pod program needs of an unpacked image to run a container from it.
_POD_IMAGE_KEYS = ("rootfs", "env", "user", "workdir", "entrypoint", "cmd")
_SYSTEM_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
_HOME = "/home/skuld"
_WORKSPACE = "/workspace"
_DIGEST = re.compile(r"@sha256:([a-f0-9]{64})$")
#: What exists while a slot does (``vm_slot_supervisor``): its named network namespace and
#: its slice's cgroup, which outlives the namespace until nothing of the slot runs.
_NETNS = "/run/netns"
_CGROUP = "/sys/fs/cgroup"

#: What the agent is told about the machine it runs on.
HOST_HINT = (
    "You are running directly on a Linux VM as the unprivileged user {user}, not in a "
    "container; apt and sudo are not available."
)
NIX_HINT = (
    "Install software with Nix, which every session on this VM shares: "
    "`nix profile install nixpkgs#<package>` keeps it on your PATH, "
    "`nix shell nixpkgs#<package> -c <command>` uses it once, and "
    "`nix search nixpkgs <name>` finds packages; one another session installed is ready at "
    "once. Language tools install into your home as usual (npm prefixes, `uv tool`, "
    "`go install`, `pip --user`)."
)
CONTAINERS_HINT = (
    "Containers run rootless with Podman: `podman build`, `podman run` (`docker` runs "
    "podman). Published ports are yours alone and open as previews."
)
#: Rootless container state in a slot's home: the machine's, never archived.
_CONTAINER_STATE = ("home/.local/share/containers", "home/.cache/containers")
#: The VM's own credentials, hidden from every host session (and its containers): the
#: guest identity token, Forge's SSH host key and the whole-VM session files.
MASKED_GUEST_PATHS = ("/etc/niuu",)
#: Commands every host session may use, from the VM: ``docker`` runs rootless Podman.
HOST_BIN = "/usr/local/lib/niuu/host-bin"
_MAX_ID = 2**32 - 2

# PID 1 of a host session's PID namespace. Started by _HOST_START as root, in a
# delegated systemd scope of the slot's slice, with the session spec on stdin: makes
# the scope a tree (itself in a leaf, the session's subtree delegated to the slot's
# user, where its containers get cgroups of their own under the slot's limits), joins
# the slot's network namespace, mounts the session's view, then runs Skuld as the
# slot's user, forwards stop signals to it and reaps whatever it leaves. When Skuld
# ends, so does this process, and with it every process left in the namespace.
_HOST_INIT = r"""
import json, os, pathlib, signal, subprocess, sys
spec = json.load(sys.stdin)
def mount(*args):
    subprocess.run(['mount', *args], check=True)
scope = pathlib.Path('/sys/fs/cgroup' + pathlib.Path('/proc/self/cgroup').read_text()
    .strip().rsplit('::', 1)[1])
session = scope / 'session'
(session / 'init').mkdir(parents=True, exist_ok=True)
(session / 'init' / 'cgroup.procs').write_text(str(os.getpid()))
available = (scope / 'cgroup.controllers').read_text().split()
wanted = ' '.join('+' + name for name in ('cpu', 'memory', 'pids') if name in available)
if wanted:
    (scope / 'cgroup.subtree_control').write_text(wanted)
for path in (session, session / 'cgroup.procs', session / 'cgroup.subtree_control',
        session / 'cgroup.threads', session / 'init', session / 'init' / 'cgroup.procs'):
    os.chown(str(path), spec['uid'], spec['uid'])
if wanted:
    (session / 'cgroup.subtree_control').write_text(wanted)
fd = os.open('/run/netns/' + spec['netns'], os.O_RDONLY)
os.setns(fd, os.CLONE_NEWNET)
os.close(fd)
os.unshare(os.CLONE_NEWNS)
# Slave: nothing mounted here reaches the VM. Shared again within: rootless
# Podman propagates its containers' mounts.
mount('--make-rslave', '/')
# Last: the next process this one starts is PID 1 of the new namespace.
os.unshare(os.CLONE_NEWPID)
child = os.fork()
if child:
    pidfile = pathlib.Path(spec['pidfile'])
    temporary = pidfile.with_suffix('.tmp')
    temporary.write_text(str(child))
    os.replace(temporary, pidfile)
    os._exit(0)
mount('-t', 'proc', 'proc', '/proc')
for item in spec['mounts']:
    mount('--bind', *(['-o', 'ro'] if item['readonly'] else []), item['source'], item['target'])
for path in spec['masked']:
    # The VM's own credentials (its identity token, Forge's host key) and the slots'
    # root-owned state: an empty, read-only tree in their place, whatever their modes.
    # After the binds, whose sources (the slot's secrets) live there.
    if os.path.isdir(path):
        mount('-t', 'tmpfs', '-o', 'ro,mode=0755', 'tmpfs', path)
mount('--make-rshared', '/')
session = os.fork()
if not session:
    os.setgroups([spec['uid']])
    os.setgid(spec['uid'])
    os.setuid(spec['uid'])
    os.umask(0o022)
    os.chdir(spec['workdir'])
    os.execve('/bin/sh', ['/bin/sh', '-c', spec['boot']], spec['environment'])
def forward(signum, frame):
    os.kill(session, signum)
for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
    signal.signal(signum, forward)
while True:
    pid, status = os.waitpid(-1, 0)
    if pid == session:
        print('Skuld exited with status', os.waitstatus_to_exitcode(status), flush=True)
        os._exit(0)
"""

# Start (or keep) the slot's host session. Stdin: the placed launch payload with
# its ``host`` section. Exit 0 once the session's PID 1 runs.
_HOST_START = (
    SLOT_STORAGE
    + r"""
import json, os, pathlib, signal, subprocess, sys, time
p = json.load(sys.stdin)
s, h = p['slot'], p['host']
pidfile = pathlib.Path(h['pidfile'])
def running():
    try:
        pid = int(pidfile.read_text())
        ours = os.stat('/proc/%d/ns/net' % pid).st_ino == os.stat('/run/netns/' + s['netns']).st_ino
    except (FileNotFoundError, ProcessLookupError, ValueError):
        return None
    return pid if ours else None
def end(pid):
    # Root touches the slot's storage next: nothing of the old session may still run.
    for signum in (signal.SIGTERM, signal.SIGKILL):
        os.kill(pid, signum)
        deadline = time.monotonic() + h['stop_timeout']
        while os.path.exists('/proc/%d' % pid) and time.monotonic() < deadline:
            time.sleep(h['poll'])
        if not os.path.exists('/proc/%d' % pid):
            return
    raise RuntimeError('The previous host session survived SIGKILL')
current = running()
if current and not p.get('relaunch'):
    sys.exit(0)
if current:
    # A new launch replaced the Forge session credential the running broker read.
    end(current)
root = pathlib.Path(s['data_root'])
prepare_storage(root, s['marker'], s['uid'], p['repo'], p['branch'], h['keep_owners'])
for directory in h['private']:
    owned(directory, 0o700, s['uid'])
c = h['containers']
if c:
    if not os.path.exists('/usr/bin/podman') or not os.path.exists('/usr/bin/newuidmap'):
        raise RuntimeError('Rootless containers need podman and uidmap on the VM; install '
            'them through the machine profile or set compute.runtime.kwargs.containers: null')
    first, last = c['first'], c['first'] + c['count'] - 1
    present = []
    for name in ('/etc/subuid', '/etc/subgid'):
        entries = [line.split(':') for line in pathlib.Path(name).read_text().split()]
        ours = [e for e in entries if e[0] == h['user']]
        for e in entries:
            start, end = int(e[1]), int(e[1]) + int(e[2]) - 1
            if e[0] != h['user'] and start <= last and first <= end:
                raise RuntimeError(name + ' gives ' + e[0] + ' ids overlapping slot ' + h['user']
                    + '; move compute.runtime.kwargs.containers.subordinate_id_base')
        if ours and ours != [[h['user'], str(first), str(c['count'])]]:
            raise RuntimeError(name + ' holds other ids for ' + h['user'])
        present.append(bool(ours))
    if present == [True, False] or present == [False, True]:
        raise RuntimeError('/etc/subuid and /etc/subgid disagree for ' + h['user'])
    if not any(present):
        span = '%d-%d' % (first, last)
        subprocess.run(['usermod', '--add-subuids', span, '--add-subgids', span, h['user']],
            check=True)
    shim = pathlib.Path(h['bin']) / 'docker'
    if not shim.exists():
        shim.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        temporary = shim.with_name('.docker')
        temporary.write_text('#!/bin/sh\nexec podman "$@"\n')
        temporary.chmod(0o755)
        os.replace(temporary, shim)
if h['nix']:
    require_nix('machine')
for item in h['mounts']:
    target = pathlib.Path(item['target'])
    # Mountpoints only: in the VM's own tree they stay empty and root-owned.
    if os.path.isdir(item['source']):
        target.mkdir(parents=True, exist_ok=True)
    elif not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.touch()
for directory in (pidfile.parent, pathlib.Path(h['log']).parent):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
pidfile.unlink(missing_ok=True)
spec = {
    'netns': s['netns'],
    'pidfile': str(pidfile),
    'mounts': h['mounts'],
    'uid': s['uid'],
    'masked': h['masked'],
    'workdir': h['workdir'],
    'boot': h['boot'],
    'environment': p['environment'],
}
log = os.open(h['log'], os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
program = pidfile.parent / 'init.py'
program.write_text(h['init'])
# A scope of its own in the slot's slice: the slot's limits hold the session, and the
# delegation lets it give its containers cgroups. Named per start: a scope whose last
# process is still ending keeps its name for a moment.
scope = h['scope_prefix'] + '%d.scope' % time.time_ns()
init = subprocess.Popen(['systemd-run', '--scope', '--quiet', '--collect', '--unit', scope,
    '--slice', s['slice'], '--property', 'Delegate=yes', sys.executable, '-I', str(program)],
    stdin=subprocess.PIPE, stdout=log, stderr=log, start_new_session=True, close_fds=True)
init.communicate(json.dumps(spec).encode(), timeout=h['start_timeout'])
if init.returncode or not running():
    raise RuntimeError('The host session did not start; see ' + h['log'] + ' on the VM')
"""
)

# Stop the slot's host session and withdraw its subordinate ids. argv: the stop spec
# (JSON). Skuld gets SIGTERM and ``stop_timeout`` to flush, then everything in
# its namespace is killed.
_HOST_STOP = r"""
import json, os, pathlib, signal, subprocess, sys, time
h = json.loads(sys.argv[1])
pidfile = pathlib.Path(h['pidfile'])
try:
    pid = int(pidfile.read_text())
    namespace = os.stat('/run/netns/' + h['netns']).st_ino
    ours = os.stat('/proc/%d/ns/net' % pid).st_ino == namespace
except (FileNotFoundError, ValueError):
    ours = False
if ours:
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + h['stop_timeout']
    while os.path.exists('/proc/%d' % pid) and time.monotonic() < deadline:
        time.sleep(h['poll'])
    if os.path.exists('/proc/%d' % pid):
        os.kill(pid, signal.SIGKILL)
        deadline = time.monotonic() + h['stop_timeout']
        while os.path.exists('/proc/%d' % pid) and time.monotonic() < deadline:
            time.sleep(h['poll'])
    if os.path.exists('/proc/%d' % pid):
        raise RuntimeError('The host session survived SIGKILL')
pidfile.unlink(missing_ok=True)
c = h['containers']
# A member of a run's unit leaves its slot's ids to the others; the root's stop withdraws them.
if c and not h.get('member'):
    span = '%d-%d' % (c['first'], c['first'] + c['count'] - 1)
    ours = h['user'] + ':' + str(c['first']) + ':' + str(c['count'])
    if ours in pathlib.Path('/etc/subuid').read_text().split():
        subprocess.run(['usermod', '--del-subuids', span, '--del-subgids', span, h['user']],
            check=True)
"""

# Whether a host session's PID 1 runs in the slot's network namespace. argv: the stop spec
# (JSON, as _HOST_STOP takes it). Exit 0 running, 44 not.
_HOST_RUNNING = r"""
import json, os, pathlib, sys
h = json.loads(sys.argv[1])
try:
    pid = int(pathlib.Path(h['pidfile']).read_text())
    ours = os.stat('/proc/%d/ns/net' % pid).st_ino == os.stat('/run/netns/' + h['netns']).st_ino
except (FileNotFoundError, ProcessLookupError, ValueError):
    ours = False
sys.exit(0 if ours else 44)
"""


class HostSlotLayout(SlotLayout):
    """A host-mode slot: whatever root writes for it lives in root's directory.

    A host session runs on the VM as the slot's user, who owns the slot's data and
    runtime directories and could swap anything in them for a link. So the launch
    file, the secrets, the Forge session credential, the guest agent's configuration
    and the token and volume directories, which root and the credential agents write
    while the session runs, live under the slot's root-owned ``agent_root``
    (``root:<slot group> 0750``: closed to other slots too). Only the session's own
    storage, its home and workspace, stays in the slot's data directory.
    """

    def supervisor_spec(self, *, image: str, platform_port: int) -> dict:
        # No holder container: the slot runs no container daemon.
        return {**super().supervisor_spec(image=image, platform_port=platform_port), "holder": None}

    @property
    def unit_prefix(self) -> str:
        """The prefix of every systemd unit of this slot (``vm_slot_supervisor.units``)."""
        return f"{self.name}-"

    def _roots(self) -> tuple[tuple[str, str], ...]:
        return (
            (SESSION_DATA, self.session_data),
            (SESSION_LAUNCH, f"{self.agent_root}/session-launch.json"),
            (SESSION_SECRETS, f"{self.agent_root}/session-secrets"),
            (FORGE_SESSION_DIRECTORY, f"{self.agent_root}/forge-session"),
            (FORGE_SESSIONS_DIRECTORY, f"{self.agent_root}/forge-sessions"),
            (GUEST_AGENT_DIRECTORY, f"{self.agent_root}/guest-agent"),
            (SESSION_RUNTIME, f"{self.agent_root}/run"),
        )


class RootlessContainers(BaseModel):
    """``compute.runtime.kwargs.containers``: rootless Podman in every slot.

    Slot ``n`` maps its containers' ids to ``subordinate_id_base + n *
    subordinate_ids_per_slot`` onwards, so no two slots, and no slot and the VM,
    share an id.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    subordinate_id_base: int = Field(
        default=1_000_000,
        ge=100_000,
        description="First subordinate uid and gid of slot 0; the ranges of all slots must "
        "be free on the VM image (Ubuntu gives its login user 100000-165535)",
    )
    subordinate_ids_per_slot: int = Field(
        default=65536,
        gt=0,
        description="Subordinate uids and gids per slot; images that use ids up to 65534 "
        "(nobody) need 65536",
    )


class SshHostVmRuntime(HostModeRuntime):
    """Run each session directly on its machine, in its slot's user, namespaces and cgroup.

    ``skuld_image`` is the digest-pinned session image Skuld, the agent CLIs and
    the guest credential agents come from; its Python must be relocatable (see
    the module docstring). ``slot_isolation`` is required: without a slot there
    is no boundary between the session and the machine. The machine needs systemd,
    cgroup v2, iproute2, iptables, curl and a ``python3`` with tarfile extraction
    filters, and no container daemon.

    ``nix`` says the machine profile installs a multi-user Nix: the session gets
    Nix's profiles on its PATH and the agent is told how to install with it.
    The runtime refuses to start a session when the daemon is missing.

    ``containers`` (``RootlessContainers``) gives every slot rootless Podman; set it
    to ``None`` for slots without containers. The machine profile installs podman,
    uidmap and passt; the runtime refuses to start a session without them.
    """

    _audit_program = AUDIT_SH

    def __init__(
        self,
        *,
        containers: dict | None = {},  # noqa: B006 - read-only; the defaults of RootlessContainers
        session_images: SessionImages | None = None,
        skuld_image: str | None = None,
        **kwargs,
    ):
        image = self._release_image(
            skuld_image,
            session_images.linux if session_images is not None else None,
            kwarg="skuld_image",
            setting="compute.session_images.linux",
        )
        super().__init__(skuld_image=image, **kwargs)
        if self._slot_isolation is None:
            raise ValueError(
                "Host-mode sessions run in a slot of their VM; configure "
                "compute.runtime.kwargs.slot_isolation"
            )
        if not _DIGEST.search(self._image):
            raise ValueError("Host mode unpacks the session image by digest; pin skuld_image")
        self._containers = RootlessContainers(**containers) if containers is not None else None
        if self._containers is not None:
            slots = self._slot_isolation.slot_capacity
            ids = self._containers
            end = ids.subordinate_id_base + slots * ids.subordinate_ids_per_slot
            uids = range(self._slot_isolation.uid_base, self._slot_isolation.uid_base + slots)
            if end > _MAX_ID:
                raise ValueError(
                    f"{slots} slots of {ids.subordinate_ids_per_slot} subordinate ids from "
                    f"{ids.subordinate_id_base} end past {_MAX_ID}; lower "
                    "containers.subordinate_id_base or slot_isolation.subnet's size"
                )
            if ids.subordinate_id_base < uids.stop and uids.start < end:
                raise ValueError(
                    "containers subordinate ids overlap the slots' own uids; move "
                    "containers.subordinate_id_base or slot_isolation.uid_base"
                )
            # Container storage is the machine's: its files carry subordinate ids, and
            # a session's images are pulled again on its next machine.
            self._archive_excludes = (*self._archive_excludes, *_CONTAINER_STATE)

    _layout_class = HostSlotLayout

    async def recycle(self, lease: SlotLease, bootstrap: MachineBootstrap) -> None:
        """The supervisor checks the slot is gone, then the audit checks the slot's user.

        The audit covers the user's processes and files anywhere it could write, IPC
        objects, crontab and linger; any finding raises, naming it.
        """
        await self._supervise(lease, bootstrap, self._slot(lease), "verify")
        await super().recycle(lease, bootstrap)

    def _slot(self, lease) -> SlotLayout:
        layout = self._layout(lease)
        if layout is None:  # pragma: no cover - slot_isolation is required at construction
            raise ValueError("Host-mode sessions need slot_isolation")
        return layout

    def _shape_launch(self, session: Session, launch: dict) -> dict:
        return {**launch, "environment": self._host_environment(launch["environment"])}

    def _host_environment(self, environment: dict[str, str]) -> dict[str, str]:
        """The container session's environment, for a session on the VM itself."""
        nix_paths = [f"{_HOME}/.nix-profile/bin", "/nix/var/nix/profiles/default/bin"]
        path = [
            f"{HOST_IMAGE_VIEW}/agents",
            *([HOST_BIN] if self._containers is not None else []),
            *(nix_paths if self._nix else []),
            _SYSTEM_PATH,
            f"{HOST_IMAGE_VIEW}/tools",
        ]
        updated = {
            **environment,
            "PATH": ":".join(path),
            "SHELL": "/bin/bash",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
            "TMPDIR": "/tmp",
        }
        if self._nix:
            updated.update(
                {
                    "NIX_REMOTE": "daemon",
                    "NIX_PROFILES": f"/nix/var/nix/profiles/default {_HOME}/.nix-profile",
                    "NIX_SSL_CERT_FILE": "/etc/ssl/certs/ca-certificates.crt",
                }
            )
        hints = [HOST_HINT, *([NIX_HINT] if self._nix else [])]
        hints += [CONTAINERS_HINT] if self._containers is not None else []
        hint = " ".join(hints).format(user="{slot_user}")
        prompt = updated.get("SKULD__SESSION__SYSTEM_PROMPT", "")
        updated["SKULD__SESSION__SYSTEM_PROMPT"] = f"{prompt}\n\n{hint}" if prompt else hint
        return updated

    async def warm(self, lease, bootstrap: MachineBootstrap) -> None:
        # No container daemon to pull into: the session image is unpacked from its registry.
        # No slot runs from it yet, so nothing claims it; every prune keeps the release's.
        await self.prepare(lease, bootstrap)
        await self._unpack(lease, bootstrap, None)

    async def _unpack(self, lease, bootstrap: MachineBootstrap, claim: dict | None) -> dict:
        """The session image, unpacked on the machine (once per digest), described."""
        session = {"agents": AGENT_COMMANDS, "tools": FALLBACK_TOOLS, "view": HOST_IMAGE_VIEW}
        return await self._fetch_image(lease, bootstrap, self._image, claim, session)

    async def _fetch_image(
        self,
        lease: AllocationRef,
        bootstrap: MachineBootstrap,
        image: str,
        claim: dict | None,
        session: dict | None = None,
    ) -> dict:
        result = await self._guest_access.execute(
            lease,
            bootstrap,
            self._python(
                programs.IMAGE, image, HOST_IMAGE_CACHE, json.dumps(session), json.dumps(claim)
            ),
            capture_stdout=True,
        )
        description = json.loads(result.stdout)
        root = f"{HOST_IMAGE_CACHE}/{description['digest'].split(':', 1)[1]}"
        return {**description, "root": root, "rootfs": f"{root}/rootfs"}

    @staticmethod
    def _claim(layout: SlotLayout) -> dict:
        """The slot's claim on the images it runs from: alive while the slot is there."""
        return {
            "slot": layout.name,
            "alive": [f"{_NETNS}/{layout.name}", f"{_CGROUP}/{layout.slice}"],
        }

    async def _prune_images(
        self, lease: AllocationRef, bootstrap: MachineBootstrap, keep: list[str]
    ) -> None:
        """Remove the unpacked images no slot claims, no mount names and ``keep`` does not.

        A prune that cannot run raises: an image cache nobody prunes fills the machine
        until it is quarantined below its free-disk floor.
        """
        result = await self._guest_access.execute(
            lease,
            bootstrap,
            self._python(programs.PRUNE, HOST_IMAGE_CACHE, json.dumps(keep), "/proc"),
            capture_stdout=True,
        )
        pruned = json.loads(result.stdout)
        if pruned["removed"]:
            logger.info(
                "Removed %d unused image(s) from %s on allocation %s: %s",
                len(pruned["removed"]),
                HOST_IMAGE_CACHE,
                lease.id,
                ", ".join(pruned["removed"]),
            )
        if pruned["unknown"]:
            logger.error(
                "%s on allocation %s holds entries Forge did not make and never removes: %s; "
                "remove them on the machine",
                HOST_IMAGE_CACHE,
                lease.id,
                ", ".join(pruned["unknown"]),
            )

    def _subordinate_ids(self, layout: SlotLayout) -> dict | None:
        if self._containers is None:
            return None
        count = self._containers.subordinate_ids_per_slot
        return {"first": self._containers.subordinate_id_base + layout.slot * count, "count": count}

    @staticmethod
    def _member_home(layout: SlotLayout, member: dict) -> str:
        return f"{layout.session_data}/members/{member['port']}/home"

    @staticmethod
    def _session_state(layout: SlotLayout, member: dict | None) -> tuple[str, str]:
        """The PID file and log of the unit's root Skuld, or of one member's."""
        key = layout.key if member is None else f"{layout.key}-m{member['port']}"
        return f"{HOST_SESSION_STATE}/{key}.pid", f"{HOST_SESSION_LOGS}/{key}.log"

    def _host_spec(
        self, layout: SlotLayout, payload: dict, image: dict, member: dict | None = None
    ) -> dict:
        """Where one Skuld of the slot runs and what it sees, for _HOST_START.

        ``member`` is a member of a run's unit: it shares the root's workspace, user,
        namespaces and cgroup, and keeps its own home, credential directory, PID file and
        log (``payload`` is then the member's own, from ``_unit_payloads``).
        """
        session_data = PurePosixPath(layout.session_data)
        rootfs = image["rootfs"]
        mounts = [
            {"source": image["root"], "target": HOST_IMAGE_VIEW},
            {"source": f"{rootfs}{image['venv']}", "target": image["venv"]},
            {"source": f"{rootfs}{image['python_root']}", "target": image["python_root"]},
        ]
        mounts = [{**item, "readonly": True} for item in mounts]
        runtime_directory = f"{layout.run_root}/xdg"
        private = [f"{layout.data_root}/tmp", f"{layout.run_root}/shm", runtime_directory]
        home = self._member_home(layout, member) if member else str(session_data / "home")
        if member:
            # Parents first: each is created inside the one before it.
            private += [
                f"{layout.session_data}/members",
                f"{layout.session_data}/members/{member['port']}",
                home,
            ]
        mounts += [
            {"source": _RESOLV_CONF, "target": "/etc/resolv.conf"},
            {"source": private[0], "target": "/tmp"},
            {"source": private[0], "target": "/var/tmp"},
            {"source": private[1], "target": "/dev/shm"},
        ]
        mounts = [{"readonly": item.get("readonly", False), **item} for item in mounts]
        mounts += [
            {"source": str(session_data / "workspace"), "target": _WORKSPACE, "readonly": False},
            {"source": home, "target": _HOME, "readonly": False},
        ]
        # The pod's mounts at the targets the session expects, parents before children.
        declared = [*payload.get("secret_mounts", []), *payload.get("volume_mounts", [])]
        mounts += sorted(
            (
                {
                    "source": item["source"],
                    "target": str(self._in_image(item["target"])),
                    "readonly": item.get("readonly", True),
                }
                for item in declared
            ),
            key=lambda item: len(PurePosixPath(item["target"]).parts),
        )
        environment = dict(payload["environment"])
        environment["NIUU_SKULD_PYTHON"] = f"{image['venv']}/bin/python"
        environment["SKULD__SESSION__SYSTEM_PROMPT"] = environment.get(
            "SKULD__SESSION__SYSTEM_PROMPT", ""
        ).replace("{slot_user}", layout.name)
        if self._containers is not None:
            # Podman's per-user runtime directory; there is no login session to make one.
            environment["XDG_RUNTIME_DIR"] = runtime_directory
        pidfile, log = self._session_state(layout, member)
        scope = f"m{member['port']}-session-" if member else "session-"
        return {
            "user": layout.name,
            "scope_prefix": f"{layout.unit_prefix}{scope}",
            "pidfile": pidfile,
            "log": log,
            "nix": self._nix,
            "containers": self._subordinate_ids(layout),
            "bin": HOST_BIN,
            "keep_owners": list(_CONTAINER_STATE) if self._containers is not None else [],
            "mounts": mounts,
            "masked": list(MASKED_GUEST_PATHS),
            "private": private,
            "workdir": _WORKSPACE,
            "boot": SESSION_BOOT,
            "init": _HOST_INIT,
            "environment": environment,
            "stop_timeout": self._stop_timeout,
            "poll": self._poll,
            "start_timeout": self._session_start_timeout,
        }

    def _pods_spec(self, layout: SlotLayout, images: dict[str, dict]) -> dict:
        """How ``linux_guest_programs.PODS_START`` runs the slot's pod containers."""
        return {
            "prefix": layout.unit_prefix,
            "slice": layout.slice,
            "state": f"{HOST_SESSION_STATE}/{layout.key}-pods",
            "logs": f"{HOST_SESSION_LOGS}/{layout.key}",
            "work": f"{HOST_POD_WORK}/{layout.key}",
            "resolv_conf": _RESOLV_CONF,
            "program": programs.CONTAINER,
            "images": {
                reference: {key: image[key] for key in _POD_IMAGE_KEYS}
                for reference, image in images.items()
            },
            "stop_timeout": self._stop_timeout,
            "poll": self._poll,
            "start_timeout": self._session_start_timeout,
        }

    def _unit_payloads(self, payload: dict) -> list[tuple[dict | None, dict]]:
        """The Skuld processes this launch starts: the root's, then each member's.

        Each is its own launch payload: the unit's pod layout with that Skuld's environment
        and mounts (its own Forge session credential directory). ``only`` names the one
        member being started again in place.
        """
        root_id = payload["environment"].get("SESSION_ID")
        units: list[tuple[dict | None, dict]] = [(None, payload)]
        for member in payload.get("members") or []:
            units.append(
                (
                    member,
                    {
                        **payload,
                        "environment": member["environment"],
                        "volume_mounts": member["volume_mounts"],
                    },
                )
            )
        only = payload.get("only")
        if not only:
            return units
        chosen = [(m, p) for m, p in units if (m["id"] if m else root_id) in only]
        if not chosen:
            raise HostedUnitError(
                f"The launch names {only} to start again, which are not Skuld processes of "
                "this unit"
            )
        return chosen

    async def _launch_skuld(
        self, lease: SlotLease, bootstrap: MachineBootstrap, payload: dict
    ) -> None:
        layout = self._slot(lease)
        claim = self._claim(layout)
        units = self._unit_payloads(payload)
        if not payload.get("only"):
            containers = [*payload.get("init_containers", []), *payload.get("sidecars", [])]
            references = list(dict.fromkeys(container["image"] for container in containers))
            # Before fetching: room for what this launch unpacks, and what it runs from stays.
            self._step(lease, "guest_prune", "Removing images no session uses")
            await self._prune_images(lease, bootstrap, [self._image, *references])
            images: dict[str, dict] = {}
            if references:
                self._step(lease, "guest_images", "Fetching the session's agent images")
                for reference in references:
                    images[reference] = await self._fetch_image(lease, bootstrap, reference, claim)
            self._step(lease, "guest_skuld", "Starting the session's agents")
            await self._guest_access.execute(
                lease,
                bootstrap,
                self._python(programs.PODS_START),
                data=json.dumps({**payload, "pods": self._pods_spec(layout, images)}).encode(),
            )
        self._step(lease, "guest_image", "Unpacking Skuld onto the machine")
        image = await self._unpack(lease, bootstrap, claim)
        for member, unit in units:
            host = self._host_spec(layout, unit, image, member)
            environment = host.pop("environment")
            self._step(lease, "guest_host", f"Starting Skuld as {layout.name}")
            await self._guest_access.execute(
                lease,
                bootstrap,
                self._python(_HOST_START),
                data=json.dumps({**unit, "environment": environment, "host": host}).encode(),
            )

    async def prune_host(self, lease: AllocationRef, bootstrap: MachineBootstrap) -> None:
        """The host's check: what no slot claims or mounts goes, the release's image stays."""
        await self._prune_images(lease, bootstrap, [self._image])

    async def stop(self, lease: SlotLease, bootstrap: MachineBootstrap) -> None:
        await super().stop(lease, bootstrap)
        # The slot is gone, and its claims with it: what only it ran from goes too. The
        # release's session image stays for the next session.
        await self._prune_images(lease, bootstrap, [self._image])

    def _stop_spec(self, layout: SlotLayout, member: dict | None) -> dict:
        pidfile, _ = self._session_state(layout, member)
        spec = {
            "pidfile": pidfile,
            "netns": layout.name,
            "user": layout.name,
            "containers": self._subordinate_ids(layout),
            "stop_timeout": self._stop_timeout,
            "poll": self._poll,
        }
        return {**spec, "member": True} if member is not None else spec

    async def _stop_host_session(
        self, lease: SlotLease, bootstrap: MachineBootstrap, layout: SlotLayout, member: dict | None
    ) -> None:
        await self._guest_access.execute(
            lease,
            bootstrap,
            self._python(_HOST_STOP, json.dumps(self._stop_spec(layout, member))),
        )

    async def _stop_session(
        self, lease: SlotLease, bootstrap: MachineBootstrap, layout: SlotLayout
    ) -> int:
        """The members, then Skuld (SIGTERM, its flush time, then SIGKILL), then the agents."""
        for member in reversed((self._launch(bootstrap) or {}).get("members") or []):
            await self._stop_host_session(lease, bootstrap, layout, member)
        await self._stop_host_session(lease, bootstrap, layout, None)
        # Then the agents, after Skuld has flushed through them.
        result = await self._guest_access.execute(
            lease,
            bootstrap,
            self._python(
                programs.PODS_STOP,
                json.dumps(
                    {
                        "prefix": layout.unit_prefix,
                        "data_root": layout.session_data,
                        "marker": layout.key,
                    }
                ),
            ),
            expected_exit_codes=(0, 42),
        )
        return result.exit_code

    async def member_running(
        self, lease: SlotLease, bootstrap: MachineBootstrap, member_id: UUID
    ) -> bool:
        """Whether the member's Skuld process runs in the slot."""
        layout = self._slot(lease)
        member = self._member(bootstrap, member_id)
        result = await self._guest_access.execute(
            lease,
            bootstrap,
            self._python(_HOST_RUNNING, json.dumps(self._stop_spec(layout, member))),
            expected_exit_codes=(0, 44),
        )
        return result.exit_code == 0

    async def stop_member(
        self, lease: SlotLease, bootstrap: MachineBootstrap, member_id: UUID
    ) -> None:
        layout = self._slot(lease)
        member = self._member(bootstrap, member_id)
        await self._stop_host_session(lease, bootstrap, layout, member)
        await self._close_tunnel(f"{layout.key}-m{member['port']}")
