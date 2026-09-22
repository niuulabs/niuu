"""Pinned OpenSSH implementation of guest access used by VM adapters."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import pwd
import re
import shlex
import socket
import sys
from pathlib import Path
from typing import BinaryIO

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from niuu.ports.session_proxy import SessionProxyTarget
from volundr.domain.compute import BootstrapFile, ComputeLease, MachineBootstrap
from volundr.domain.guest_access import GuestAccess, GuestCommandResult
from volundr.domain.vm_runtime import VmRuntimeStageError, VmRuntimeUnavailableError

HOST_KEY_PATH = "/etc/niuu/ssh_host_ed25519_key"
HOST_KEY_CONFIG_PATH = "/etc/ssh/sshd_config.d/10-niuu-host-key.conf"

_INSTALL_BOOTSTRAP = r"""
import json, os, pathlib, pwd, subprocess, sys
p=json.load(sys.stdin)
marker=pathlib.Path('/var/lib/niuu/bootstrap.sha256')
if marker.exists():
    if marker.read_text() != p['digest']:
        raise RuntimeError('Machine bootstrap fingerprint mismatch')
    sys.exit(0)
state_path=pathlib.Path('/var/lib/niuu/bootstrap-state.json')
if state_path.exists():
    state=json.loads(state_path.read_text())
    if state.get('digest') != p['digest']:
        raise RuntimeError('Machine bootstrap fingerprint mismatch')
    next_command=state.get('next_command')
    if not isinstance(next_command,int) or not 0 <= next_command <= len(p['commands']):
        raise RuntimeError('Invalid machine bootstrap progress')
else:
    state={'digest':p['digest'],'next_command':0}
    next_command=0
    state_path.parent.mkdir(parents=True,exist_ok=True)
    temporary=state_path.with_name(state_path.name+'.niuu-tmp')
    temporary.write_text(json.dumps(state))
    os.chmod(temporary,0o600)
    os.replace(temporary,state_path)
for item in p['files']:
    path=pathlib.Path(item['path'])
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.niuu-tmp')
    temporary.write_text(item['content'])
    os.chmod(temporary,int(item['permissions'],8))
    os.replace(temporary,path)
if p['ssh_authorized_keys']:
    account=pwd.getpwnam(p['ssh_user'])
    directory=pathlib.Path(account.pw_dir)/'.ssh'
    directory.mkdir(mode=0o700,parents=True,exist_ok=True)
    os.chmod(directory,0o700)
    authorized=directory/'authorized_keys'
    existing=authorized.read_text().splitlines() if authorized.exists() else []
    additions=[key for key in p['ssh_authorized_keys'] if key not in existing]
    if additions:
        temporary=authorized.with_name('authorized_keys.niuu-tmp')
        temporary.write_text('\n'.join([*existing,*additions])+'\n')
        os.chmod(temporary,0o600)
        os.chown(temporary,account.pw_uid,account.pw_gid)
        os.replace(temporary,authorized)
    if authorized.exists():
        os.chmod(authorized,0o600)
        os.chown(authorized,account.pw_uid,account.pw_gid)
    os.chown(directory,account.pw_uid,account.pw_gid)
for index,command in enumerate(p['commands'][next_command:],start=next_command):
    subprocess.run(command,check=True,stdout=subprocess.DEVNULL)
    state['next_command']=index+1
    temporary=state_path.with_name(state_path.name+'.niuu-tmp')
    temporary.write_text(json.dumps(state))
    os.chmod(temporary,0o600)
    os.replace(temporary,state_path)
marker.parent.mkdir(parents=True,exist_ok=True)
temporary=marker.with_name(marker.name+'.niuu-tmp')
temporary.write_text(p['digest'])
os.chmod(temporary,0o600)
os.replace(temporary,marker)
state_path.unlink(missing_ok=True)
"""


class SshGuestAccess(GuestAccess):
    """OpenSSH control with allocation-scoped host identity pins."""

    def __init__(
        self,
        *,
        ssh_private_key_file: str,
        ssh_public_key_file: str,
        data_dir: str,
        ssh_user: str = "ubuntu",
        ssh_port: int = 22,
        connect_timeout_seconds: int = 10,
        command_timeout_seconds: float = 600,
        poll_interval_seconds: float = 2,
        bootstrap_delivery: str = "provider",
        machine_commands: list[list[str]] | tuple[tuple[str, ...], ...] = (),
    ) -> None:
        if not ssh_user or ssh_user.startswith("-"):
            raise ValueError("Configure a valid SSH user")
        if not 1 <= ssh_port <= 65535:
            raise ValueError("SSH port must be between 1 and 65535")
        if min(connect_timeout_seconds, command_timeout_seconds, poll_interval_seconds) <= 0:
            raise ValueError("SSH timeouts must be positive")
        if bootstrap_delivery not in {"provider", "ssh"}:
            raise ValueError("bootstrap_delivery must be 'provider' or 'ssh'")
        if any(
            not command or any(not str(argument) for argument in command)
            for command in machine_commands
        ):
            raise ValueError("VM machine commands must contain non-empty arguments")

        self._private_key = str(Path(ssh_private_key_file).expanduser().resolve(strict=True))
        self._public_key = Path(ssh_public_key_file).expanduser().read_text().strip()
        serialization.load_ssh_public_key(self._public_key.encode())
        self._data = Path(data_dir).expanduser().resolve()
        self._data.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._ssh_user = ssh_user
        self._ssh_port = ssh_port
        self._connect_timeout = connect_timeout_seconds
        self._command_timeout = command_timeout_seconds
        self._poll = poll_interval_seconds
        self._bootstrap_delivery = bootstrap_delivery
        self._machine_commands = tuple(
            tuple(str(argument) for argument in command) for command in machine_commands
        )
        self._subprocess_env = self._ssh_subprocess_environment()
        self._tunnels: dict[str, tuple[asyncio.subprocess.Process, int, str]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    @property
    def principal(self) -> str:
        return self._ssh_user

    def configure_trust(self, *, principal: str, mode: str, kwargs: dict) -> None:
        if principal != self._ssh_user:
            raise ValueError(
                f"SSH access principal {principal!r} does not match configured user "
                f"{self._ssh_user!r}"
            )
        if kwargs:
            raise ValueError("SSH trust configuration contains unsupported parameters")
        deliveries = {"provider_identity": "provider", "tofu": "ssh"}
        if mode == "ssh_ca":
            raise ValueError("SSH CA trust is not implemented by SshGuestAccess")
        try:
            delivery = deliveries[mode]
        except KeyError as exc:
            raise ValueError(f"Unsupported SSH trust mode {mode!r}") from exc
        if delivery != self._bootstrap_delivery:
            raise ValueError(
                f"SSH trust mode {mode!r} conflicts with bootstrap_delivery "
                f"{self._bootstrap_delivery!r}"
            )

    def validate_host_preparation(self) -> None:
        if self._machine_commands:
            raise ValueError("File-backed host preparation cannot execute legacy machine_commands")

    @property
    def private_key(self) -> str:
        """Resolved key path retained for legacy runtime diagnostics and tests."""
        return self._private_key

    @property
    def public_key(self) -> str:
        return self._public_key

    @property
    def data_dir(self) -> Path:
        return self._data

    @property
    def subprocess_env(self) -> dict[str, str] | None:
        return self._subprocess_env

    def _ssh_subprocess_environment(self) -> dict[str, str] | None:
        try:
            pwd.getpwuid(os.getuid())
            return None
        except KeyError:
            pass

        libraries = sorted(Path("/usr/lib").glob("*/libnss_wrapper.so"))
        if not libraries:
            raise RuntimeError(
                "VM SSH runtime needs libnss-wrapper when the container UID has no passwd entry"
            )
        uid, gid = os.getuid(), os.getgid()
        passwd_file = self._data / ".nss-passwd"
        group_file = self._data / ".nss-group"
        identity_files = {
            passwd_file: (
                f"niuu-runtime:x:{uid}:{gid}:Niuu runtime:{self._data}:/usr/sbin/nologin\n"
            ),
            group_file: f"niuu-runtime:x:{gid}:\n",
        }
        for path, content in identity_files.items():
            temporary = path.with_name(path.name + ".niuu-tmp")
            temporary.write_text(content)
            temporary.chmod(0o600)
            os.replace(temporary, path)
        preload = str(libraries[0])
        if existing := os.environ.get("LD_PRELOAD", "").strip():
            preload += f":{existing}"
        return {
            **os.environ,
            "LD_PRELOAD": preload,
            "NSS_WRAPPER_PASSWD": str(passwd_file),
            "NSS_WRAPPER_GROUP": str(group_file),
        }

    def machine_bootstrap(self, defaults: MachineBootstrap) -> MachineBootstrap:
        reserved = {HOST_KEY_PATH, HOST_KEY_PATH + ".pub", HOST_KEY_CONFIG_PATH}
        if any(item.path in reserved for item in defaults.files):
            raise ValueError("Default bootstrap conflicts with SSH access files")
        defaults = defaults.model_copy(
            update={"commands": (*defaults.commands, *self._machine_commands)}
        )
        if self._bootstrap_delivery == "ssh":
            return defaults.model_copy(
                update={"ssh_authorized_keys": (*defaults.ssh_authorized_keys, self._public_key)},
                deep=True,
            )

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
        files = (
            BootstrapFile(path=HOST_KEY_PATH, content=private),
            BootstrapFile(path=HOST_KEY_PATH + ".pub", content=public, permissions="0644"),
            BootstrapFile(
                path=HOST_KEY_CONFIG_PATH,
                content=f"HostKey {HOST_KEY_PATH}\n",
                permissions="0644",
            ),
        )
        return MachineBootstrap(
            files=(*defaults.files, *files),
            commands=(*defaults.commands, ("systemctl", "restart", "ssh")),
            ssh_authorized_keys=(*defaults.ssh_authorized_keys, self._public_key),
        )

    def command_argv(self, lease: ComputeLease, bootstrap: MachineBootstrap) -> list[str]:
        if not lease.machine or not lease.machine.addresses:
            raise RuntimeError("VM has no observed network address")
        alias = self._host_alias(lease)
        directory = self._allocation_directory(lease)
        directory.mkdir(mode=0o700, exist_ok=True, parents=True)
        known = directory / "known_hosts"
        if self._bootstrap_delivery == "provider":
            public = next(
                item.content for item in bootstrap.files if item.path == HOST_KEY_PATH + ".pub"
            )
            known.write_text(f"{alias} {public}\n")
        elif not self._has_host_pin(lease):
            raise VmRuntimeUnavailableError(
                "VM host identity is not pinned; complete SSH bootstrap first"
            )
        return self._base_ssh(lease, strict="yes")

    def bootstrap_argv(self, lease: ComputeLease) -> list[str]:
        return self._base_ssh(lease, strict="accept-new")

    def _base_ssh(self, lease: ComputeLease, *, strict: str) -> list[str]:
        if not lease.machine or not lease.machine.addresses:
            raise RuntimeError("VM has no observed network address")
        directory = self._allocation_directory(lease)
        directory.mkdir(mode=0o700, exist_ok=True, parents=True)
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
            f"StrictHostKeyChecking={strict}",
            "-o",
            f"UserKnownHostsFile={directory / 'known_hosts'}",
            "-o",
            f"HostKeyAlias={self._host_alias(lease)}",
            "-o",
            f"ConnectTimeout={self._connect_timeout}",
            "-o",
            "ServerAliveInterval=15",
            "-o",
            "ServerAliveCountMax=3",
            "-o",
            "ExitOnForwardFailure=yes",
            f"{self._ssh_user}@{lease.machine.addresses[0]}",
        ]

    async def ensure(self, lease: ComputeLease, bootstrap: MachineBootstrap) -> None:
        if self._bootstrap_delivery == "provider":
            await self.execute(lease, bootstrap, "sudo -n cloud-init status --wait")
            return
        await self._deliver_bootstrap_over_ssh(lease, bootstrap)

    async def _deliver_bootstrap_over_ssh(
        self, lease: ComputeLease, bootstrap: MachineBootstrap
    ) -> None:
        serialized = bootstrap.model_dump(mode="json")
        payload = {
            **serialized,
            "ssh_user": self._ssh_user,
            "digest": hashlib.sha256(bootstrap.model_dump_json().encode()).hexdigest(),
        }
        command = self.python_command(_INSTALL_BOOTSTRAP)
        data = json.dumps(payload).encode()
        verified = self.bootstrap_verified_marker(lease)
        has_pin = self._has_host_pin(lease)
        if verified.exists() and not has_pin:
            raise RuntimeError("Verified VM bootstrap is missing its pinned host identity")
        argv = self.command_argv(lease, bootstrap) if has_pin else self.bootstrap_argv(lease)
        await self.run_argv([*argv, command], data=data)
        await self.run_argv([*self.command_argv(lease, bootstrap), "true"])
        temporary = verified.with_name(verified.name + ".niuu-tmp")
        temporary.write_text(payload["digest"])
        temporary.chmod(0o600)
        os.replace(temporary, verified)

    async def execute(
        self,
        lease: ComputeLease,
        bootstrap: MachineBootstrap,
        command: str,
        *,
        data: bytes | None = None,
        stdin: BinaryIO | None = None,
        stdout: BinaryIO | None = None,
        capture_stdout: bool = False,
        timeout_seconds: float | None = None,
        expected_exit_codes: tuple[int, ...] = (0,),
        safe_error_prefix: str | None = None,
        safe_detail_prefix: str | None = None,
    ) -> GuestCommandResult:
        return await self.run_argv(
            [*self.command_argv(lease, bootstrap), command],
            data=data,
            stdin=stdin,
            stdout=stdout,
            capture_stdout=capture_stdout,
            timeout_seconds=timeout_seconds,
            expected_exit_codes=expected_exit_codes,
            safe_error_prefix=safe_error_prefix,
            safe_detail_prefix=safe_detail_prefix,
        )

    async def run_argv(
        self,
        argv: list[str],
        *,
        data: bytes | None = None,
        stdin: BinaryIO | None = None,
        stdout: BinaryIO | None = None,
        capture_stdout: bool = False,
        timeout_seconds: float | None = None,
        expected_exit_codes: tuple[int, ...] = (0,),
        safe_error_prefix: str | None = None,
        safe_detail_prefix: str | None = None,
    ) -> GuestCommandResult:
        if stdout is not None and capture_stdout:
            raise ValueError("stdout and capture_stdout are mutually exclusive")
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=stdin if stdin is not None else asyncio.subprocess.PIPE,
            stdout=(
                stdout
                if stdout is not None
                else asyncio.subprocess.PIPE
                if capture_stdout
                else asyncio.subprocess.DEVNULL
            ),
            stderr=asyncio.subprocess.PIPE,
            env=self._subprocess_env,
        )
        try:
            async with asyncio.timeout(timeout_seconds or self._command_timeout):
                captured, stderr = await process.communicate(data)
        except BaseException:
            if process.returncode is None:
                process.kill()
            await process.wait()
            raise
        if process.returncode not in expected_exit_codes:
            self._raise_command_error(
                process.returncode,
                stderr,
                safe_error_prefix=safe_error_prefix,
                safe_detail_prefix=safe_detail_prefix,
            )
        return GuestCommandResult(process.returncode, captured or b"")

    @staticmethod
    def _raise_command_error(
        returncode: int,
        stderr: bytes,
        *,
        safe_error_prefix: str | None,
        safe_detail_prefix: str | None,
    ) -> None:
        if returncode == 255:
            raise VmRuntimeUnavailableError(
                "VM SSH connection unavailable; check readiness and pinned identity"
            )
        detail = None
        diagnostic = ""
        lines = stderr.decode(errors="replace").splitlines() if stderr else []
        if safe_error_prefix:
            for line in lines:
                if not line.startswith(safe_error_prefix):
                    continue
                candidate = line.removeprefix(safe_error_prefix).strip()
                if re.fullmatch(r"[A-Za-z0-9._-]{1,64}", candidate):
                    detail = candidate
                    break
        if safe_detail_prefix:
            for line in lines:
                if not line.startswith(safe_detail_prefix):
                    continue
                encoded = line.removeprefix(safe_detail_prefix).strip()
                if not re.fullmatch(r"[A-Za-z0-9+/=]{1,2048}", encoded):
                    continue
                try:
                    candidate = base64.b64decode(encoded, validate=True).decode()
                except (ValueError, UnicodeDecodeError):
                    continue
                if re.fullmatch(r"[\x20-\x7e]{1,800}", candidate):
                    diagnostic = candidate
                    break
        if detail:
            raise VmRuntimeStageError(detail, diagnostic)
        raise RuntimeError(f"VM SSH command failed with exit code {returncode}; inspect the guest")

    async def open_tunnel(
        self,
        lease: ComputeLease,
        bootstrap: MachineBootstrap,
        *,
        remote_port: int,
        reverse_bind_host: str,
        reverse_port: int,
        controller_host: str,
        controller_port: int,
    ) -> SessionProxyTarget:
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
                await self.close_tunnel(key)
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            argv = self.command_argv(lease, bootstrap)
            options = [
                "-N",
                "-L",
                f"127.0.0.1:{port}:127.0.0.1:{remote_port}",
                "-R",
                f"{reverse_bind_host}:{reverse_port}:{controller_host}:{controller_port}",
            ]
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "volundr.adapters.outbound.ssh_tunnel",
                str(self._poll),
                str(self._connect_timeout),
                *argv[:-1],
                *options,
                argv[-1],
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                env=self._subprocess_env,
            )
            self._tunnels[key] = (process, port, address)
            return SessionProxyTarget(
                service_url=f"http://127.0.0.1:{port}",
                connect_host="127.0.0.1",
                connect_port=port,
            )

    async def close_tunnel(self, lease_id: str) -> None:
        current = self._tunnels.pop(lease_id, None)
        if current is None:
            return
        process = current[0]
        if process.returncode is None:
            process.terminate()
            await process.wait()

    async def close(self) -> None:
        for key in list(self._tunnels):
            await self.close_tunnel(key)

    def bootstrap_verified_marker(self, lease: ComputeLease) -> Path:
        directory = self._allocation_directory(lease)
        directory.mkdir(mode=0o700, exist_ok=True, parents=True)
        return directory / "bootstrap.verified"

    def _allocation_directory(self, lease: ComputeLease) -> Path:
        return self._data / str(lease.id)

    def _has_host_pin(self, lease: ComputeLease) -> bool:
        known = self._allocation_directory(lease) / "known_hosts"
        return known.is_file() and bool(known.read_text().strip())

    @staticmethod
    def _host_alias(lease: ComputeLease) -> str:
        return "niuu-" + str(lease.id)

    @staticmethod
    def python_command(program: str, *args: str, principal: str = "root") -> str:
        encoded = base64.b64encode(program.encode()).decode()
        code = f"import base64;exec(base64.b64decode({encoded!r}))"
        command = ["python3", "-c", code, *args]
        if principal == "root":
            command[:0] = ["sudo", "-n"]
        return shlex.join(command)
