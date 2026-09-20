"""Run Skuld on a VM through pinned OpenSSH, preserving local-disk session data.

Only the SSH listener is exposed. Skuld and the platform callback forward bind
loopback; private provider credentials never enter the guest. The VM image or
operator cloud-init must install Docker, Python 3.12+, and OpenSSH.
"""

from __future__ import annotations

import asyncio  # noqa: F401 - compatibility for tests patching the shared module
import json
import os
import pwd  # noqa: F401 - compatibility for tests patching the shared module
import re
import shlex
import tempfile
from pathlib import Path, PurePosixPath

import httpx

from niuu.ports.session_proxy import SessionProxyTarget
from volundr.adapters.outbound.brokered_credentials import (
    DEFAULT_CODEX_AUTH_ADAPTER,
    BrokeredCredentialPodManager,
)
from volundr.adapters.outbound.local_process import LocalProcessPodManager
from volundr.adapters.outbound.ssh_guest_access import (
    HOST_KEY_CONFIG_PATH,
    HOST_KEY_PATH,
    SshGuestAccess,
)
from volundr.domain.compute import BootstrapFile, ComputeLease, MachineBootstrap
from volundr.domain.guest_access import GuestAccess
from volundr.domain.models import Session, SessionSpec
from volundr.domain.vm_runtime import VmRuntime

# cloud-init's SSH module deletes/recreates keys at /etc/ssh/ssh_host_*.
_HOST_KEY = HOST_KEY_PATH
_HOST_KEY_CONFIG = HOST_KEY_CONFIG_PATH
_LAUNCH = "/etc/niuu/session-launch.json"
_REMOTE_DATA = "/var/lib/niuu/session"

# Passed as a fixed program; session-controlled values arrive as JSON on stdin.
_START = r"""
import json, os, pathlib, subprocess, sys
p=json.load(sys.stdin)
root=pathlib.Path('/var/lib/niuu/session')
marker=root/'.allocation'
name='niuu-skuld'
def run(*args, **kwargs):
    return subprocess.run(args,check=True,stdout=subprocess.DEVNULL,**kwargs)
existing=subprocess.run(['docker','container','inspect',name],capture_output=True,text=True)
if existing.returncode == 0:
    info=json.loads(existing.stdout)[0]
    if info['Config'].get('Labels',{}).get('compute.niuu.io/allocation') != p['allocation_id']:
        raise RuntimeError('Container ownership mismatch')
    run('docker','start',name)
    sys.exit(0)
if not marker.exists():
    raise RuntimeError('Session storage was not prepared')
if marker.read_text() != p['allocation_id']:
    raise RuntimeError('Workspace allocation mismatch')
workspace=root/'workspace'
home=root/'home'
workspace.mkdir(exist_ok=True)
home.mkdir(exist_ok=True)
if p['repo'] and not (workspace/'.git').exists():
    args=['git','clone']
    if p['branch']:
        args.extend(['--branch',p['branch']])
    args.extend(['--',p['repo'],str(workspace)])
    run(*args)
run('chown','-R','1000:1000',str(root))
env_args=[arg for key in p['environment'] for arg in ('--env',key)]
secret_mounts=[arg for mount in p.get('secret_mounts',[]) for arg in
    ('--mount','type=bind,src='+mount['source']+',dst='+mount['target']+',readonly')]
run('docker','pull',p['image'])
run('docker','run','--detach','--init','--name',name,'--network','host',
    '--label','compute.niuu.io/allocation='+p['allocation_id'],
    *env_args,*secret_mounts,
    '--mount','type=bind,src='+str(workspace)+',dst=/workspace',
    '--mount','type=bind,src='+str(home)+',dst=/home/skuld',p['image'],
    env={**os.environ,**p['environment']})
"""

_PREPARE = r"""
import json,pathlib,sys,tarfile
root=pathlib.Path('/var/lib/niuu/session')
marker=root/'.allocation'
allocation=sys.argv[1]
root.mkdir(parents=True,exist_ok=True)
if marker.exists():
    if marker.read_text() != allocation:
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
    return tarfile.data_filter(member,destination)
if sys.argv[2]=='restore':
    with tarfile.open(fileobj=sys.stdin.buffer,mode='r|') as archive:
        archive.extractall(root,filter=restore_member)
marker.write_text(allocation)
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

_STOP = r"""
import json,pathlib,subprocess,sys
allocation=sys.argv[1]
marker=pathlib.Path('/var/lib/niuu/session/.allocation')
if not marker.exists():
    sys.exit(42)
if marker.read_text()!=allocation:
    raise RuntimeError('Workspace allocation mismatch')
r=subprocess.run(['docker','container','inspect','niuu-skuld'],capture_output=True,text=True)
if r.returncode:
    # Confirm Docker is reachable; missing container is expected after partial startup.
    subprocess.run(['docker','info'],check=True,stdout=subprocess.DEVNULL)
    sys.exit(0)
info=json.loads(r.stdout)[0]
if info['Config'].get('Labels',{}).get('compute.niuu.io/allocation') != allocation:
    raise RuntimeError('Container ownership mismatch')
subprocess.run(['docker','stop','--time',sys.argv[2],'niuu-skuld'],check=True,stdout=subprocess.DEVNULL)
"""


class SshContainerVmRuntime(BrokeredCredentialPodManager, VmRuntime):
    def __init__(
        self,
        *,
        ssh_private_key_file: str | None = None,
        ssh_public_key_file: str | None = None,
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
    ):
        if not skuld_image or not ssh_user or ssh_user.startswith("-"):
            raise ValueError("Configure a Skuld image and SSH user")
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
        if guest_platform_bind_host not in {"127.0.0.1", "0.0.0.0"}:
            raise ValueError("guest_platform_bind_host must be '127.0.0.1' or '0.0.0.0'")
        if any(
            not command or any(not str(argument) for argument in command)
            for command in machine_commands
        ):
            raise ValueError("VM machine commands must contain non-empty arguments")
        self._owns_guest_access = guest_access is None
        if guest_access is None:
            if not ssh_private_key_file or not ssh_public_key_file or not data_dir:
                raise ValueError("Configure SSH key files and data_dir, or inject guest_access")
            guest_access = SshGuestAccess(
                ssh_private_key_file=ssh_private_key_file,
                ssh_public_key_file=ssh_public_key_file,
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
        pod = spec.pod_spec
        if any(
            (
                pod.service_account,
                pod.init_containers,
                pod.extra_containers,
            )
        ):
            raise ValueError(
                "SSH VM sessions require local-disk storage; "
                "Kubernetes service accounts and sidecars are unsupported"
            )
        if session.source.type != "git":
            raise ValueError(
                "SSH VM sessions accept Git or an empty workspace, not host-path mounts"
            )
        if any(spec.values.get(key) for key in ("homeVolume", "persistence", "flock", "ravnFlock")):
            raise ValueError(
                "SSH VM runtime cannot silently replace configured storage or flock topology"
            )
        secret_files, secret_mounts = self._secret_files(spec)
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
                "SKULD__PORT": str(self._broker_port),
                "SKULD__VOLUNDR_API_URL": f"http://127.0.0.1:{self._guest_platform_port}",
                "HOME": "/home/skuld",
            }
        )
        for source, target in [
            ("systemPrompt", "SYSTEM_PROMPT"),
            ("initialPrompt", "INITIAL_PROMPT"),
        ]:
            value = spec.values.get("session", {}).get(source)
            if value:
                env[f"SKULD__SESSION__{target}"] = str(value)
        if any("\x00" in v for v in env.values()):
            raise ValueError("SSH VM environment cannot contain NUL values")
        if any(not k or "=" in k or any(c.isspace() for c in k) or "\x00" in k for k in env):
            raise ValueError("Invalid VM environment variable name")
        payload = json.dumps(
            {
                "image": self._image,
                "environment": env,
                "repo": session.source.repo,
                "branch": session.source.branch,
                "secret_mounts": secret_mounts,
            }
        )
        return machine.model_copy(
            update={
                "files": (
                    *machine.files,
                    BootstrapFile(path=_LAUNCH, content=payload),
                    *secret_files,
                )
            }
        )

    def machine_bootstrap(self, defaults: MachineBootstrap) -> MachineBootstrap:
        if any(
            f.path == _LAUNCH or f.path.startswith("/etc/niuu/session-secrets/")
            for f in defaults.files
        ):
            raise ValueError("Default bootstrap conflicts with VM runtime files")
        return self._guest_access.machine_bootstrap(defaults)

    async def warm(self, lease: ComputeLease, bootstrap: MachineBootstrap) -> None:
        await self.prepare(lease, bootstrap)
        await self._guest_access.execute(
            lease,
            bootstrap,
            shlex.join(["sudo", "-n", "docker", "pull", self._image]),
        )

    @staticmethod
    def _secret_files(spec: SessionSpec) -> tuple[list[BootstrapFile], list[dict[str, str]]]:
        """Carry the existing injector's read-only files to guest Docker binds.

        Credentials stay outside archived workspace/home, and use Skuld's normal
        env.sh/file readers. Kubernetes agent injection cannot be snapshotted.
        """
        volumes = {v.get("name"): v for v in spec.pod_spec.volumes}
        if len(volumes) != len(spec.pod_spec.volumes):
            raise ValueError("Duplicate VM volume names")
        files, mounts, used = [], [], set()
        for mount in spec.pod_spec.volume_mounts:
            name = mount.get("name")
            volume = volumes.get(name, {})
            host = volume.get("hostPath", {})
            target = str(mount.get("mountPath", ""))
            if (
                not mount.get("readOnly")
                or mount.get("subPath")
                or mount.get("subPathExpr")
                or host.get("type") != "File"
                or not target.startswith("/")
                or target == "/"
                or ".." in PurePosixPath(target).parts
                or any(char in target for char in (",", "\x00"))
            ):
                raise ValueError(
                    "VM secret mounts require read-only hostPath files and absolute targets"
                )
            source = Path(host["path"]).expanduser()
            if not source.is_file():
                raise ValueError("VM secret source is not an existing file")
            guest = f"/etc/niuu/session-secrets/{len(files)}"
            files.append(BootstrapFile(path=guest, content=source.read_text(), permissions="0644"))
            mounts.append({"source": guest, "target": target})
            used.add(name)
        if used != set(volumes):
            raise ValueError("VM runtime cannot ignore unmounted or unsupported volumes")
        if len({m["target"] for m in mounts}) != len(mounts):
            raise ValueError("Duplicate VM secret mount targets")
        return files, mounts

    def _ssh(self, lease: ComputeLease, bootstrap: MachineBootstrap) -> list[str]:
        if not isinstance(self._guest_access, SshGuestAccess):
            raise TypeError("Raw SSH command inspection requires SshGuestAccess")
        return self._guest_access.command_argv(lease, bootstrap)

    def _bootstrap_ssh(self, lease: ComputeLease) -> list[str]:
        """Build the explicit first-contact SSH command for post-connect bootstrap."""
        if not isinstance(self._guest_access, SshGuestAccess):
            raise TypeError("Raw SSH command inspection requires SshGuestAccess")
        return self._guest_access.bootstrap_argv(lease)

    def _bootstrap_verified_marker(self, lease: ComputeLease) -> Path:
        if not isinstance(self._guest_access, SshGuestAccess):
            raise TypeError("SSH bootstrap markers require SshGuestAccess")
        return self._guest_access.bootstrap_verified_marker(lease)

    def _allocation_directory(self, lease: ComputeLease) -> Path:
        return self._data / str(lease.id)

    def _has_host_pin(self, lease: ComputeLease) -> bool:
        if not isinstance(self._guest_access, SshGuestAccess):
            raise TypeError("SSH host pin inspection requires SshGuestAccess")
        return self._guest_access._has_host_pin(lease)

    @staticmethod
    def _host_alias(lease: ComputeLease) -> str:
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
        self, lease: ComputeLease, bootstrap: MachineBootstrap
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

    async def target(self, lease: ComputeLease, bootstrap: MachineBootstrap) -> SessionProxyTarget:
        return await self._guest_access.open_tunnel(
            lease,
            bootstrap,
            remote_port=self._broker_port,
            reverse_bind_host=self._guest_platform_bind_host,
            reverse_port=self._guest_platform_port,
            controller_host=self._platform_host,
            controller_port=self._platform_port,
        )

    def _tunnel_options(self, port: int) -> list[str]:
        return [
            "-N",
            "-L",
            f"127.0.0.1:{port}:127.0.0.1:{self._broker_port}",
            "-R",
            f"127.0.0.1:{self._guest_platform_port}:{self._platform_host}:{self._platform_port}",
        ]

    async def prepare(self, lease: ComputeLease, bootstrap: MachineBootstrap) -> None:
        if self._host_prepared:
            await self._guest_access.execute(lease, bootstrap, "true")
            return
        access_bootstrap = bootstrap
        if self._bootstrap_delivery == "ssh":
            access_bootstrap = self._ssh_machine_bootstrap(bootstrap)
        await self._guest_access.ensure(lease, access_bootstrap)

    async def start(self, lease: ComputeLease, bootstrap: MachineBootstrap) -> None:
        if lease.session_id is None:
            raise ValueError("Cannot start an unbound guest")
        session_directory = self._data / str(lease.session_id)
        session_directory.mkdir(mode=0o700, exist_ok=True, parents=True)
        # Warm machines were created without session secrets. Deliver them only
        # after the durable binding, through the authenticated guest transport.
        files = [
            f.model_dump()
            for f in bootstrap.files
            if f.path == _LAUNCH or f.path.startswith("/etc/niuu/session-secrets/")
        ]
        await self._guest_access.execute(
            lease,
            bootstrap,
            self._python(_SESSION_FILES),
            data=json.dumps(files).encode(),
        )
        await self._restore_data(lease, bootstrap)
        await self.target(lease, bootstrap)
        payload = json.loads(next(f.content for f in bootstrap.files if f.path == _LAUNCH))
        payload["allocation_id"] = str(lease.id)
        await self._guest_access.execute(
            lease,
            bootstrap,
            self._python(_START),
            data=json.dumps(payload).encode(),
        )

    async def _restore_data(self, lease: ComputeLease, bootstrap: MachineBootstrap) -> None:
        archive = self._data / str(lease.session_id) / "session.tar"
        if archive.exists():
            with archive.open("rb") as src:
                await self._guest_access.execute(
                    lease,
                    bootstrap,
                    self._python(
                        _PREPARE, str(lease.id), "restore", json.dumps(self._archive_excludes)
                    ),
                    stdin=src,
                )
        else:
            await self._guest_access.execute(
                lease,
                bootstrap,
                self._python(_PREPARE, str(lease.id), "empty", json.dumps(self._archive_excludes)),
            )

    async def ready(self, lease: ComputeLease, bootstrap: MachineBootstrap) -> bool:
        target = await self.target(lease, bootstrap)
        try:
            async with httpx.AsyncClient(timeout=self._health_timeout) as client:
                response = await client.get(target.service_url + "/health")
        except httpx.TransportError:
            return False
        return response.status_code == 200

    async def stop(self, lease: ComputeLease, bootstrap: MachineBootstrap) -> None:
        result = await self._guest_access.execute(
            lease,
            bootstrap,
            self._python(_STOP, str(lease.id), str(self._stop_timeout)),
            expected_exit_codes=(0, 42),
        )
        if result.exit_code == 42:
            # Cancelled before storage restore: retain any previous local archive.
            await self._close_tunnel(str(lease.id))
            return
        await self._archive_data(lease, bootstrap)
        await self._close_tunnel(str(lease.id))

    async def _archive_data(self, lease: ComputeLease, bootstrap: MachineBootstrap) -> None:
        directory = self._data / str(lease.session_id or lease.id)
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
                            _REMOTE_DATA,
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
