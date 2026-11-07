"""Run a VM session directly on the VM ("host mode"), inside its slot's boundary.

The session has no image of its own: Skuld and the agent CLI run as the slot's
user against the VM's own filesystem and toolchain (and its Nix store), not in a
container. What keeps sessions apart is the slot the supervisor creates
(``vm_slot_supervisor``): its user, network namespace and cgroup. The runtime
starts the session over SSH, as every other guest step, never as a service:

* the slot's holder container keeps the slot's network namespace and cgroup, and
  its credential agents still run beside it in the guest's Docker, as for
  ``SshContainerVmRuntime``;
* a small guest program joins that namespace and cgroup, opens its own mount and
  PID namespaces, mounts what the launch names (secrets, workspace, home, a
  private ``/tmp``) and runs Skuld as the slot's user. It is PID 1 of that
  namespace, so ending it ends every process of the session.

Every slot can run containers rootless with Podman: the slot's user gets its own
subordinate uid and gid range, a delegated cgroup inside the slot's (so a
container's limits sit under the slot's), its own runtime directory, and its
storage in its home. It never gets the guest's Docker or sudo, which are root.

Skuld comes from the digest-pinned session image, unpacked once per digest on
the VM (``docker create`` and ``export``; no container runs it) and bind-mounted
read-only at its own paths in the session's mount namespace. Its Python must be
relocatable (uv's python-build-standalone, as in ``ghcr.io/niuulabs/openshell``):
a distribution's interpreter only runs inside its own root.
"""

from __future__ import annotations

import json
import re
from pathlib import PurePosixPath

from pydantic import BaseModel, ConfigDict, Field

from volundr.adapters.outbound.ssh_vm_runtime import _START, SshContainerVmRuntime
from volundr.adapters.outbound.vm_slot_layout import SESSION_LAUNCH, SlotLayout
from volundr.domain.compute import BootstrapFile, MachineBootstrap, SlotLease
from volundr.domain.models import Session, SessionSpec
from volundr.domain.session_runtimes import SessionRuntimeKind

#: Where the session image is unpacked on the VM, one directory per digest.
HOST_IMAGE_CACHE = "/var/lib/niuu/host-images"
#: Where a session sees its unpacked image (wrappers under ``agents``/``tools``).
HOST_IMAGE_VIEW = "/opt/niuu-skuld"
#: Root-only state of the host sessions: their PID files and Skuld's output.
HOST_SESSION_STATE = "/run/niuu/host-sessions"
HOST_SESSION_LOGS = "/var/log/niuu/host-sessions"
#: Agent CLIs the session uses from the pinned image, ahead of everything else on PATH.
AGENT_COMMANDS = ("claude", "codex", "opencode", "grok", "pi", "muse")
#: Tools taken from the image only where neither Nix nor the VM provides them.
FALLBACK_TOOLS = ("gh", "glab", "uv", "uvx", "node", "npm", "npx")
_SYSTEM_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
_HOME = "/home/skuld"
_WORKSPACE = "/workspace"
_DIGEST = re.compile(r"@sha256:([a-f0-9]{64})$")

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

# Unpack the session image once per digest. argv: image, cache root. Prints the
# unpack's description (host.json). The image's own PATH finds the commands.
_UNPACK = r"""
import json, os, pathlib, shlex, shutil, subprocess, sys, tempfile
image, cache = sys.argv[1], pathlib.Path(sys.argv[2])
agents, tools, view = json.loads(sys.argv[3]), json.loads(sys.argv[4]), sys.argv[5]
digest = image.rsplit('@sha256:', 1)[1]
root = cache / digest
if (root / 'host.json').exists():
    print((root / 'host.json').read_text())
    sys.exit(0)
cache.mkdir(parents=True, exist_ok=True, mode=0o755)
quiet = {'stdout': subprocess.DEVNULL}
subprocess.run(['docker', 'pull', '--quiet', image], check=True, **quiet)
info = json.loads(subprocess.run(['docker', 'image', 'inspect', image], check=True,
    capture_output=True, text=True).stdout)[0]
env = dict(item.split('=', 1) for item in info['Config'].get('Env') or [])
venv, python_root = env.get('VIRTUAL_ENV'), env.get('UV_PYTHON_INSTALL_DIR')
if not venv or not python_root:
    raise RuntimeError('The session image declares no VIRTUAL_ENV and UV_PYTHON_INSTALL_DIR; '
        'host mode needs an image on a relocatable Python, such as ghcr.io/niuulabs/openshell')
work = pathlib.Path(tempfile.mkdtemp(dir=cache, prefix='.unpack-'))
rootfs = work / 'rootfs'
rootfs.mkdir()
container = subprocess.run(['docker', 'create', image, 'true'], check=True,
    capture_output=True, text=True).stdout.strip()
try:
    export = subprocess.Popen(['docker', 'export', container], stdout=subprocess.PIPE)
    subprocess.run(['tar', '-x', '-C', str(rootfs), '--exclude=dev/*', '--exclude=proc/*',
        '--exclude=sys/*'], stdin=export.stdout, check=True)
    export.stdout.close()
    if export.wait():
        raise RuntimeError('docker export of the session image failed')
finally:
    subprocess.run(['docker', 'rm', '--force', container], check=True, **quiet)
def resolve(path):
    # A path inside the image, following its links as the image itself would.
    parts, seen = [p for p in pathlib.PurePosixPath(path).parts if p != '/'], 0
    done = []
    while parts:
        part = parts.pop(0)
        if part == '..':
            done = done[:-1]
            continue
        candidate = rootfs.joinpath(*done, part)
        if candidate.is_symlink():
            seen += 1
            if seen > 64:
                raise RuntimeError('Symbolic link loop in the session image at ' + path)
            target = pathlib.PurePosixPath(os.readlink(candidate))
            if target.is_absolute():
                done = []
            parts = [p for p in target.parts if p != '/'] + parts
            continue
        done.append(part)
    return '/' + '/'.join(done)
python = resolve(venv + '/bin/python')
if not python.startswith(python_root.rstrip('/') + '/') or not (rootfs / python[1:]).is_file():
    raise RuntimeError('The session image venv ' + venv + ' does not run on its own Python '
        'under ' + python_root + '; host mode needs a relocatable Python')
search = env.get('PATH', '/usr/local/bin:/usr/bin:/bin').split(':')
def found(name):
    for directory in search:
        # lexists: an absolute link names a path in the image, not on the VM.
        if os.path.lexists(rootfs / directory.lstrip('/') / name):
            target = resolve(directory + '/' + name)
            if (rootfs / target[1:]).is_file():
                return target
    return None
def wrapper(directory, name):
    target = found(name)
    if target is None:
        return
    command = [view + '/rootfs' + target]
    with (rootfs / target[1:]).open('rb') as program:
        line = program.readline(256)
    if line.startswith(b'#!/usr/bin/env '):
        # The image's own interpreter, not whichever one the VM has on PATH.
        interpreter = found(line.split()[1].decode())
        if interpreter is None:
            raise RuntimeError('The session image has no interpreter for ' + name)
        command.insert(0, view + '/rootfs' + interpreter)
    path = work / directory / name
    path.write_text('#!/bin/sh\nexec ' + shlex.join(command) + ' "$@"\n')
    path.chmod(0o755)
for directory, names in (('agents', agents), ('tools', tools)):
    (work / directory).mkdir()
    for name in names:
        wrapper(directory, name)
description = json.dumps({'venv': venv, 'python_root': python_root, 'digest': digest})
(work / 'host.json').write_text(description)
work.chmod(0o755)
try:
    os.rename(work, root)
except OSError:
    # A concurrent unpack of the same digest finished first.
    shutil.rmtree(work)
    if not (root / 'host.json').exists():
        raise
print((root / 'host.json').read_text())
"""

# PID 1 of a host session's PID namespace. Started by _HOST_START as root with
# the session spec on stdin: joins the slot's network namespace and cgroup,
# mounts the session's view, then runs Skuld as the slot's user, forwards stop
# signals to it and reaps whatever it leaves. When Skuld ends, so does this
# process, and with it every process left in the namespace.
_HOST_INIT = r"""
import json, os, pathlib, signal, subprocess, sys
spec = json.load(sys.stdin)
def mount(*args):
    subprocess.run(['mount', *args], check=True)
pathlib.Path(spec['cgroup_procs']).write_text(str(os.getpid()))
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
for path in spec['masked']:
    # The VM's own credentials (its identity token, Forge's host key): an empty,
    # read-only tree in their place, whatever their modes on the VM.
    if os.path.isdir(path):
        mount('-t', 'tmpfs', '-o', 'ro,mode=0755', 'tmpfs', path)
for item in spec['mounts']:
    mount('--bind', *(['-o', 'ro'] if item['readonly'] else []), item['source'], item['target'])
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

# What Skuld's container entrypoint does before it starts the broker: secrets the
# delivery rendered become environment, then Skuld runs from the image's venv.
_BOOT = r"""
set -eu
if [ -f /run/secrets/manifest.json ] && [ -d /run/secrets/user ]; then
    eval "$(python3 -c '
import json, os, shlex
manifest = json.load(open("/run/secrets/manifest.json"))
for name, spec in manifest.get("env", {}).items():
    path = os.path.join("/run/secrets/user", spec["file"])
    if os.path.exists(path):
        value = json.load(open(path)).get(spec.get("key", ""), "")
        print("export " + name + "=" + shlex.quote(value))
for target, spec in manifest.get("files", {}).items():
    source = os.path.join("/run/secrets/user", spec["file"])
    if os.path.exists(source) and not os.path.lexists(target):
        os.makedirs(os.path.dirname(target), exist_ok=True)
        os.symlink(source, target)
')"
fi
if [ -f /run/secrets/env.sh ]; then
    . /run/secrets/env.sh
fi
export CLAUDE_CONFIG_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
mkdir -p "$CLAUDE_CONFIG_DIR" "$WORKSPACE_DIR"
exec "$NIUU_SKULD_PYTHON" -m skuld
"""

# Start (or keep) the slot's host session. Stdin: the placed launch payload with
# its ``host`` section. Exit 0 once the session's PID 1 runs.
_HOST_START = r"""
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
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + h['stop_timeout']
    while os.path.exists('/proc/%d' % pid) and time.monotonic() < deadline:
        time.sleep(h['poll'])
    if os.path.exists('/proc/%d' % pid):
        os.kill(pid, signal.SIGKILL)
current = running()
if current and not p.get('relaunch'):
    sys.exit(0)
if current:
    # A new launch replaced the Forge session credential the running broker read.
    end(current)
root = pathlib.Path(s['data_root'])
marker = root / '.allocation'
if not marker.exists():
    raise RuntimeError('Session storage was not prepared')
if marker.read_text() != s['marker']:
    raise RuntimeError('Workspace allocation mismatch')
workspace, home = root / 'workspace', root / 'home'
workspace.mkdir(exist_ok=True)
home.mkdir(exist_ok=True)
if p['repo'] and not (workspace / '.git').exists():
    args = ['git', 'clone']
    if p['branch']:
        args += ['--branch', p['branch']]
    subprocess.run([*args, '--', p['repo'], str(workspace)], check=True, stdout=subprocess.DEVNULL)
owner = '%d:%d' % (s['uid'], s['uid'])
# -h: a link the session left in its storage names its own path, never a target.
# Container storage keeps the owners its subordinate ids gave it.
prune = [a for path in h['keep_owners'] for a in ('-path', str(root / path), '-prune', '-o')]
subprocess.run(['find', str(root), *prune, '-exec', 'chown', '-h', owner, '{}', '+'], check=True)
for directory in h['private']:
    path = pathlib.Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    os.chown(path, s['uid'], s['uid'])
    path.chmod(0o700)
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
if h['nix'] and not os.path.exists('/nix/var/nix/daemon-socket/socket'):
    raise RuntimeError('This VM has no Nix daemon; install Nix through the machine profile '
        'or set compute.runtime.kwargs.nix: false')
for item in h['mounts']:
    target = pathlib.Path(item['target'])
    # Mountpoints only: in the VM's own tree they stay empty and root-owned.
    if os.path.isdir(item['source']):
        target.mkdir(parents=True, exist_ok=True)
    elif not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.touch()
holder = subprocess.run(['docker', 'inspect', '--format', '{{.State.Pid}}', s['holder']],
    check=True, capture_output=True, text=True).stdout.strip()
# The holder's cgroup (its delegated Docker scope in the slot's slice) becomes a
# tree: the holder in a leaf, and the session's own subtree, delegated to the
# slot's user, where its containers get cgroups of their own under the slot's limits.
scope = pathlib.Path('/sys/fs/cgroup' + pathlib.Path('/proc/%s/cgroup' % holder).read_text()
    .strip().rsplit('::', 1)[1])
if scope.name == 'holder':
    scope = scope.parent
leaf, session = scope / 'holder', scope / 'session'
leaf.mkdir(exist_ok=True)
for pid in (scope / 'cgroup.procs').read_text().split():
    (leaf / 'cgroup.procs').write_text(pid)
available = (scope / 'cgroup.controllers').read_text().split()
wanted = ' '.join('+' + name for name in ('cpu', 'memory', 'pids') if name in available)
(scope / 'cgroup.subtree_control').write_text(wanted)
(session / 'init').mkdir(parents=True, exist_ok=True)
for path in (session, session / 'cgroup.procs', session / 'cgroup.subtree_control',
        session / 'cgroup.threads', session / 'init', session / 'init' / 'cgroup.procs'):
    os.chown(path, s['uid'], s['uid'])
(session / 'cgroup.subtree_control').write_text(wanted)
for directory in (pidfile.parent, pathlib.Path(h['log']).parent):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
pidfile.unlink(missing_ok=True)
spec = {
    'cgroup_procs': str(session / 'init' / 'cgroup.procs'),
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
init = subprocess.Popen([sys.executable, str(program)], stdin=subprocess.PIPE,
    stdout=log, stderr=log, start_new_session=True, close_fds=True)
init.communicate(json.dumps(spec).encode(), timeout=h['start_timeout'])
if init.returncode or not running():
    raise RuntimeError('The host session did not start; see ' + h['log'] + ' on the VM')
"""

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
if c:
    span = '%d-%d' % (c['first'], c['first'] + c['count'] - 1)
    ours = h['user'] + ':' + str(c['first']) + ':' + str(c['count'])
    if ours in pathlib.Path('/etc/subuid').read_text().split():
        subprocess.run(['usermod', '--del-subuids', span, '--del-subgids', span, h['user']],
            check=True)
"""


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


class SshHostVmRuntime(SshContainerVmRuntime):
    """Run each session directly on its VM, in its slot's user, namespaces and cgroup.

    ``skuld_image`` is the digest-pinned session image Skuld, the agent CLIs and
    the guest credential agents come from; its Python must be relocatable (see
    the module docstring). ``slot_isolation`` is required: without a slot there
    is no boundary between the session and the VM.

    ``nix`` says the machine profile installs a multi-user Nix: the session gets
    Nix's profiles on its PATH and the agent is told how to install with it.
    The runtime refuses to start a session when the daemon is missing.

    ``containers`` (``RootlessContainers``) gives every slot rootless Podman; set it
    to ``None`` for slots without containers. The machine profile installs podman,
    uidmap and passt; the runtime refuses to start a session without them.
    """

    def __init__(
        self,
        *,
        nix: bool = True,
        containers: dict | None = {},  # noqa: B006 - read-only; the defaults of RootlessContainers
        session_start_timeout_seconds: float = 30,
        **kwargs,
    ):
        super().__init__(**kwargs)
        if self._slot_isolation is None:
            raise ValueError(
                "Host-mode sessions run in a slot of their VM; configure "
                "compute.runtime.kwargs.slot_isolation"
            )
        if not _DIGEST.search(self._image):
            raise ValueError("Host mode unpacks the session image by digest; pin skuld_image")
        if session_start_timeout_seconds <= 0:
            raise ValueError("session_start_timeout_seconds must be positive")
        self._nix = nix
        self._containers = RootlessContainers(**containers) if containers is not None else None
        self._session_start_timeout = session_start_timeout_seconds
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

    @property
    def session_runtime(self) -> SessionRuntimeKind | None:
        return SessionRuntimeKind.HOST

    def _slot(self, lease) -> SlotLayout:
        layout = self._layout(lease)
        if layout is None:  # pragma: no cover - slot_isolation is required at construction
            raise ValueError("Host-mode sessions need slot_isolation")
        return layout

    def session_bootstrap(
        self, session: Session, spec: SessionSpec, machine: MachineBootstrap
    ) -> MachineBootstrap:
        placed = super().session_bootstrap(session, spec, machine)
        files = []
        for item in placed.files:
            if item.path == SESSION_LAUNCH:
                launch = json.loads(item.content)
                launch["environment"] = self._host_environment(launch["environment"])
                item = BootstrapFile(
                    path=item.path, content=json.dumps(launch), permissions=item.permissions
                )
            files.append(item)
        return placed.model_copy(update={"files": tuple(files)})

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

    def _prepare_arguments(self, lease: SlotLease, mode: str) -> tuple[str, ...]:
        # The session lives on the VM, where Nix's profiles and caches are links to
        # absolute paths by design: restore them as the session left them.
        return (*super()._prepare_arguments(lease, mode), "absolute-links")

    async def warm(self, lease, bootstrap: MachineBootstrap) -> None:
        await super().warm(lease, bootstrap)
        await self._unpack(lease, bootstrap)

    async def _unpack(self, lease, bootstrap: MachineBootstrap) -> dict:
        result = await self._guest_access.execute(
            lease,
            bootstrap,
            self._python(
                _UNPACK,
                self._image,
                HOST_IMAGE_CACHE,
                json.dumps(AGENT_COMMANDS),
                json.dumps(FALLBACK_TOOLS),
                HOST_IMAGE_VIEW,
            ),
            capture_stdout=True,
        )
        return json.loads(result.stdout)

    def _subordinate_ids(self, layout: SlotLayout) -> dict | None:
        if self._containers is None:
            return None
        count = self._containers.subordinate_ids_per_slot
        return {"first": self._containers.subordinate_id_base + layout.slot * count, "count": count}

    def _host_spec(self, layout: SlotLayout, payload: dict, image: dict) -> dict:
        """Where the session's processes run and what they see, for _HOST_START."""
        session_data = PurePosixPath(layout.session_data)
        rootfs = f"{HOST_IMAGE_CACHE}/{image['digest']}/rootfs"
        mounts = [
            {"source": f"{HOST_IMAGE_CACHE}/{image['digest']}", "target": HOST_IMAGE_VIEW},
            {"source": f"{rootfs}{image['venv']}", "target": image["venv"]},
            {"source": f"{rootfs}{image['python_root']}", "target": image["python_root"]},
        ]
        mounts = [{**item, "readonly": True} for item in mounts]
        runtime_directory = f"{layout.run_root}/xdg"
        private = [f"{layout.data_root}/tmp", f"{layout.run_root}/shm", runtime_directory]
        mounts += [
            {"source": "/run/systemd/resolve/resolv.conf", "target": "/etc/resolv.conf"},
            {"source": private[0], "target": "/tmp"},
            {"source": private[0], "target": "/var/tmp"},
            {"source": private[1], "target": "/dev/shm"},
        ]
        mounts = [{"readonly": item.get("readonly", False), **item} for item in mounts]
        mounts += [
            {"source": str(session_data / "workspace"), "target": _WORKSPACE, "readonly": False},
            {"source": str(session_data / "home"), "target": _HOME, "readonly": False},
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
        return {
            "user": layout.name,
            "pidfile": f"{HOST_SESSION_STATE}/{layout.key}.pid",
            "log": f"{HOST_SESSION_LOGS}/{layout.key}.log",
            "nix": self._nix,
            "containers": self._subordinate_ids(layout),
            "bin": HOST_BIN,
            "keep_owners": list(_CONTAINER_STATE) if self._containers is not None else [],
            "mounts": mounts,
            "masked": list(MASKED_GUEST_PATHS),
            "private": private,
            "workdir": _WORKSPACE,
            "boot": _BOOT,
            "init": _HOST_INIT,
            "environment": environment,
            "stop_timeout": self._stop_timeout,
            "poll": self._poll,
            "start_timeout": self._session_start_timeout,
        }

    async def _launch_skuld(
        self, lease: SlotLease, bootstrap: MachineBootstrap, payload: dict
    ) -> None:
        layout = self._slot(lease)
        self._step(lease, "guest_skuld", "Starting the session's agents")
        await self._guest_access.execute(
            lease,
            bootstrap,
            self._python(_START),
            data=json.dumps({**payload, "containers_only": True}).encode(),
        )
        self._step(lease, "guest_image", "Unpacking Skuld onto the VM")
        image = await self._unpack(lease, bootstrap)
        host = self._host_spec(layout, payload, image)
        environment = host.pop("environment")
        self._step(lease, "guest_host", f"Starting Skuld as {layout.name}")
        await self._guest_access.execute(
            lease,
            bootstrap,
            self._python(_HOST_START),
            data=json.dumps({**payload, "environment": environment, "host": host}).encode(),
        )

    async def stop(self, lease: SlotLease, bootstrap: MachineBootstrap) -> None:
        layout = self._slot(lease)
        await self._guest_access.execute(
            lease,
            bootstrap,
            self._python(
                _HOST_STOP,
                json.dumps(
                    {
                        "pidfile": f"{HOST_SESSION_STATE}/{layout.key}.pid",
                        "netns": layout.name,
                        "user": layout.name,
                        "containers": self._subordinate_ids(layout),
                        "stop_timeout": self._stop_timeout,
                        "poll": self._poll,
                    }
                ),
            ),
        )
        # Then as a container session: sidecars, archive, the slot itself.
        await super().stop(lease, bootstrap)
