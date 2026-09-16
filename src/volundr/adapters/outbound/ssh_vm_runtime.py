"""Run Skuld on a VM through pinned OpenSSH, preserving local-disk session data.

Only the SSH listener is exposed. Skuld and the platform callback forward bind
loopback; private provider credentials never enter the guest. The VM image or
operator cloud-init must install Docker, Python 3.12+, and OpenSSH.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shlex
import socket
import tempfile
from pathlib import Path

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from niuu.ports.session_proxy import SessionProxyTarget
from volundr.adapters.outbound.local_process import LocalProcessPodManager
from volundr.domain.compute import BootstrapFile, ComputeLease, MachineBootstrap
from volundr.domain.models import Session, SessionSpec
from volundr.domain.vm_runtime import VmRuntime, VmRuntimeUnavailableError

# cloud-init's SSH module deletes/recreates keys at /etc/ssh/ssh_host_*.
_HOST_KEY = "/etc/niuu/ssh_host_ed25519_key"
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
run('docker','pull',p['image'])
run('docker','run','--detach','--init','--name',name,'--network','host',
    '--label','compute.niuu.io/allocation='+p['allocation_id'],
    *env_args,
    '--mount','type=bind,src='+str(workspace)+',dst=/workspace',
    '--mount','type=bind,src='+str(home)+',dst=/home/skuld',p['image'],
    env={**os.environ,**p['environment']})
"""

_PREPARE = r"""
import pathlib,sys,tarfile
root=pathlib.Path('/var/lib/niuu/session')
marker=root/'.allocation'
allocation=sys.argv[1]
root.mkdir(parents=True,exist_ok=True)
if marker.exists():
    if marker.read_text() != allocation:
        raise RuntimeError('Workspace allocation mismatch')
    sys.exit(0)
if sys.argv[2]=='restore':
    with tarfile.open(fileobj=sys.stdin.buffer,mode='r|') as archive:
        archive.extractall(root,filter='data')
marker.write_text(allocation)
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


class SshContainerVmRuntime(VmRuntime):
    def __init__(
        self,
        *,
        ssh_private_key_file: str,
        ssh_public_key_file: str,
        data_dir: str,
        skuld_image: str,
        platform_host: str = "127.0.0.1",
        platform_port: int = 8080,
        guest_platform_port: int = 18080,
        ssh_user: str = "ubuntu",
        ssh_port: int = 22,
        broker_port: int = 8081,
        connect_timeout_seconds: int = 10,
        command_timeout_seconds: float = 600,
        stop_timeout_seconds: int = 30,
        poll_interval_seconds: float = 2,
        health_timeout_seconds: float = 5,
    ):
        if not skuld_image or not ssh_user or ssh_user.startswith("-"):
            raise ValueError("Configure a Skuld image and SSH user")
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
        self._private_key = str(Path(ssh_private_key_file).expanduser().resolve(strict=True))
        self._public_key = Path(ssh_public_key_file).expanduser().read_text().strip()
        serialization.load_ssh_public_key(self._public_key.encode())
        self._data = Path(data_dir).expanduser().resolve()
        self._data.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._image = skuld_image
        self._platform_host, self._platform_port = platform_host, platform_port
        self._guest_platform_port = guest_platform_port
        self._ssh_user, self._ssh_port = ssh_user, ssh_port
        self._broker_port = broker_port
        self._connect_timeout = connect_timeout_seconds
        self._command_timeout = command_timeout_seconds
        self._stop_timeout = stop_timeout_seconds
        self._poll = poll_interval_seconds
        self._health_timeout = health_timeout_seconds
        self._tunnels: dict[str, tuple[asyncio.subprocess.Process, int, str]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def bootstrap(
        self, session: Session, spec: SessionSpec, defaults: MachineBootstrap
    ) -> MachineBootstrap:
        pod = spec.pod_spec
        if any(
            (
                pod.volumes,
                pod.volume_mounts,
                pod.service_account,
                pod.init_containers,
                pod.extra_containers,
            )
        ):
            raise ValueError(
                "SSH VM sessions require local-disk storage; "
                "Kubernetes mounts and sidecars are unsupported"
            )
        if session.source.type != "git":
            raise ValueError(
                "SSH VM sessions accept Git or an empty workspace, not host-path mounts"
            )
        if any(spec.values.get(key) for key in ("homeVolume", "persistence", "flock", "ravnFlock")):
            raise ValueError(
                "SSH VM runtime cannot silently replace configured storage or flock topology"
            )
        env = LocalProcessPodManager._session_env(spec, Path("/workspace"))
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
        key = Ed25519PrivateKey.generate()
        private = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.OpenSSH,
            serialization.NoEncryption(),
        ).decode()
        public = (
            key.public_key()
            .public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH)
            .decode()
        )
        payload = json.dumps(
            {
                "image": self._image,
                "environment": env,
                "repo": session.source.repo,
                "branch": session.source.branch,
            }
        )
        files = (
            BootstrapFile(path=_HOST_KEY, content=private),
            BootstrapFile(path=_HOST_KEY + ".pub", content=public, permissions="0644"),
            BootstrapFile(
                path="/etc/ssh/sshd_config.d/10-niuu-host-key.conf",
                content=f"HostKey {_HOST_KEY}\n",
                permissions="0644",
            ),
            BootstrapFile(path=_LAUNCH, content=payload),
        )
        paths = [f.path for f in (*defaults.files, *files)]
        if len(paths) != len(set(paths)):
            raise ValueError("Default bootstrap conflicts with VM runtime files")
        return MachineBootstrap(
            files=(*defaults.files, *files),
            commands=(*defaults.commands, ("systemctl", "restart", "ssh")),
            ssh_authorized_keys=(*defaults.ssh_authorized_keys, self._public_key),
        )

    def _ssh(self, lease: ComputeLease, bootstrap: MachineBootstrap) -> list[str]:
        if not lease.machine or not lease.machine.addresses:
            raise RuntimeError("VM has no observed network address")
        address = lease.machine.addresses[0]
        alias = "niuu-" + str(lease.id)
        public = next(f.content for f in bootstrap.files if f.path == _HOST_KEY + ".pub")
        directory = self._data / str(lease.session_id)
        directory.mkdir(mode=0o700, exist_ok=True)
        known = directory / "known_hosts"
        known.write_text(f"{alias} {public}\n")
        return [
            "ssh",
            "-F",
            "/dev/null",
            "-i",
            self._private_key,
            "-p",
            str(self._ssh_port),
            "-o",
            "BatchMode=yes",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"UserKnownHostsFile={known}",
            "-o",
            f"HostKeyAlias={alias}",
            "-o",
            f"ConnectTimeout={self._connect_timeout}",
            "-o",
            "ServerAliveInterval=15",
            "-o",
            "ServerAliveCountMax=3",
            "-o",
            "ExitOnForwardFailure=yes",
            f"{self._ssh_user}@{address}",
        ]

    @staticmethod
    def _python(program: str, *args: str) -> str:
        encoded = base64.b64encode(program.encode()).decode()
        code = f"import base64;exec(base64.b64decode({encoded!r}))"
        return shlex.join(["sudo", "-n", "python3", "-c", code, *args])

    async def _run(
        self,
        argv: list[str],
        *,
        data: bytes | None = None,
        stdin=None,
        stdout=None,
        expected_exit_codes: tuple[int, ...] = (0,),
    ) -> int:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=stdin if stdin is not None else asyncio.subprocess.PIPE,
            stdout=stdout if stdout is not None else asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            async with asyncio.timeout(self._command_timeout):
                await process.communicate(data)
        except BaseException:
            if process.returncode is None:
                process.kill()
            await process.wait()
            raise
        if process.returncode not in expected_exit_codes:
            # Remote stderr can contain session credentials or source-control URLs.
            if process.returncode == 255:
                raise VmRuntimeUnavailableError(
                    "VM SSH connection unavailable; check readiness and pinned identity"
                )
            raise RuntimeError(
                f"VM SSH command failed with exit code {process.returncode}; inspect the guest"
            )
        return process.returncode

    async def target(self, lease: ComputeLease, bootstrap: MachineBootstrap) -> SessionProxyTarget:
        key = str(lease.id)
        async with self._locks.setdefault(key, asyncio.Lock()):
            current = self._tunnels.get(key)
            address = (
                lease.machine.addresses[0] if lease.machine and lease.machine.addresses else ""
            )
            if current is not None and current[0].returncode is None and current[2] == address:
                return SessionProxyTarget(
                    service_url=f"http://127.0.0.1:{current[1]}",
                    connect_host="127.0.0.1",
                    connect_port=current[1],
                )
            if current is not None:
                await self._close_tunnel(key)
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            argv = self._ssh(lease, bootstrap)
            options = [
                "-N",
                "-L",
                f"127.0.0.1:{port}:127.0.0.1:{self._broker_port}",
                "-R",
                f"127.0.0.1:{self._guest_platform_port}:{self._platform_host}:{self._platform_port}",
            ]
            process = await asyncio.create_subprocess_exec(
                *argv[:-1],
                *options,
                argv[-1],
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            self._tunnels[key] = (process, port, address)
            return SessionProxyTarget(
                service_url=f"http://127.0.0.1:{port}", connect_host="127.0.0.1", connect_port=port
            )

    async def prepare(self, lease: ComputeLease, bootstrap: MachineBootstrap) -> None:
        ssh = self._ssh(lease, bootstrap)
        # cloud-init finishes before Docker and the pinned host key are used.
        await self._run([*ssh, "sudo -n cloud-init status --wait"])

    async def start(self, lease: ComputeLease, bootstrap: MachineBootstrap) -> None:
        ssh = self._ssh(lease, bootstrap)
        archive = self._data / str(lease.session_id) / "session.tar"
        if archive.exists():
            with archive.open("rb") as src:
                await self._run([*ssh, self._python(_PREPARE, str(lease.id), "restore")], stdin=src)
        else:
            await self._run([*ssh, self._python(_PREPARE, str(lease.id), "empty")])
        await self.target(lease, bootstrap)
        payload = json.loads(next(f.content for f in bootstrap.files if f.path == _LAUNCH))
        payload["allocation_id"] = str(lease.id)
        await self._run([*ssh, self._python(_START)], data=json.dumps(payload).encode())

    async def ready(self, lease: ComputeLease, bootstrap: MachineBootstrap) -> bool:
        target = await self.target(lease, bootstrap)
        try:
            async with httpx.AsyncClient(timeout=self._health_timeout) as client:
                response = await client.get(target.service_url + "/health")
        except httpx.TransportError:
            return False
        return response.status_code == 200

    async def stop(self, lease: ComputeLease, bootstrap: MachineBootstrap) -> None:
        ssh = self._ssh(lease, bootstrap)
        result = await self._run(
            [*ssh, self._python(_STOP, str(lease.id), str(self._stop_timeout))],
            expected_exit_codes=(0, 42),
        )
        if result == 42:
            # Cancelled before storage restore: retain any previous local archive.
            await self._close_tunnel(str(lease.id))
            return
        directory = self._data / str(lease.session_id)
        fd, temporary = tempfile.mkstemp(prefix="session-", suffix=".tar", dir=directory)
        try:
            with os.fdopen(fd, "wb") as dest:
                await self._run(
                    [
                        *ssh,
                        shlex.join(
                            [
                                "sudo",
                                "-n",
                                "tar",
                                "--one-file-system",
                                "-C",
                                _REMOTE_DATA,
                                "-cf",
                                "-",
                                ".",
                            ]
                        ),
                    ],
                    stdout=dest,
                )
                dest.flush()
                os.fsync(dest.fileno())
            os.replace(temporary, directory / "session.tar")
        finally:
            Path(temporary).unlink(missing_ok=True)
        await self._close_tunnel(str(lease.id))

    async def _close_tunnel(self, key: str) -> None:
        current = self._tunnels.pop(key, None)
        if current is None or current[0].returncode is not None:
            return
        current[0].terminate()
        await current[0].wait()

    async def close(self) -> None:
        for key in list(self._tunnels):
            await self._close_tunnel(key)
