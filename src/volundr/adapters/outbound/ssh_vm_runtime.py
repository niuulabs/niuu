"""Run Skuld on a VM through pinned OpenSSH, preserving local-disk session data.

Only the SSH listener is exposed. Skuld and the platform callback forward bind
loopback; private provider credentials never enter the guest. The VM image or
operator cloud-init must install Docker, Python 3.12+, and OpenSSH.
"""

from __future__ import annotations

import asyncio  # noqa: F401 - compatibility for tests patching the shared module
import inspect
import json
import logging
import os
import pwd  # noqa: F401 - compatibility for tests patching the shared module
import re
import shlex
import tempfile
from pathlib import Path, PurePosixPath
from uuid import UUID

import httpx

from niuu.domain.services.forge_session_token import IssuedForgeSessionToken
from niuu.observability import get_observability
from niuu.ports.session_proxy import SessionProxyTarget
from volundr.adapters.outbound import vm_slot_supervisor
from volundr.adapters.outbound.brokered_credentials import (
    DEFAULT_CODEX_AUTH_ADAPTER,
    BrokeredCredentialPodManager,
)
from volundr.adapters.outbound.guest_credential_delivery import (
    AGENT_CONFIG_DIRECTORY,
    AGENT_CONFIG_FILE,
)
from volundr.adapters.outbound.host_checks import parse_check_output, slot_audit_arguments
from volundr.adapters.outbound.local_process import (
    FORGE_MCP_GRANTS_ENV,
    FORGE_MCP_TOKEN_FILE_ENV,
    LocalProcessPodManager,
)
from volundr.adapters.outbound.ssh_guest_access import (
    HOST_KEY_CONFIG_PATH,
    HOST_KEY_PATH,
    SshGuestAccess,
)
from volundr.adapters.outbound.vm_hosted_unit import (
    HOSTED_MEMBERS_KEY,
    UNIT_MESH_SOURCE,
    UNIT_MESH_TARGET,
    HostedUnitError,
    member_container,
    member_credential_directory,
    member_spec,
    require_same_pod,
)
from volundr.adapters.outbound.vm_slot_layout import (
    FORGE_SESSION_DIRECTORY,
    SESSION_DATA,
    SESSION_LAUNCH,
    SESSION_RUNTIME,
    SESSION_SECRETS,
    SlotIsolation,
    SlotLayout,
    slot_index,
)
from volundr.domain.compute import (
    AllocationRef,
    BootstrapFile,
    ComputeLease,
    MachineBootstrap,
    SlotLease,
)
from volundr.domain.guest_access import GuestAccess
from volundr.domain.guest_credentials import GuestCredentialDelivery, GuestCredentialPlan
from volundr.domain.models import Session, SessionSpec
from volundr.domain.services.host_access_key import HostAccessKey
from volundr.domain.session_runtimes import SessionRuntimeKind
from volundr.domain.vm_runtime import SlotLimits, StartupStepReport, VmRuntime
from volundr.domain.workflow_session import workflow_session_environment

# cloud-init's SSH module deletes/recreates keys at /etc/ssh/ssh_host_*.
_HOST_KEY = HOST_KEY_PATH
_HOST_KEY_CONFIG = HOST_KEY_CONFIG_PATH
_LAUNCH = SESSION_LAUNCH
#: The guest's local DNS stub (systemd-resolved on 127.0.0.53): never a preview.
VM_GUEST_RESOLVER_PORT = 53
_REMOTE_DATA = SESSION_DATA
_SECRETS = SESSION_SECRETS + "/"
# Pod volumes Kubernetes would keep in memory: guest tmpfs, gone on reboot and
# recreated on every start.
_VOLUMES = f"{SESSION_RUNTIME}/volumes"
_TOKENS = f"{SESSION_RUNTIME}/tokens"
# The Forge session credential of the current launch, outside archived data.
_FORGE_SESSION_DIR = FORGE_SESSION_DIRECTORY
_FORGE_SESSION_TARGET = "/var/run/niuu/forge-session"
_FORGE_SESSION_FILE = "token"
_NAME = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,40}[a-z0-9])?")
_CONTAINER_KEYS = {"name", "image", "command", "args", "env", "volumeMounts", "securityContext"}
_MOUNT_KEYS = {"name", "mountPath", "readOnly"}
#: Directory symlinks in the session images: in the Skuld image ``/var/run`` is ``/run``.
_DEFAULT_IMAGE_PATH_LINKS = {"/var/run": "/run"}

logger = logging.getLogger(__name__)

# Passed as a fixed program; session-controlled values arrive as JSON on stdin.
# Init containers run to completion and sidecars start before Skuld, in the
# order a kubelet uses, on every start (their volumes live on guest tmpfs).
_START = r"""
import json, os, pathlib, subprocess, sys
p=json.load(sys.stdin)
# A slot (vm_slot_layout.SlotLayout.launch) places the session in its own
# directories, namespaces and cgroup; without one the session is the VM's.
s=p.get('slot')
root=pathlib.Path(s['data_root'] if s else '/var/lib/niuu/session')
marker=root/'.allocation'
name=s['skuld'] if s else 'niuu-skuld'
labels=s['labels'] if s else ['compute.niuu.io/allocation='+p['allocation_id']]
def run(*args, env=None):
    return subprocess.run(args,check=True,stdout=subprocess.DEVNULL,
        env={**os.environ,**(env or {})})
def owned(container):
    r=subprocess.run(['docker','container','inspect',container],capture_output=True,text=True)
    if r.returncode:
        return False
    info=json.loads(r.stdout)[0]
    found=info['Config'].get('Labels') or {}
    if any(found.get(k) != v for k,v in (l.split('=',1) for l in labels)):
        raise RuntimeError('Container ownership mismatch')
    return True
def binds(mounts):
    return [arg for m in mounts for arg in
        ('--mount','type=bind,src='+m['source']+',dst='+m['target']
         +(',readonly' if m.get('readonly',True) else ''))]
def placement():
    if not s:
        return ['--network','host']
    # Every container of a slot shares its holder's network and PID namespaces.
    return ['--network','container:'+s['holder'],'--pid','container:'+s['holder'],
        '--cgroup-parent',s['slice'],'--cap-drop','NET_RAW']
def container(c):
    args=[*placement(),*(a for l in labels for a in ('--label',l))]
    if c.get('user'):
        args+=['--user',c['user']]
    args+=[a for key in c['environment'] for a in ('--env',key)]
    args+=binds(c['mounts'])
    if c['command']:
        args+=['--entrypoint',c['command'][0]]
    return [*args,c['image'],*c['command'][1:],*c['args']]
for directory in p.get('directories',[]):
    path=pathlib.Path(directory['path'])
    path.mkdir(parents=True,exist_ok=True)
    path.chmod(int(directory['mode'],8))
address=p.get('metadata_address')
if address:
    # The link-local address the guest agent serves credentials on, in the
    # namespace the session runs in.
    ip=['ip','netns','exec',s['netns'],'ip'] if s else ['ip']
    shown=subprocess.run([*ip,'-o','addr','show','dev','lo'],check=True,capture_output=True,text=True)
    if ' '+address+'/' not in shown.stdout:
        run(*ip,'addr','add',address+'/32','dev','lo')
# The holder of a slot's PID namespace is its init and reaps for every container.
init=[] if s else ['--init']
for c in p.get('init_containers',[]):
    run('docker','run','--rm',*container(c),env=c['environment'])
for c in p.get('sidecars',[]):
    sidecar=(s['sidecar_prefix'] if s else 'niuu-sidecar-')+c['name']
    if owned(sidecar):
        run('docker','start',sidecar)
        continue
    run('docker','run','--detach',*init,'--restart','unless-stopped','--name',sidecar,
        *container(c),env=c['environment'])
if p.get('containers_only'):
    # Another runtime starts the broker itself, next to these containers.
    sys.exit(0)
# The unit's Skuld containers: the session's own, then one per member of a run (the same
# machine or slot, one shared workspace, each its own home and credential directory).
units=[{'id':p['environment'].get('SESSION_ID'),'name':name,'environment':p['environment'],
    'mounts':p.get('volume_mounts',[]),'home':root/'home'}]
for m in p.get('members',[]):
    units.append({'id':m['id'],'name':name+'-m'+str(m['port']),'environment':m['environment'],
        'mounts':m['volume_mounts'],'home':root/'members'/str(m['port'])/'home'})
if p.get('only'):
    # One member again, in place, under its new launch credential.
    units=[u for u in units if u['id'] in p['only']]
missing=[]
for u in units:
    if owned(u['name']):
        # A new launch replaced the Forge session credential the running broker read.
        run('docker','restart' if p.get('relaunch') else 'start',u['name'])
        continue
    missing.append(u)
if not missing:
    sys.exit(0)
if not marker.exists():
    raise RuntimeError('Session storage was not prepared')
if marker.read_text() != (s['marker'] if s else p['allocation_id']):
    raise RuntimeError('Workspace allocation mismatch')
workspace=root/'workspace'
workspace.mkdir(exist_ok=True)
for u in missing:
    u['home'].mkdir(parents=True,exist_ok=True)
if p['repo'] and not (workspace/'.git').exists():
    args=['git','clone']
    if p['branch']:
        args.extend(['--branch',p['branch']])
    args.extend(['--',p['repo'],str(workspace)])
    run(*args)
# -h: a link the session left in its storage names its own path, never a target.
owner=f"{s['uid']}:{s['uid']}" if s else '1000:1000'
run('chown','-R','-h',owner,str(root))
identity=[]
if s:
    # The slot's uid, with the image account's name, home and shell.
    identity=['--user',owner,*binds([
        {'source':s['identity']+'/passwd','target':'/etc/passwd'},
        {'source':s['identity']+'/group','target':'/etc/group'}])]
run('docker','pull',p['image'])
for u in missing:
    env_args=[arg for key in u['environment'] for arg in ('--env',key)]
    run('docker','run','--detach',*init,'--name',u['name'],*placement(),
        *(a for l in labels for a in ('--label',l)),*identity,
        *env_args,*binds(p.get('secret_mounts',[])),*binds(u['mounts']),
        '--mount','type=bind,src='+str(workspace)+',dst=/workspace',
        '--mount','type=bind,src='+str(u['home'])+',dst=/home/skuld',p['image'],
        env=u['environment'])
"""

_PREPARE = r"""
import json,os,pathlib,sys,tarfile
# argv: storage key, restore|empty, exclusions, a slot's storage root, and
# 'absolute-links' where the session's links to absolute paths are restored too.
root=pathlib.Path(sys.argv[4]) if len(sys.argv)>4 else pathlib.Path('/var/lib/niuu/session')
absolute_links=len(sys.argv)>5 and sys.argv[5]=='absolute-links'
allocation=sys.argv[1]
root.mkdir(parents=True,exist_ok=True)
# A slot owns its storage and may have turned it, or the marker, into a link: the
# root is held open without following one, and everything below goes through it.
fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
try:
    existing=os.open('.allocation',os.O_RDONLY|os.O_NOFOLLOW,dir_fd=fd)
except FileNotFoundError:
    existing=None
if existing is not None:
    with os.fdopen(existing) as current:
        if current.read() != allocation:
            raise RuntimeError('Workspace allocation mismatch')
    sys.exit(0)
excludes=[pathlib.PurePosixPath(p) for p in json.loads(sys.argv[3])]
def restore_member(member,destination):
    name=pathlib.PurePosixPath(member.name)
    # The completion marker belongs to this allocation, never the archived one.
    if name == pathlib.PurePosixPath('.allocation'):
        return None
    if any(name == p or p in name.parents for p in excludes):
        return None
    if absolute_links and member.issym() and member.linkname.startswith('/'):
        # Restored as written, never followed: tar_filter still refuses any member
        # whose own path resolves outside the root.
        return tarfile.tar_filter(member,destination)
    return tarfile.data_filter(member,destination)
if sys.argv[2]=='restore':
    # Through the held descriptor on Linux (the guest); macOS runs this only in tests.
    held='/proc/self/fd/%d' % fd if sys.platform=='linux' else str(root)
    with tarfile.open(fileobj=sys.stdin.buffer,mode='r|') as archive:
        archive.extractall(held,filter=restore_member)
marker=os.open('.allocation',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o644,dir_fd=fd)
with os.fdopen(marker,'w') as written:
    written.write(allocation)
"""

_SESSION_FILES = r"""
import json, os, pathlib, sys, tempfile
for item in json.load(sys.stdin):
    path=pathlib.Path(item['path'])
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd, name=tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd,'w') as f:
            os.fchmod(f.fileno(),int(item['permissions'],8))
            f.write(item['content'])
            f.flush()
            os.fsync(f.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name): os.unlink(name)
"""

# Credentials refreshed while the session runs: each lands by rename in a
# directory bind-mounted read-only, so containers see the new file at once.
_REFRESHED_FILES = r"""
import json, os, pathlib, sys, tempfile
items=json.load(sys.stdin)
for item in items:
    directory=pathlib.Path(item['directory'])
    directory.mkdir(parents=True,exist_ok=True)
    directory.chmod(0o755)
    path=directory/item['name']
    path.parent.mkdir(parents=True,exist_ok=True)
    fd, name=tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd,'w') as f:
            os.fchmod(f.fileno(),0o644)
            f.write(item['content'])
            f.flush()
            os.fsync(f.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name): os.unlink(name)
"""

# Exit 43: the controller no longer holds this launch's credential and the guest
# never received it, so the broker cannot start.
_REQUIRE_FILE = r"""
import pathlib,sys
sys.exit(0 if pathlib.Path(sys.argv[1]).is_file() else 43)
"""

_STOP = r"""
import json,pathlib,subprocess,sys
allocation=sys.argv[1]
# A slot (vm_slot_layout.SlotLayout.stop_spec) names its storage and containers; the
# containers of a run's other members (argv[4]) stop before the session's own.
s=json.loads(sys.argv[3]) if len(sys.argv)>3 else None
members=json.loads(sys.argv[4]) if len(sys.argv)>4 else []
marker=pathlib.Path(s['data_root'] if s else '/var/lib/niuu/session')/'.allocation'
name=s['skuld'] if s else 'niuu-skuld'
filters=['--filter','label=compute.niuu.io/allocation='+allocation]
if s:
    filters+=['--filter','label='+s['slot_label']]
if not marker.exists():
    sys.exit(42)
if marker.read_text()!=(s['marker'] if s else allocation):
    raise RuntimeError('Workspace allocation mismatch')
def sidecars():
    # Sidecars of this allocation, stopped after Skuld so it can still flush.
    listed=subprocess.run(['docker','ps','-aq',*filters],capture_output=True,text=True,check=True)
    for container in listed.stdout.split():
        subprocess.run(['docker','stop','--time',sys.argv[2],container],check=True,
            stdout=subprocess.DEVNULL)
def mine(info):
    found=info['Config'].get('Labels') or {}
    if found.get('compute.niuu.io/allocation') != allocation or (
            s and found.get('compute.niuu.io/slot') != s['slot_label'].split('=',1)[1]):
        raise RuntimeError('Container ownership mismatch')
for member in members:
    # A member that never started has nothing to stop.
    r=subprocess.run(['docker','container','inspect',member],capture_output=True,text=True)
    if r.returncode:
        continue
    mine(json.loads(r.stdout)[0])
    subprocess.run(['docker','stop','--time',sys.argv[2],member],check=True,
        stdout=subprocess.DEVNULL)
r=subprocess.run(['docker','container','inspect',name],capture_output=True,text=True)
if r.returncode:
    # Confirm Docker is reachable; missing container is expected after partial startup.
    subprocess.run(['docker','info'],check=True,stdout=subprocess.DEVNULL)
    sidecars()
    sys.exit(0)
mine(json.loads(r.stdout)[0])
subprocess.run(['docker','stop','--time',sys.argv[2],name],check=True,stdout=subprocess.DEVNULL)
sidecars()
"""

# One member of a run's unit: argv is the action (running|stop), the member's container, the
# stop timeout and the labels the unit's containers carry. Exit 44: no such container, or
# it is not running.
_MEMBER = r"""
import json,subprocess,sys
action,name,timeout=sys.argv[1:4]
labels=json.loads(sys.argv[4])
r=subprocess.run(['docker','container','inspect',name],capture_output=True,text=True)
if r.returncode:
    sys.exit(44)
info=json.loads(r.stdout)[0]
found=info['Config'].get('Labels') or {}
if any(found.get(k) != v for k,v in (l.split('=',1) for l in labels)):
    raise RuntimeError('Container ownership mismatch')
if action=='running':
    sys.exit(0 if info['State']['Running'] else 44)
subprocess.run(['docker','stop','--time',timeout,name],check=True,stdout=subprocess.DEVNULL)
"""


class _MemberFiles:
    """The runtime's file channel, as one member of a unit sees it.

    A credential delivery writes the guest agent's configuration to one fixed file. A unit
    runs an agent per member, each configured for its member, so this puts that file
    beside the unit's own, named for the member, and tells the agent which session it
    fetches for. Everything else a delivery writes goes through unchanged.
    """

    def __init__(self, files, member_id: str) -> None:
        self._files = files
        self._member_id = member_id

    async def write_files(
        self, lease: AllocationRef, bootstrap: MachineBootstrap, items: list[dict]
    ) -> None:
        await self._files.write_files(lease, bootstrap, [self._item(item) for item in items])

    async def file_exists(
        self, lease: AllocationRef, bootstrap: MachineBootstrap, path: str
    ) -> bool:
        return await self._files.file_exists(lease, bootstrap, path)

    def _item(self, item: dict) -> dict:
        if item.get("directory") != AGENT_CONFIG_DIRECTORY or item.get("name") != AGENT_CONFIG_FILE:
            return item
        config = json.loads(item["content"])
        config["session_id"] = self._member_id
        return {**item, "name": f"{self._member_id}.json", "content": json.dumps(config)}


class SlotNotCleanError(RuntimeError):
    """A stopped slot still holds something of its session; it must not take another."""


class SshContainerVmRuntime(BrokeredCredentialPodManager, VmRuntime):
    def __init__(
        self,
        *,
        host_key: HostAccessKey | None = None,
        data_dir: str | None = None,
        skuld_image: str,
        codex_auth_adapter: str = DEFAULT_CODEX_AUTH_ADAPTER,
        codex_auth_kwargs: dict | None = None,
        platform_host: str = "127.0.0.1",
        platform_port: int = 8080,
        guest_platform_port: int = 18080,
        guest_platform_bind_host: str = "127.0.0.1",
        ssh_user: str = "ubuntu",
        ssh_port: int = 22,
        broker_port: int = 8081,
        connect_timeout_seconds: int = 10,
        command_timeout_seconds: float = 600,
        stop_timeout_seconds: int = 30,
        poll_interval_seconds: float = 2,
        health_timeout_seconds: float = 5,
        archive_excludes: list[str] | None = None,
        bootstrap_delivery: str = "provider",
        machine_commands: list[list[str]] | tuple[tuple[str, ...], ...] = (),
        guest_access: GuestAccess | None = None,
        host_prepared: bool = False,
        default_service_account: str = "",
        image_path_links: dict[str, str] | None = None,
        preview_router_auth: dict | None = None,
        preview_excluded_ports: list[int] | None = None,
        slot_isolation: dict | None = None,
    ):
        """``preview_router_auth`` turns on live preview in session VMs: the Skuld
        ``preview.router_auth`` adapter (``{"adapter": ..., "kwargs": {...}}``),
        normally ``skuld.preview_adapters.WorkloadJwtRouterAuth`` for Forge's
        workload issuer, whose JWKS the guest reaches through the platform tunnel
        (``http://127.0.0.1:<guest_platform_port>/api/v1/tokens/workload/jwks``).
        The VM is the session, so every listener on it is offered except SSH,
        the guest resolver, the tunnel and broker ports, and
        ``preview_excluded_ports``.

        ``slot_isolation`` (``vm_slot_layout.SlotIsolation``) runs each session in
        its own slot of the VM: its own user, directories, network and PID
        namespaces and cgroup, created by ``vm_slot_supervisor`` at start and
        removed, provably, at stop. Without it the session is the whole VM and
        only slot 0 exists."""
        if not skuld_image or not ssh_user or ssh_user.startswith("-"):
            raise ValueError("Configure a Skuld image and SSH user")
        if slot_isolation is not None and not self._separates_slots:
            raise ValueError(
                f"{type(self).__name__} keeps its own sandbox boundary; remove slot_isolation"
            )
        self._slot_isolation = (
            SlotIsolation(**slot_isolation) if slot_isolation is not None else None
        )
        # The supervisor runs in the guest from its own source; fail at
        # composition when this build cannot ship it.
        self._supervisor = (
            inspect.getsource(vm_slot_supervisor) if self._slot_isolation is not None else ""
        )
        self._preview_router_auth = dict(preview_router_auth or {})
        self._preview_excluded_ports = sorted(
            {ssh_port, VM_GUEST_RESOLVER_PORT, guest_platform_port, broker_port}
            | {int(port) for port in preview_excluded_ports or []}
        )
        if host_prepared and not re.search(r"@sha256:[a-f0-9]{64}$", skuld_image):
            raise ValueError("Prepared-host runtime requires a digest-pinned Skuld image")
        if any(
            p < 1 or p > 65535 for p in (ssh_port, broker_port, platform_port, guest_platform_port)
        ):
            raise ValueError("VM runtime ports must be between 1 and 65535")
        if (
            min(
                connect_timeout_seconds,
                command_timeout_seconds,
                stop_timeout_seconds,
                poll_interval_seconds,
                health_timeout_seconds,
            )
            <= 0
        ):
            raise ValueError("VM runtime timeouts must be positive")
        links = _DEFAULT_IMAGE_PATH_LINKS if image_path_links is None else image_path_links
        if any(
            not PurePosixPath(link).is_absolute()
            or not PurePosixPath(target).is_absolute()
            or ".." in PurePosixPath(link).parts + PurePosixPath(target).parts
            for link, target in links.items()
        ):
            raise ValueError("image_path_links map absolute image paths to absolute paths")
        self._image_path_links = {
            PurePosixPath(link): PurePosixPath(target) for link, target in links.items()
        }
        if guest_platform_bind_host not in {"127.0.0.1", "0.0.0.0"}:
            raise ValueError("guest_platform_bind_host must be '127.0.0.1' or '0.0.0.0'")
        if any(
            not command or any(not str(argument) for argument in command)
            for command in machine_commands
        ):
            raise ValueError("VM machine commands must contain non-empty arguments")
        self._owns_guest_access = guest_access is None
        if guest_access is None:
            if not data_dir or host_key is None:
                raise ValueError(
                    "Configure data_dir (Forge provides the pool's host key), or inject "
                    "guest_access"
                )
            guest_access = SshGuestAccess(
                host_key=host_key,
                data_dir=data_dir,
                ssh_user=ssh_user,
                ssh_port=ssh_port,
                connect_timeout_seconds=connect_timeout_seconds,
                command_timeout_seconds=command_timeout_seconds,
                poll_interval_seconds=poll_interval_seconds,
                bootstrap_delivery=bootstrap_delivery,
                machine_commands=machine_commands,
            )
        elif machine_commands:
            raise ValueError("machine_commands belong to the injected guest access binding")
        if data_dir is None:
            if isinstance(guest_access, SshGuestAccess):
                data_dir = str(guest_access.data_dir)
            else:
                raise ValueError("Injected guest access requires a runtime data_dir")
        self._guest_access = guest_access
        self._data = Path(data_dir).expanduser().resolve()
        self._data.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._archive_excludes = tuple(
            str(PurePosixPath(p))
            for p in (archive_excludes if archive_excludes is not None else ("home/.codex/tmp",))
        )
        if any(
            p == "."
            or PurePosixPath(p).is_absolute()
            or ".." in PurePosixPath(p).parts
            or any(char in p for char in "*?[]")
            for p in self._archive_excludes
        ):
            raise ValueError("VM archive exclusions must be explicit relative paths, not globs")
        self._private_key = (
            guest_access.private_key if isinstance(guest_access, SshGuestAccess) else None
        )
        self._public_key = (
            guest_access.public_key if isinstance(guest_access, SshGuestAccess) else None
        )
        self._subprocess_env = (
            guest_access.subprocess_env if isinstance(guest_access, SshGuestAccess) else None
        )
        self._image = skuld_image
        self._configure_brokered_credentials(
            codex_auth_adapter=codex_auth_adapter, codex_auth_kwargs=codex_auth_kwargs
        )
        self._platform_host, self._platform_port = platform_host, platform_port
        self._guest_platform_port = guest_platform_port
        self._guest_platform_bind_host = guest_platform_bind_host
        self._ssh_user, self._ssh_port = guest_access.principal, ssh_port
        self._broker_port = broker_port
        self._connect_timeout = connect_timeout_seconds
        self._command_timeout = command_timeout_seconds
        self._stop_timeout = stop_timeout_seconds
        self._poll = poll_interval_seconds
        self._health_timeout = health_timeout_seconds
        self._bootstrap_delivery = bootstrap_delivery
        self._machine_commands = ()
        self._host_prepared = host_prepared
        self._default_service_account = default_service_account
        self._credential_delivery: GuestCredentialDelivery | None = None
        self._report_startup: StartupStepReport | None = None

    def report_startup_to(self, report: StartupStepReport) -> None:
        """Name each step of ``start`` as it begins, for the session's launch stages."""
        self._report_startup = report

    def _step(self, lease: SlotLease, key: str, label: str) -> None:
        if self._report_startup is not None:
            self._report_startup(lease.session_id, key, label)

    def configure_credential_delivery(self, delivery: GuestCredentialDelivery) -> None:
        """Attach the configured delivery of ServiceAccount and Forge session credentials."""
        self._credential_delivery = delivery

    @property
    def delivers_forge_session_token(self) -> bool:
        """The broker reads its launch credential from a file the delivery provides."""
        delivery = self._credential_delivery
        return delivery is not None and delivery.delivers_forge_session_token

    @property
    def supports_slots(self) -> bool:
        """Separated slots are configured, and every slot can receive its own credentials.

        ``slot_isolation`` gives each slot its own user, directories, network and PID
        namespaces and cgroup (``vm_slot_supervisor``). A credential delivery that only
        the whole allocation can receive would still hand every slot one identity.
        """
        if self._slot_isolation is None:
            return False
        delivery = self._credential_delivery
        return delivery is None or delivery.serves_slots

    #: Whether the guest runs the pod's init containers and sidecars next to Skuld.
    _runs_pod_containers = True
    #: Where a slot's guest paths live (``vm_slot_layout.SlotLayout``).
    _layout_class: type[SlotLayout] = SlotLayout
    #: Whether ``slot_isolation`` applies: runtimes with their own sandbox refuse it.
    _separates_slots = True

    def _layout(self, lease: AllocationRef) -> SlotLayout | None:
        """The lease's slot, or ``None`` for the whole VM without slot isolation."""
        if self._slot_isolation is None:
            slot = slot_index(lease)
            if slot != 0:
                raise ValueError(
                    f"Slot {slot} of a VM needs separated slots; configure "
                    "compute.runtime.kwargs.slot_isolation"
                )
            return None
        return self._layout_class.for_lease(self._slot_isolation, lease)

    async def _supervise(
        self, lease: SlotLease, bootstrap: MachineBootstrap, layout: SlotLayout, action: str
    ) -> bytes:
        result = await self._guest_access.execute(
            lease,
            bootstrap,
            self._python(self._supervisor, action),
            data=json.dumps(
                layout.supervisor_spec(image=self._image, platform_port=self._guest_platform_port)
            ).encode(),
            capture_stdout=True,
            safe_error_prefix=vm_slot_supervisor.STAGE_PREFIX,
            safe_detail_prefix=vm_slot_supervisor.DETAIL_PREFIX,
        )
        return result.stdout

    @property
    def session_runtime(self) -> SessionRuntimeKind | None:
        return SessionRuntimeKind.CONTAINER

    @property
    def requires_root_daemon(self) -> bool:
        """Skuld, its sidecars and the slot holders run under the guest's root Docker."""
        return True

    @property
    def slot_limits(self) -> SlotLimits | None:
        isolation = self._slot_isolation
        if isolation is None:
            return None
        return SlotLimits(
            uid_base=isolation.uid_base,
            cpu=isolation.cpu,
            memory_mib=isolation.memory_mib,
            pids=isolation.pids,
        )

    def stage_forge_session(self, session_id: UUID, credential: IssuedForgeSessionToken) -> None:
        """Hand over the credential the next start of ``session_id`` delivers."""
        if self._credential_delivery is not None:
            self._credential_delivery.stage_forge_session(session_id, credential)

    def discard_forge_session(self, session_id: UUID) -> None:
        if self._credential_delivery is not None:
            self._credential_delivery.discard_forge_session(session_id)

    async def write_files(
        self, lease: AllocationRef, bootstrap: MachineBootstrap, items: list[dict]
    ) -> None:
        # Deliveries name the session's paths; a slot keeps them in its own directories.
        layout = self._layout(lease)
        if layout is not None:
            items = [{**item, "directory": layout.relocate(item["directory"])} for item in items]
        await self._guest_access.execute(
            lease, bootstrap, self._python(_REFRESHED_FILES), data=json.dumps(items).encode()
        )

    async def file_exists(
        self, lease: AllocationRef, bootstrap: MachineBootstrap, path: str
    ) -> bool:
        layout = self._layout(lease)
        result = await self._guest_access.execute(
            lease,
            bootstrap,
            self._python(_REQUIRE_FILE, layout.relocate(path) if layout else path),
            expected_exit_codes=(0, 43),
        )
        return result.exit_code == 0

    def _ssh_subprocess_environment(self) -> dict[str, str] | None:
        """Retained compatibility hook; SSH access owns process identity setup."""
        if not isinstance(self._guest_access, SshGuestAccess):
            return None
        return self._guest_access.subprocess_env

    def bootstrap(
        self, session: Session, spec: SessionSpec, defaults: MachineBootstrap
    ) -> MachineBootstrap:
        return self.session_bootstrap(session, spec, self.machine_bootstrap(defaults))

    def session_bootstrap(
        self, session: Session, spec: SessionSpec, machine: MachineBootstrap
    ) -> MachineBootstrap:
        if session.source.type != "git":
            raise ValueError(
                "SSH VM sessions accept Git or an empty workspace, not host-path mounts"
            )
        if any(spec.values.get(key) for key in ("homeVolume", "persistence")):
            raise ValueError("SSH VM runtime cannot silently replace configured storage")
        layout = self._pod_layout(spec)
        entries = list(spec.values.get(HOSTED_MEMBERS_KEY) or [])
        for entry in entries:
            require_same_pod(spec, entry)
        member_ports = tuple(entry["port"] for entry in entries)
        env = self._launch_environment(
            session, spec, port=self._broker_port, member_ports=member_ports
        )
        if spec.forge_session is not None:
            # The token itself is written at start, never into the stored bootstrap.
            env[FORGE_MCP_TOKEN_FILE_ENV] = f"{_FORGE_SESSION_TARGET}/{_FORGE_SESSION_FILE}"
            layout["volume_mounts"].append(
                {"source": _FORGE_SESSION_DIR, "target": _FORGE_SESSION_TARGET, "readonly": True}
            )
        launch = {
            "image": self._image,
            "environment": env,
            "repo": session.source.repo,
            "branch": session.source.branch,
            "secret_mounts": layout["secret_mounts"],
            "volume_mounts": layout["volume_mounts"],
            "directories": layout["directories"],
            "init_containers": layout["init_containers"],
            "sidecars": layout["sidecars"],
            "service_account": layout["service_account"],
            "token_volumes": layout["token_volumes"],
            "forge_session": spec.forge_session is not None,
            "forge_session_directory": _FORGE_SESSION_DIR,
            "forge_session_file": _FORGE_SESSION_FILE,
        }
        if entries:
            self._add_members(session, launch, entries)
        plan = GuestCredentialPlan.from_launch(launch)
        member_plans = [m for m in launch.get("members", []) if m["forge_session"]]
        if (not plan.empty or member_plans) and self._credential_delivery is None:
            raise ValueError(
                "This VM session needs delivered credentials; configure compute.credential_delivery"
            )
        if not plan.empty:
            added = self._credential_delivery.session_containers(plan, image=self._delivery_image())
            # Delivery containers run first: the others read what they deliver.
            launch["init_containers"] = [*added.init_containers, *launch["init_containers"]]
            launch["sidecars"] = [*added.sidecars, *launch["sidecars"]]
            launch["directories"] = [
                *({"path": path, "mode": "0755"} for path in added.directories),
                *launch["directories"],
            ]
            launch.update(added.launch)
        for member in member_plans:
            self._add_member_agent(launch, member)
        launch["directories"] = [
            *launch["directories"],
            *self._nested_mountpoints(
                [*launch["secret_mounts"], *launch["volume_mounts"]], owner="the session"
            ),
            *(
                directory
                for container in (*launch["init_containers"], *launch["sidecars"])
                for directory in self._nested_mountpoints(
                    container["mounts"], owner=f"container {container['name']!r}"
                )
            ),
        ]
        payload = json.dumps(self._shape_launch(session, launch))
        return machine.model_copy(
            update={
                "files": (
                    *machine.files,
                    BootstrapFile(path=_LAUNCH, content=payload),
                    *layout["files"],
                )
            }
        )

    def _launch_environment(
        self,
        session: Session,
        spec: SessionSpec,
        *,
        port: int,
        member_ports: tuple[int, ...] = (),
    ) -> dict[str, str]:
        """The environment of one Skuld of the machine: the session's, or a unit member's."""
        pod = spec.pod_spec
        brokered = self._with_brokered_credentials(spec)
        env = LocalProcessPodManager._session_env(brokered, Path("/workspace"))
        env.update(self._brokered_credential_environment(brokered))
        for entry in pod.env:
            if "valueFrom" in entry or "value" not in entry or not entry.get("name"):
                raise ValueError("SSH VM session env must contain literal name/value entries")
            env[str(entry["name"])] = str(entry["value"])
        env.update(
            {
                "SESSION_ID": str(session.id),
                "WORKSPACE_DIR": "/workspace",
                "SKULD__SESSION__ID": str(session.id),
                "SKULD__SESSION__NAME": session.name,
                "SKULD__SESSION__MODEL": session.model,
                "SKULD__SESSION__OWNER_ID": session.owner_id or "",
                "SKULD__SESSION__TENANT_ID": session.tenant_id or "",
                "SKULD__SESSION__WORKSPACE_DIR": "/workspace",
                "SKULD__PERSISTENCE_MOUNT_PATH": "/home/skuld",
                "SKULD__HOST": "127.0.0.1",
                "SKULD__PORT": str(port),
                "SKULD__VOLUNDR_API_URL": f"http://127.0.0.1:{self._guest_platform_port}",
                "HOME": "/home/skuld",
            }
        )
        env.update(self._preview_env(member_ports))
        for source, target in [
            ("systemPrompt", "SYSTEM_PROMPT"),
            ("initialPrompt", "INITIAL_PROMPT"),
        ]:
            value = spec.values.get("session", {}).get(source)
            if value:
                env[f"SKULD__SESSION__{target}"] = str(value)
        if persona := spec.values.get("session", {}).get("persona"):
            env["SKULD__SESSION__PERSONA"] = json.dumps(persona, separators=(",", ":"))
        env.update(workflow_session_environment(spec.values))
        if any("\x00" in v for v in env.values()):
            raise ValueError("SSH VM environment cannot contain NUL values")
        if any(not k or "=" in k or any(c.isspace() for c in k) or "\x00" in k for k in env):
            raise ValueError("Invalid VM environment variable name")
        return env

    def _add_members(self, session: Session, launch: dict, entries: list[dict]) -> None:
        """Put a run's members into the unit's launch payload.

        The unit is one machine (or slot) with one pod layout: its mounts, volumes,
        sidecars and ServiceAccount are the root's. Each member adds its own Skuld
        environment and port, its own Forge session credential directory (mounted into
        its container alone) and the unit's shared mesh directory, where every Skuld finds
        the others through the registry discovery file. They all share the workspace and
        each keeps its own home.
        """
        seen: set[int] = {self._broker_port}
        mesh = {"source": UNIT_MESH_SOURCE, "target": UNIT_MESH_TARGET, "readonly": False}
        root_mounts = launch["volume_mounts"]
        shared = [mount for mount in root_mounts if mount["source"] != _FORGE_SESSION_DIR]
        launch["volume_mounts"] = [*root_mounts, mesh]
        launch["directories"] = [*launch["directories"], {"path": UNIT_MESH_SOURCE, "mode": "0777"}]
        members = []
        for entry in entries:
            port = entry["port"]
            if port in seen:
                raise HostedUnitError(
                    f"Member session {entry['id']} has the broker port {port}, which another "
                    "Skuld of the unit already uses"
                )
            seen.add(port)
        for entry in entries:
            port = entry["port"]
            member = Session.model_construct(
                id=UUID(entry["id"]),
                name=entry["name"],
                model=entry["model"],
                owner_id=entry["owner_id"] or None,
                tenant_id=entry["tenant_id"] or None,
            )
            spec = member_spec(entry)
            environment = self._launch_environment(
                member,
                spec,
                port=port,
                member_ports=tuple(sorted(seen - {port, self._broker_port})),
            )
            directory = member_credential_directory(entry["id"])
            mounts = [*shared, mesh]
            if entry["forge_session"]:
                environment[FORGE_MCP_TOKEN_FILE_ENV] = (
                    f"{_FORGE_SESSION_TARGET}/{_FORGE_SESSION_FILE}"
                )
                mounts.append(
                    {"source": directory, "target": _FORGE_SESSION_TARGET, "readonly": True}
                )
                launch["directories"] = [
                    *launch["directories"],
                    {"path": directory, "mode": "0755"},
                ]
            environment.update(workflow_session_environment(spec.values))
            members.append(
                {
                    "id": entry["id"],
                    "port": port,
                    "environment": environment,
                    "volume_mounts": mounts,
                    "forge_session": entry["forge_session"],
                    "forge_session_directory": directory,
                    "forge_session_file": _FORGE_SESSION_FILE,
                }
            )
        launch["members"] = members

    def _add_member_agent(self, launch: dict, member: dict) -> None:
        """The credential agent of one member, next to the unit's, when delivery runs one."""
        if not member["forge_session"]:
            return
        plan = GuestCredentialPlan(
            service_account="",
            forge_session=True,
            forge_session_directory=member["forge_session_directory"],
            forge_session_file=member["forge_session_file"],
        )
        added = self._credential_delivery.session_containers(plan, image=self._delivery_image())
        config = f"{AGENT_CONFIG_DIRECTORY}/{member['id']}.json"
        suffix = member["id"][:8]
        for key in ("init_containers", "sidecars"):
            renamed = [self._member_agent_container(c, suffix, config) for c in getattr(added, key)]
            launch[key] = [*launch[key], *renamed]
        launch.update(added.launch)
        launch["directories"] = [
            *launch["directories"],
            *({"path": path, "mode": "0755"} for path in added.directories),
        ]

    @staticmethod
    def _member_agent_container(container: dict, suffix: str, config: str) -> dict:
        """A delivery's agent container, named and configured for one member."""
        command = list(container["command"])
        if "--config" not in command or command.index("--config") + 1 >= len(command):
            raise HostedUnitError(
                f"The credential delivery's agent container {container['name']!r} takes no "
                "--config argument, so a unit member cannot get an agent of its own"
            )
        command[command.index("--config") + 1] = config
        return {**container, "name": f"{container['name']}-{suffix}", "command": command}

    def _shape_launch(self, session: Session, launch: dict) -> dict:
        """The launch payload as this runtime runs it; checks or changes it before it is stored.

        A container session runs the payload as built. A runtime that runs the session
        on the machine itself gives it the machine's environment, or refuses what it
        cannot run, before anything is kept.
        """
        return launch

    def machine_bootstrap(self, defaults: MachineBootstrap) -> MachineBootstrap:
        if any(
            f.path == _LAUNCH or f.path.startswith("/etc/niuu/session-secrets/")
            for f in defaults.files
        ):
            raise ValueError("Default bootstrap conflicts with VM runtime files")
        machine = self._guest_access.machine_bootstrap(defaults)
        if self._credential_delivery is not None:
            machine = self._credential_delivery.machine_bootstrap(machine)
        return machine

    async def warm(self, lease: ComputeLease, bootstrap: MachineBootstrap) -> None:
        await self.prepare(lease, bootstrap)
        await self._guest_access.execute(
            lease,
            bootstrap,
            shlex.join(["sudo", "-n", "docker", "pull", self._image]),
        )

    def _pod_layout(self, spec: SessionSpec) -> dict:
        """Translate the session's pod volumes and containers into guest Docker.

        The subset a guest can honour without Kubernetes: read-only hostPath files
        (copied in the bootstrap), emptyDir (guest tmpfs), projected ServiceAccount
        tokens (issued and refreshed by this controller), and init/sidecar
        containers with literal env. Anything else is refused rather than dropped.
        """
        pod = spec.pod_spec
        volumes = {v.get("name"): v for v in pod.volumes}
        if len(volumes) != len(pod.volumes):
            raise ValueError("Duplicate VM volume names")
        files: list[BootstrapFile] = []
        sources: dict[str, tuple[str, str]] = {}
        directories: list[dict] = []
        token_volumes: list[dict] = []
        for name, volume in volumes.items():
            if not isinstance(name, str) or not _NAME.fullmatch(name):
                raise ValueError(f"VM volume names must be DNS labels: {name!r}")
            kinds = set(volume) - {"name"}
            if kinds == {"hostPath"}:
                sources[name] = ("file", "")
            elif kinds == {"emptyDir"}:
                sources[name] = ("directory", f"{_VOLUMES}/{name}")
                # The mode Kubernetes gives an emptyDir: any container user may write.
                directories.append({"path": f"{_VOLUMES}/{name}", "mode": "0777"})
            elif kinds == {"projected"}:
                token_volumes.append(self._token_volume(name, volume["projected"]))
                sources[name] = ("token", f"{_TOKENS}/{name}")
            else:
                raise ValueError(
                    f"VM sessions cannot provide volume {name!r} ({', '.join(sorted(kinds))}); "
                    "they support read-only hostPath files, emptyDir and projected "
                    "serviceAccountToken volumes"
                )
        if not self._runs_pod_containers and (
            directories or token_volumes or pod.init_containers or pod.extra_containers
        ):
            raise ValueError(
                f"{type(self).__name__} runs no pod sidecars, emptyDir or "
                "ServiceAccount token volumes"
            )
        used: set[str] = set()

        def mounts(entries, *, owner: str) -> tuple[list[dict], list[dict]]:
            secret, volume = [], []
            for mount in entries:
                name = mount.get("name")
                target = str(mount.get("mountPath", ""))
                if (
                    set(mount) - _MOUNT_KEYS
                    or name not in sources
                    or not target.startswith("/")
                    or target == "/"
                    or ".." in PurePosixPath(target).parts
                    or any(char in target for char in (",", "\x00"))
                ):
                    raise ValueError(
                        f"VM mounts of {owner} need a declared volume, an absolute target "
                        "and no subPath"
                    )
                kind, guest = sources[name]
                used.add(name)
                if kind == "file":
                    host = volumes[name]["hostPath"]
                    if not mount.get("readOnly") or host.get("type") != "File":
                        raise ValueError(
                            "VM secret mounts require read-only hostPath files and absolute targets"
                        )
                    source = Path(host["path"]).expanduser()
                    if not source.is_file():
                        raise ValueError("VM secret source is not an existing file")
                    guest = f"{_SECRETS}{len(files)}"
                    files.append(
                        BootstrapFile(path=guest, content=source.read_text(), permissions="0644")
                    )
                    secret.append({"source": guest, "target": target})
                    continue
                volume.append(
                    {
                        "source": guest,
                        "target": target,
                        "readonly": kind == "token" or bool(mount.get("readOnly")),
                    }
                )
            targets = [m["target"] for m in (*secret, *volume)]
            if len(set(targets)) != len(targets):
                raise ValueError(f"Duplicate VM mount targets in {owner}")
            return secret, volume

        secret_mounts, volume_mounts = mounts(pod.volume_mounts, owner="the session")
        containers: dict[str, list[dict]] = {"init_containers": [], "sidecars": []}
        names: set[str] = set()
        for key, declared in (
            ("init_containers", pod.init_containers),
            ("sidecars", pod.extra_containers),
        ):
            for container in declared:
                translated = self._container(container, mounts)
                if translated["name"] in names:
                    raise ValueError(f"Duplicate VM container name {translated['name']!r}")
                names.add(translated["name"])
                containers[key].append(translated)
        if used != set(volumes):
            raise ValueError("VM runtime cannot ignore unmounted or unsupported volumes")
        service_account = pod.service_account or self._default_service_account
        if token_volumes and self._credential_delivery is None:
            raise ValueError(
                "This VM session needs ServiceAccount tokens; configure compute.credential_delivery"
            )
        if token_volumes and not service_account:
            raise ValueError(
                "This VM session needs ServiceAccount tokens but names no ServiceAccount; "
                "set compute.runtime.kwargs.default_service_account"
            )
        return {
            "files": files,
            "secret_mounts": secret_mounts,
            "volume_mounts": volume_mounts,
            "directories": directories,
            "token_volumes": token_volumes,
            "service_account": service_account or "",
            **containers,
        }

    def _delivery_image(self) -> str:
        """The image the guest's credential delivery containers run."""
        return self._image

    def _in_image(self, target: str) -> PurePosixPath:
        """Where a mount target lands inside the image, through its directory symlinks."""
        path = PurePosixPath(target)
        for link, resolved in self._image_path_links.items():
            if path == link or link in path.parents:
                return resolved / path.relative_to(link)
        return path

    def _nested_mountpoints(self, mounts: list[dict], *, owner: str) -> list[dict]:
        """Mountpoints that must exist in a read-only bind's guest directory before start.

        The container runtime creates a missing mountpoint inside the container. When it
        falls inside a read-only bind (``/var/run/secrets/x`` in ``/run/secrets``) it
        cannot, and the container never starts; the guest creates it in that bind's
        source first, so the parent bind stays read-only.
        """
        placed = [(self._in_image(mount["target"]), mount) for mount in mounts]
        directories = []
        for target, mount in placed:
            parents = [(path, other) for path, other in placed if path in target.parents]
            if not parents:
                continue
            parent_target, parent = max(parents, key=lambda entry: len(entry[0].parts))
            if not parent.get("readonly", True):
                continue
            if parent["source"].startswith(_SECRETS):
                raise ValueError(
                    f"VM mount {mount['target']!r} of {owner} lies inside the secret file "
                    f"mounted at {parent['target']!r}"
                )
            path = PurePosixPath(parent["source"]) / target.relative_to(parent_target)
            directories.append({"path": str(path), "mode": "0755"})
        return directories

    @staticmethod
    def _token_volume(name: str, projected: dict) -> dict:
        tokens = []
        if set(projected) - {"sources", "defaultMode"} or not projected.get("sources"):
            raise ValueError(f"Projected VM volume {name!r} must list serviceAccountToken sources")
        for entry in projected["sources"]:
            source = (
                entry.get("serviceAccountToken") if set(entry) == {"serviceAccountToken"} else None
            )
            if not isinstance(source, dict):
                raise ValueError(
                    f"Projected VM volume {name!r} may hold only serviceAccountToken sources"
                )
            path = PurePosixPath(str(source.get("path", "")))
            audience = str(source.get("audience", ""))
            if (
                set(source) - {"path", "audience", "expirationSeconds"}
                or not path.parts
                or path.is_absolute()
                or ".." in path.parts
                or not audience
            ):
                raise ValueError(
                    f"ServiceAccount tokens in VM volume {name!r} need a relative path "
                    "and an explicit audience"
                )
            tokens.append(
                {
                    "path": str(path),
                    "audience": audience,
                    # The kubelet's default lifetime for a projected token.
                    "expiration_seconds": int(source.get("expirationSeconds", 3600)),
                }
            )
        return {"volume": name, "directory": f"{_TOKENS}/{name}", "tokens": tokens}

    @staticmethod
    def _container(container: dict, mounts) -> dict:
        name = container.get("name")
        if (
            set(container) - _CONTAINER_KEYS
            or not isinstance(name, str)
            or not _NAME.fullmatch(name)
        ):
            raise ValueError(
                f"VM pod containers take a DNS-label name and only "
                f"{', '.join(sorted(_CONTAINER_KEYS))}: {name!r}"
            )
        if not container.get("image"):
            raise ValueError(f"VM container {name!r} needs an image")
        environment = {}
        for entry in container.get("env", ()):
            if set(entry) != {"name", "value"} or not entry["name"]:
                raise ValueError(
                    f"VM container {name!r} env must contain literal name/value entries"
                )
            environment[str(entry["name"])] = str(entry["value"])
        security = container.get("securityContext") or {}
        if set(security) - {"runAsUser", "runAsGroup"}:
            raise ValueError(
                f"VM container {name!r} securityContext takes runAsUser/runAsGroup only"
            )
        user = ""
        if "runAsUser" in security:
            user = str(int(security["runAsUser"]))
            if "runAsGroup" in security:
                user += f":{int(security['runAsGroup'])}"
        elif "runAsGroup" in security:
            raise ValueError(f"VM container {name!r} sets runAsGroup without runAsUser")
        secret, volume = mounts(container.get("volumeMounts", ()), owner=f"container {name!r}")
        return {
            "name": name,
            "image": str(container["image"]),
            "command": [str(part) for part in container.get("command", ())],
            "args": [str(part) for part in container.get("args", ())],
            "environment": environment,
            "user": user,
            "mounts": [{**m, "readonly": True} for m in secret] + volume,
        }

    def _ssh(self, lease: AllocationRef, bootstrap: MachineBootstrap) -> list[str]:
        if not isinstance(self._guest_access, SshGuestAccess):
            raise TypeError("Raw SSH command inspection requires SshGuestAccess")
        return self._guest_access.command_argv(lease, bootstrap)

    def _bootstrap_ssh(self, lease: AllocationRef) -> list[str]:
        """Build the explicit first-contact SSH command for post-connect bootstrap."""
        if not isinstance(self._guest_access, SshGuestAccess):
            raise TypeError("Raw SSH command inspection requires SshGuestAccess")
        return self._guest_access.bootstrap_argv(lease)

    def _bootstrap_verified_marker(self, lease: AllocationRef) -> Path:
        if not isinstance(self._guest_access, SshGuestAccess):
            raise TypeError("SSH bootstrap markers require SshGuestAccess")
        return self._guest_access.bootstrap_verified_marker(lease)

    def _allocation_directory(self, lease: AllocationRef) -> Path:
        return self._data / str(lease.id)

    def _has_host_pin(self, lease: AllocationRef) -> bool:
        if not isinstance(self._guest_access, SshGuestAccess):
            raise TypeError("SSH host pin inspection requires SshGuestAccess")
        return self._guest_access._has_host_pin(lease)

    @staticmethod
    def _host_alias(lease: AllocationRef) -> str:
        return SshGuestAccess._host_alias(lease)

    @staticmethod
    def _ssh_machine_bootstrap(bootstrap: MachineBootstrap) -> MachineBootstrap:
        """Remove session data and controller-generated private identity from TOFU delivery."""
        return bootstrap.model_copy(
            update={
                "files": tuple(
                    item
                    for item in bootstrap.files
                    if item.path not in {_HOST_KEY, _HOST_KEY + ".pub", _HOST_KEY_CONFIG, _LAUNCH}
                    and not item.path.startswith("/etc/niuu/session-secrets/")
                )
            }
        )

    async def _deliver_bootstrap_over_ssh(
        self, lease: AllocationRef, bootstrap: MachineBootstrap
    ) -> None:
        machine = self._ssh_machine_bootstrap(bootstrap)
        await self._guest_access.ensure(lease, machine)

    @staticmethod
    def _python(program: str, *args: str) -> str:
        return SshGuestAccess.python_command(program, *args)

    async def _run(
        self,
        argv: list[str],
        *,
        data: bytes | None = None,
        stdin=None,
        stdout=None,
        expected_exit_codes: tuple[int, ...] = (0,),
        safe_error_prefix: str | None = None,
        safe_detail_prefix: str | None = None,
    ) -> int:
        result = await self._guest_access.run_argv(
            argv,
            data=data,
            stdin=stdin,
            stdout=stdout,
            expected_exit_codes=expected_exit_codes,
            safe_error_prefix=safe_error_prefix,
            safe_detail_prefix=safe_detail_prefix,
        )
        return result.exit_code

    def _preview_env(self, member_ports: tuple[int, ...] = ()) -> dict[str, str]:
        """Skuld live-preview settings for a session VM (see ``__init__``).

        The brokers of a run's other members are listeners of the machine too, and not
        previews.
        """
        if not self._preview_router_auth:
            return {"SKULD__PREVIEW__ENABLED": "false"}
        return {
            "SKULD__PREVIEW__ENABLED": "true",
            "SKULD__PREVIEW__PORT_SCANNER": json.dumps(
                {
                    "adapter": "skuld.preview_adapters.ProcNetListeningPortScanner",
                    "kwargs": {
                        "excluded_ports": sorted({*self._preview_excluded_ports, *member_ports}),
                        # The guest's own services run as root on ports no list
                        # names (containerd's stream server picks one per boot).
                        "same_user_only": True,
                    },
                }
            ),
            "SKULD__PREVIEW__ROUTER_AUTH": json.dumps(self._preview_router_auth),
        }

    def _tunnel_ends(self, layout) -> dict:
        """Where the broker listens and where its callbacks to Forge arrive, on the guest."""
        if layout is None:
            return {
                "reverse_bind_host": self._guest_platform_bind_host,
                "reverse_port": self._guest_platform_port,
            }
        # The slot's broker listens on the slot's address; its callbacks reach the slot's
        # own loopback port, where its gateway forwards them.
        return {
            "remote_host": layout.address,
            "reverse_bind_host": "127.0.0.1",
            "reverse_port": layout.platform_forward_port,
            "key": layout.key,
        }

    async def target(self, lease: AllocationRef, bootstrap: MachineBootstrap) -> SessionProxyTarget:
        target = await self._guest_access.open_tunnel(
            lease,
            bootstrap,
            remote_port=self._broker_port,
            controller_host=self._platform_host,
            controller_port=self._platform_port,
            **self._tunnel_ends(self._layout(lease)),
        )
        await self._resume_delivery(lease, bootstrap)
        return target

    @staticmethod
    def _launch(bootstrap: MachineBootstrap) -> dict | None:
        launch = next((f.content for f in bootstrap.files if f.path == _LAUNCH), None)
        return json.loads(launch) if launch is not None else None

    async def _resume_delivery(self, lease: AllocationRef, bootstrap: MachineBootstrap) -> None:
        # A machine with no session has no credentials to resume.
        if not isinstance(lease, SlotLease):
            return
        launch = self._launch(bootstrap)
        if self._credential_delivery is None or launch is None:
            return
        plan = GuestCredentialPlan.from_launch(launch)
        if not plan.empty:
            await self._credential_delivery.resume(lease, bootstrap, plan, self)

    def _tunnel_options(self, port: int) -> list[str]:
        return [
            "-N",
            "-L",
            f"127.0.0.1:{port}:127.0.0.1:{self._broker_port}",
            "-R",
            f"127.0.0.1:{self._guest_platform_port}:{self._platform_host}:{self._platform_port}",
        ]

    async def prepare(self, lease: AllocationRef, bootstrap: MachineBootstrap) -> None:
        if self._host_prepared:
            await self._guest_access.execute(lease, bootstrap, "true")
            return
        access_bootstrap = bootstrap
        if self._bootstrap_delivery == "ssh":
            access_bootstrap = self._ssh_machine_bootstrap(bootstrap)
        await self._guest_access.ensure(lease, access_bootstrap)

    async def start(self, lease: SlotLease, bootstrap: MachineBootstrap) -> None:
        layout = self._layout(lease)
        session_directory = self._data / str(lease.session_id)
        session_directory.mkdir(mode=0o700, exist_ok=True, parents=True)
        if layout is not None:
            self._require_slot_delivery(layout, bootstrap)
            self._step(lease, "guest_slot", f"Preparing slot {layout.slot}")
            await self._supervise(lease, bootstrap, layout, "up")
        # Warm machines were created without session secrets. Deliver them only
        # after the durable binding, through the authenticated guest transport.
        files = [
            f.model_dump()
            for f in bootstrap.files
            if f.path == _LAUNCH or f.path.startswith("/etc/niuu/session-secrets/")
        ]
        if layout is not None:
            files = [{**item, "path": layout.relocate(item["path"])} for item in files]
        self._step(lease, "guest_files", "Delivering session files")
        await self._guest_access.execute(
            lease,
            bootstrap,
            self._python(_SESSION_FILES),
            data=json.dumps(files).encode(),
        )
        await self._restore_data(lease, bootstrap)
        self._step(lease, "guest_tunnel", "Opening the tunnel to Forge")
        await self.target(lease, bootstrap)
        payload = await self._launch_payload(lease, bootstrap, layout)
        await self._launch_skuld(lease, bootstrap, payload)

    async def _launch_payload(
        self,
        lease: SlotLease,
        bootstrap: MachineBootstrap,
        layout: SlotLayout | None,
        *,
        only: UUID | None = None,
    ) -> dict:
        """The stored launch payload with this launch's credentials delivered, placed.

        ``only`` names one member of the unit to start again in place: its credential is
        delivered and the start program is told to touch no other Skuld.
        """
        payload = json.loads(next(f.content for f in bootstrap.files if f.path == _LAUNCH))
        payload["allocation_id"] = str(lease.id)
        plan = GuestCredentialPlan.from_launch(payload)
        if only is None and not plan.empty:
            if self._credential_delivery is None:
                raise ValueError(
                    "This VM session needs delivered credentials; configure "
                    "compute.credential_delivery"
                )
            self._step(lease, "guest_credentials", "Delivering credentials")
            await self._credential_delivery.start(lease, bootstrap, plan, payload, self)
            grants = payload.pop("forge_session_grants", None)
            if grants is not None:
                payload["environment"][FORGE_MCP_GRANTS_ENV] = ",".join(grants)
        await self._deliver_member_credentials(lease, bootstrap, payload, only)
        if only is not None:
            payload["only"] = [str(only)]
            payload["relaunch"] = True
        if layout is not None:
            payload = self._place_launch(layout, payload)
        return payload

    async def _deliver_member_credentials(
        self, lease: SlotLease, bootstrap: MachineBootstrap, payload: dict, only: UUID | None
    ) -> None:
        """Each member's Forge session credential, into that member's own directory."""
        for member in payload.get("members") or []:
            if only is not None and member["id"] != str(only):
                continue
            plan = GuestCredentialPlan.from_launch(member)
            if plan.empty:
                continue
            if self._credential_delivery is None:
                raise ValueError(
                    "This VM session needs delivered credentials; configure "
                    "compute.credential_delivery"
                )
            self._step(lease, "guest_credentials", "Delivering credentials")
            started: dict = {}
            await self._credential_delivery.start(
                self._member_lease(lease, member["id"]),
                bootstrap,
                plan,
                started,
                _MemberFiles(self, member["id"]),
            )
            grants = started.get("forge_session_grants")
            if grants is not None:
                member["environment"][FORGE_MCP_GRANTS_ENV] = ",".join(grants)
            if started.get("relaunch"):
                payload["relaunch"] = True

    @staticmethod
    def _member_lease(lease: SlotLease, member_id: str) -> SlotLease:
        """The unit's lease as one of its members sees it: its own session, the same slot."""
        return SlotLease(
            allocation=lease.allocation,
            binding=lease.binding.model_copy(update={"session_id": UUID(member_id)}),
        )

    def _place_launch(self, layout, payload: dict) -> dict:
        """The launch payload placed in the session's slot."""
        return layout.launch(payload, platform_port=self._guest_platform_port)

    async def _launch_skuld(
        self, lease: SlotLease, bootstrap: MachineBootstrap, payload: dict
    ) -> None:
        """Run the session's containers and Skuld from the placed launch payload."""
        self._step(lease, "guest_skuld", "Pulling and starting Skuld")
        await self._guest_access.execute(
            lease,
            bootstrap,
            self._python(_START),
            data=json.dumps(payload).encode(),
        )

    def _require_slot_delivery(self, layout: SlotLayout, bootstrap: MachineBootstrap) -> None:
        """Refuse a slot past 0 whose credentials only the whole allocation can receive."""
        launch = self._launch(bootstrap)
        if (
            layout.slot == 0
            or self._credential_delivery is None
            or self._credential_delivery.serves_slots
            or launch is None
            or GuestCredentialPlan.from_launch(launch).empty
        ):
            return
        raise ValueError(
            f"{type(self._credential_delivery).__name__} vouches for the whole allocation, so "
            f"slot {layout.slot} cannot receive its credentials; run one slot per VM or use a "
            "per-slot credential delivery"
        )

    def _prepare_arguments(self, lease: SlotLease, mode: str) -> tuple[str, ...]:
        layout = self._layout(lease)
        excludes = json.dumps(self._archive_excludes)
        if layout is None:
            return (str(lease.id), mode, excludes)
        return (layout.key, mode, excludes, layout.session_data)

    async def _restore_data(self, lease: SlotLease, bootstrap: MachineBootstrap) -> None:
        archive = self._data / str(lease.session_id) / "session.tar"
        if archive.exists():
            self._step(lease, "guest_restore", "Restoring the workspace")
            with archive.open("rb") as src:
                await self._guest_access.execute(
                    lease,
                    bootstrap,
                    self._python(_PREPARE, *self._prepare_arguments(lease, "restore")),
                    stdin=src,
                )
        else:
            await self._guest_access.execute(
                lease,
                bootstrap,
                self._python(_PREPARE, *self._prepare_arguments(lease, "empty")),
            )

    # -- The members of a run's unit ------------------------------------------------------

    def _member(self, bootstrap: MachineBootstrap, member_id: UUID) -> dict:
        launch = self._launch(bootstrap) or {}
        member = next((m for m in launch.get("members") or [] if m["id"] == str(member_id)), None)
        if member is None:
            raise HostedUnitError(
                f"Session {member_id} is not a member of the unit this VM was launched with; "
                "a run's members are fixed when its unit starts"
            )
        return member

    def _member_container_name(self, layout: SlotLayout | None, member: dict) -> str:
        return member_container(layout.skuld if layout else "niuu-skuld", member["port"])

    def _member_labels(self, lease: SlotLease, layout: SlotLayout | None) -> list[str]:
        if layout is None:
            return [f"{vm_slot_supervisor.ALLOCATION_LABEL}={lease.id}"]
        return [
            f"{vm_slot_supervisor.ALLOCATION_LABEL}={lease.id}",
            f"{vm_slot_supervisor.SLOT_LABEL}={layout.slot}",
        ]

    async def member_running(
        self, lease: SlotLease, bootstrap: MachineBootstrap, member_id: UUID
    ) -> bool:
        """Whether the member's container runs."""
        layout = self._layout(lease)
        member = self._member(bootstrap, member_id)
        result = await self._guest_access.execute(
            lease,
            bootstrap,
            self._python(
                _MEMBER,
                "running",
                self._member_container_name(layout, member),
                str(self._stop_timeout),
                json.dumps(self._member_labels(lease, layout)),
            ),
            expected_exit_codes=(0, 44),
        )
        return result.exit_code == 0

    async def member_target(
        self, lease: SlotLease, bootstrap: MachineBootstrap, member_id: UUID
    ) -> SessionProxyTarget:
        """A tunnel to the member's broker, beside the unit's own.

        The unit's tunnel carries the callbacks to Forge for every Skuld of the machine
        (they share its loopback or the slot's gateway), so this one carries only the
        forward: its reverse listener asks the guest for a port it picks, and nothing
        connects to it.
        """
        layout = self._layout(lease)
        member = self._member(bootstrap, member_id)
        ends = self._tunnel_ends(layout)
        ends["reverse_port"] = 0
        ends["key"] = f"{ends.get('key') or lease.id}-m{member['port']}"
        return await self._guest_access.open_tunnel(
            lease,
            bootstrap,
            remote_port=member["port"],
            controller_host=self._platform_host,
            controller_port=self._platform_port,
            **ends,
        )

    async def member_ready(
        self, lease: SlotLease, bootstrap: MachineBootstrap, member_id: UUID
    ) -> bool:
        target = await self.member_target(lease, bootstrap, member_id)
        try:
            async with httpx.AsyncClient(timeout=self._health_timeout) as client:
                response = await client.get(target.service_url + "/health")
        except httpx.TransportError:
            return False
        return response.status_code == 200

    async def restart_member(
        self, lease: SlotLease, bootstrap: MachineBootstrap, member_id: UUID
    ) -> None:
        """Start the member's Skuld again under its new launch, the rest of the unit as is."""
        layout = self._layout(lease)
        self._member(bootstrap, member_id)
        payload = await self._launch_payload(lease, bootstrap, layout, only=member_id)
        await self._launch_skuld(lease, bootstrap, payload)

    async def stop_member(
        self, lease: SlotLease, bootstrap: MachineBootstrap, member_id: UUID
    ) -> None:
        layout = self._layout(lease)
        member = self._member(bootstrap, member_id)
        await self._guest_access.execute(
            lease,
            bootstrap,
            self._python(
                _MEMBER,
                "stop",
                self._member_container_name(layout, member),
                str(self._stop_timeout),
                json.dumps(self._member_labels(lease, layout)),
            ),
            expected_exit_codes=(0, 44),
        )
        await self._close_tunnel(f"{layout.key if layout else lease.id}-m{member['port']}")

    def _member_names(self, lease: SlotLease, bootstrap: MachineBootstrap) -> list[str]:
        """The unit's member containers, in the order they stop."""
        launch = self._launch(bootstrap) or {}
        layout = self._layout(lease)
        return [
            self._member_container_name(layout, member)
            for member in reversed(launch.get("members") or [])
        ]

    async def ready(self, lease: SlotLease, bootstrap: MachineBootstrap) -> bool:
        target = await self.target(lease, bootstrap)
        try:
            async with httpx.AsyncClient(timeout=self._health_timeout) as client:
                response = await client.get(target.service_url + "/health")
        except httpx.TransportError:
            return False
        return response.status_code == 200

    async def _stop_session(self, lease: SlotLease, bootstrap: MachineBootstrap, layout) -> int:
        """Stop Skuld, then its agents; the exit code is 42 when storage was never prepared."""
        arguments = [str(lease.id), str(self._stop_timeout)]
        members = self._member_names(lease, bootstrap)
        if layout is not None or members:
            arguments.append(json.dumps(layout.stop_spec() if layout is not None else None))
        if members:
            arguments.append(json.dumps(members))
        result = await self._guest_access.execute(
            lease,
            bootstrap,
            self._python(_STOP, *arguments),
            expected_exit_codes=(0, 42),
        )
        return result.exit_code

    async def stop(self, lease: SlotLease, bootstrap: MachineBootstrap) -> None:
        layout = self._layout(lease)
        exit_code = await self._stop_session(lease, bootstrap, layout)
        if self._credential_delivery is not None:
            await self._credential_delivery.stop(lease, bootstrap)
        # Exit 42: cancelled before storage restore; retain any previous local archive.
        if exit_code != 42:
            await self._archive_data(lease, bootstrap)
        if layout is not None:
            # Archived: the slot's user, directories, namespaces and cgroup go,
            # and the supervisor fails unless nothing of them is left.
            if isinstance(layout, SlotLayout):
                await self._report_slot_limits(lease, bootstrap, layout)
            await self._supervise(lease, bootstrap, layout, "down")
        key = layout.key if layout else str(lease.id)
        for member in (self._launch(bootstrap) or {}).get("members") or []:
            await self._close_tunnel(f"{key}-m{member['port']}")
        await self._close_tunnel(key)

    async def _audit_slot(
        self, lease: AllocationRef, bootstrap: MachineBootstrap, program: str, slot: int
    ) -> None:
        """Run the registered-host audit ``program`` for ``slot`` alone; any finding raises."""
        limits = self.slot_limits
        if limits is None:
            raise ValueError(f"{type(self).__name__} keeps no slots to audit")
        arguments = slot_audit_arguments(uid_base=limits.uid_base, slot=slot)
        result = await self._guest_access.execute(
            lease,
            bootstrap,
            shlex.join(["sudo", "-n", "sh", "-s", "--", *arguments]),
            data=program.encode(),
            capture_stdout=True,
        )
        findings = parse_check_output(result.stdout.decode(errors="replace")).findings
        if findings:
            raise SlotNotCleanError(
                f"Slot {slot} of allocation {lease.id} is not clean after its session, so it "
                f"is not reused: {'; '.join(findings)}"
            )

    async def _report_slot_limits(
        self, lease: SlotLease, bootstrap: MachineBootstrap, layout: SlotLayout
    ) -> None:
        """Count every time the ending slot hit its memory or PID limit."""
        events = json.loads(await self._supervise(lease, bootstrap, layout, "limits") or b"{}")
        for event, count in sorted(events.items()):
            if not count:
                continue
            get_observability().count(
                "volundr.compute.slot.limit_hits",
                count,
                attributes={"limit": event},
            )
            logger.error(
                "Session %s in slot %d of VM allocation %s hit its %s limit %d times; raise "
                "compute.runtime.kwargs.slot_isolation.%s",
                lease.session_id,
                layout.slot,
                lease.id,
                event,
                count,
                "pids" if event.startswith("pids") else "memory_mib",
            )

    async def _archive_data(self, lease: SlotLease, bootstrap: MachineBootstrap) -> None:
        layout = self._layout(lease)
        directory = self._data / str(lease.session_id)
        directory.mkdir(mode=0o700, exist_ok=True, parents=True)
        fd, temporary = tempfile.mkstemp(prefix="session-", suffix=".tar", dir=directory)
        try:
            with os.fdopen(fd, "wb") as dest:
                await self._guest_access.execute(
                    lease,
                    bootstrap,
                    shlex.join(
                        [
                            "sudo",
                            "-n",
                            "tar",
                            "--one-file-system",
                            "--exclude=./.allocation",
                            *[f"--exclude=./{p}" for p in self._archive_excludes],
                            "-C",
                            layout.session_data if layout else _REMOTE_DATA,
                            "-cf",
                            "-",
                            ".",
                        ]
                    ),
                    stdout=dest,
                )
                dest.flush()
                os.fsync(dest.fileno())
            os.replace(temporary, directory / "session.tar")
        finally:
            Path(temporary).unlink(missing_ok=True)

    async def _close_tunnel(self, key: str) -> None:
        await self._guest_access.close_tunnel(key)

    async def close(self) -> None:
        if self._owns_guest_access:
            await self._guest_access.close()


class HostModeRuntime(SshContainerVmRuntime):
    """A session that runs on the machine itself as its slot's user, with no session container.

    What every host-mode runtime shares (Linux slots, Macs): no root daemon on the
    machine, reuse of a slot once it is proven clean (``recycle`` runs the registered-host
    audit ``_audit_program`` for that slot), the session's absolute links restored as it
    left them (Nix makes its profiles that way), the ``nix`` flag (the host recipe
    installs a multi-user Nix; a session is refused when the daemon is missing) and how
    long the session may take to start.
    """

    #: The registered-host audit of this runtime's platform (``host_checks``).
    _audit_program: str = ""

    def __init__(
        self, *, nix: bool = True, session_start_timeout_seconds: float = 30, **kwargs
    ) -> None:
        if session_start_timeout_seconds <= 0:
            raise ValueError("session_start_timeout_seconds must be positive")
        super().__init__(**kwargs)
        self._nix = nix
        self._session_start_timeout = session_start_timeout_seconds

    @classmethod
    def _release_image(
        cls, own: str | None, released: str | None, *, kwarg: str, setting: str
    ) -> str:
        """The image a runtime runs: its own ``kwarg``, or the release's; never both, or none."""
        if released and own:
            raise ValueError(
                f"{kwarg} is set and Forge hands this runtime the release's session image "
                f"too; remove {kwarg} to run the release's"
            )
        image = released or own
        if not image:
            raise ValueError(
                f"{cls.__name__} needs a session image: set {kwarg}, or {setting} for the "
                "built-in host execution"
            )
        return image

    @property
    def session_runtime(self) -> SessionRuntimeKind | None:
        return SessionRuntimeKind.HOST

    @property
    def requires_root_daemon(self) -> bool:
        return False

    @property
    def supports_reuse(self) -> bool:
        """``stop`` removes the slot and proves it gone; ``recycle`` audits it again."""
        return True

    async def recycle(self, lease: SlotLease, bootstrap: MachineBootstrap) -> None:
        """Prove the slot ``stop`` removed is clean before it takes another session."""
        await self._audit_slot(lease, bootstrap, self._audit_program, slot_index(lease))

    def _prepare_arguments(self, lease: SlotLease, mode: str) -> tuple[str, ...]:
        return (*super()._prepare_arguments(lease, mode), "absolute-links")
