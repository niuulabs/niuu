"""OpenShell's Docker gateway on provider-managed, single-session Linux guests.

The provider delivers machine bootstrap. The existing VM lease/pool controller
owns admission and deletion; this adapter owns guest installation, session
startup, and local workspace preservation. Docker, Python and OpenSSH must be
provided by the machine image or the operator's bootstrap commands.
"""

from __future__ import annotations

import asyncio
import json
import shlex
import tomllib
from copy import deepcopy
from dataclasses import asdict

import grpc
import httpx

from volundr.adapters.outbound.openshell_gateway import OpenShellGatewayPodManager
from volundr.adapters.outbound.ssh_vm_runtime import (
    _LAUNCH,
    _SESSION_FILES,
    SshContainerVmRuntime,
)
from volundr.domain.compute import BootstrapFile, MachineBootstrap
from volundr.domain.models import PodSpecAdditions, Session, SessionSpec
from volundr.domain.vm_runtime import VmRuntimeUnavailableError

_SPEC = "/etc/niuu/openshell-session.json"
_CONFIG = "/etc/openshell/gateway.toml"
_UNIT = """[Unit]
Description=Niuu OpenShell Docker gateway
Requires=docker.service
After=docker.service network-online.target
[Service]
User=root
ExecStart=/usr/local/bin/openshell-gateway \\
  --config /etc/openshell/gateway.toml \\
  --db-url sqlite:/var/lib/openshell/gateway.db
WorkingDirectory=/var/lib/openshell
Restart=on-failure
RestartSec=5
[Install]
WantedBy=multi-user.target
"""

_STORAGE = r"""
import pathlib,subprocess,sys
root=pathlib.Path('/var/lib/niuu/session')
for name in ('workspace','home'):
    (root/name).mkdir(exist_ok=True)
identity=subprocess.check_output(
    ['docker','run','--rm','--network','none','--user',sys.argv[2],
     '--entrypoint','/bin/sh',sys.argv[1],'-c','id -u; id -g'],text=True).split()
if len(identity)!=2 or not all(value.isdecimal() for value in identity):
    raise RuntimeError('Could not resolve sandbox filesystem ownership')
subprocess.run(['chown','-R',':'.join(identity),str(root)],check=True)
"""
_CHECK_STORAGE = r"""
import pathlib,sys
marker=pathlib.Path('/var/lib/niuu/session/.allocation')
if not marker.exists(): sys.exit(42)
if marker.read_text()!=sys.argv[1]: raise RuntimeError('Workspace allocation mismatch')
"""


class OpenShellVmRuntime(SshContainerVmRuntime):
    def __init__(
        self,
        *,
        gateway_image: str,
        gateway_config: str,
        gateway_kwargs: dict,
        network_subnet: str,
        gateway_client_secret: str = "",
        **kwargs,
    ):
        config = tomllib.loads(gateway_config)["openshell"]
        gateway = config["gateway"]
        docker = config["drivers"]["docker"]
        if gateway.get("compute_driver") != "docker" or not docker.get("enable_bind_mounts"):
            raise ValueError("OpenShell VM gateway requires Docker with enable_bind_mounts=true")
        if gateway.get("auth", {}).get("allow_unauthenticated_users", False):
            raise ValueError("OpenShell VM gateway must require authentication")
        if not gateway.get("oidc"):
            raise ValueError("Configure OpenShell gateway OIDC authentication")
        if not gateway.get("disable_tls"):
            raise ValueError("OpenShell VM gateway uses plaintext inside pinned SSH transport")
        self._gateway_host, port = gateway["bind_address"].rsplit(":", 1)
        self._gateway_port = int(port)
        if self._gateway_host != docker["host_gateway_ip"]:
            raise ValueError("Gateway bind address must be the configured Docker bridge address")
        if not gateway_image or not docker.get("supervisor_image"):
            raise ValueError("Configure matching OpenShell gateway and supervisor images")
        forbidden = {
            "gateway_endpoint",
            "driver_config",
            "sandbox_workspace",
            "sandbox_home",
            "sandbox_image",
            "compute_driver",
        }
        if forbidden & gateway_kwargs.keys():
            raise ValueError("VM runtime owns gateway endpoint, storage mounts and sandbox paths")
        self._network_subnet = network_subnet
        self._gateway_image = gateway_image
        self._gateway_config = gateway_config
        self._gateway_kwargs = deepcopy(gateway_kwargs)
        if gateway_client_secret:
            self._gateway_kwargs["client_secret"] = gateway_client_secret
        self._docker = docker
        self._managers: dict[str, tuple[str, OpenShellGatewayPodManager]] = {}
        # Codex recreates its executable symlinks; they refer to image paths,
        # not durable session data and cannot be safely extracted on the host.
        kwargs["archive_excludes"] = list(
            dict.fromkeys(["workspace/.codex/tmp", *(kwargs.get("archive_excludes") or [])])
        )
        super().__init__(**kwargs)

    def machine_bootstrap(self, defaults: MachineBootstrap) -> MachineBootstrap:
        machine = super().machine_bootstrap(defaults)
        # No user/model credentials enter an unbound warm guest. The gateway's
        # signing key is generated locally and stays on this disposable guest.
        install = f"""set -eu
install -d -m 700 /etc/openshell /var/lib/openshell/jwt
docker pull {shlex.quote(self._gateway_image)}
cid=$(docker create {shlex.quote(self._gateway_image)})
trap 'docker rm "$cid" >/dev/null' EXIT
docker cp "$cid:/usr/local/bin/openshell-gateway" /usr/local/bin/openshell-gateway
chmod 755 /usr/local/bin/openshell-gateway
docker network inspect {shlex.quote(self._docker["network_name"])} >/dev/null 2>&1 || \\
  docker network create --subnet {shlex.quote(self._network_subnet)} \\
  --gateway {shlex.quote(self._gateway_host)} {shlex.quote(self._docker["network_name"])}
if [ ! -s /var/lib/openshell/jwt/signing.pem ]; then
  openssl genpkey -algorithm Ed25519 -out /var/lib/openshell/jwt/signing.pem
  chmod 600 /var/lib/openshell/jwt/signing.pem
  openssl pkey -in /var/lib/openshell/jwt/signing.pem -pubout -out /var/lib/openshell/jwt/public.pem
  openssl rand -hex 16 > /var/lib/openshell/jwt/kid
fi
openshell-gateway config preflight --path {_CONFIG}
systemctl daemon-reload
systemctl enable --now openshell-gateway
"""
        files = (
            BootstrapFile(path=_CONFIG, content=self._gateway_config),
            BootstrapFile(path="/etc/systemd/system/openshell-gateway.service", content=_UNIT),
            BootstrapFile(
                path="/etc/ssh/sshd_config.d/20-niuu-callback.conf",
                content="GatewayPorts clientspecified\n",
            ),
        )
        if {f.path for f in files} & {f.path for f in machine.files}:
            raise ValueError("Default bootstrap conflicts with OpenShell runtime files")
        return machine.model_copy(
            update={
                "files": (*machine.files, *files),
                "commands": (
                    *machine.commands,
                    ("sh", "-c", install),
                    ("systemctl", "restart", "ssh"),
                ),
            }
        )

    def session_bootstrap(self, session, spec, machine):
        # Apply the same supported-volume/secret contract as direct Docker VMs.
        # Persist source contents now; never depend on a later controller file.
        if spec.values.get("credentialMappings") or self._gateway_kwargs.get("volundr_api_url"):
            raise ValueError(
                "VM sessions use brokered credentials and injected secret files; "
                "OpenShell SPIFFE provider grants require a shared SPIFFE deployment"
            )
        checked = super().session_bootstrap(session, spec, machine)
        payload = {
            "session": session.model_dump(mode="json"),
            "values": spec.values,
            "pod_spec": asdict(spec.pod_spec),
        }
        return checked.model_copy(
            update={
                "files": (*checked.files, BootstrapFile(path=_SPEC, content=json.dumps(payload)))
            }
        )

    @staticmethod
    def _session(bootstrap):
        payload = json.loads(next(f.content for f in bootstrap.files if f.path == _SPEC))
        session = Session.model_validate(payload["session"])
        spec = SessionSpec(
            values=payload["values"], pod_spec=PodSpecAdditions(**payload["pod_spec"])
        )
        # VmPodManager stores an allocation UUID in pod_name, not an OpenShell name.
        session.pod_name = None
        session.pod_name = OpenShellGatewayPodManager._sandbox_name(session)
        return session, spec

    def _tunnel_options(self, port):
        return [
            "-N",
            "-L",
            f"127.0.0.1:{port}:{self._gateway_host}:{self._gateway_port}",
            "-R",
            f"{self._gateway_host}:{self._guest_platform_port}:{self._platform_host}:{self._platform_port}",
        ]

    async def _manager(self, lease, bootstrap):
        target = await super().target(lease, bootstrap)
        key = str(lease.id)
        endpoint = f"{target.connect_host}:{target.connect_port}"
        identity = f"{endpoint}/{lease.session_id}"
        previous = self._managers.get(key)
        if previous and previous[0] == identity:
            await self._gateway_ready(previous[1])
            return previous[1]
        if previous:
            await previous[1].close()
        mounts = [
            {
                "type": "bind",
                "source": f"/var/lib/niuu/session/{name}",
                "target": f"/sandbox/{name}",
                "read_only": False,
            }
            for name in ("workspace", "home")
        ]
        if lease.session_id is not None:
            launch = json.loads(next(f.content for f in bootstrap.files if f.path == _LAUNCH))
            mounts.extend(
                {"type": "bind", "source": m["source"], "target": m["target"], "read_only": True}
                for m in launch["secret_mounts"]
            )
        options = deepcopy(self._gateway_kwargs)
        options.setdefault("codex_auth_adapter", self._codex_auth_adapter)
        options.setdefault("codex_auth_kwargs", self._codex_auth_kwargs)
        manager = OpenShellGatewayPodManager(
            **options,
            compute_driver="docker",
            gateway_endpoint=endpoint,
            sandbox_image=self._image,
            sandbox_workspace="/sandbox/workspace",
            sandbox_home="/sandbox/home",
            driver_config={"mounts": mounts},
        )
        self._managers[key] = identity, manager
        await self._gateway_ready(manager)
        return manager

    async def _gateway_ready(self, manager):
        try:
            await asyncio.to_thread(manager._client.wait_for_ready, self._connect_timeout)
        except grpc.FutureTimeoutError as exc:
            raise VmRuntimeUnavailableError("OpenShell gateway tunnel is not ready") from exc

    async def warm(self, lease, bootstrap):
        await super().warm(lease, bootstrap)
        try:
            manager = await self._manager(lease, bootstrap)
            # A real authenticated RPC, not just a listening TCP socket.
            await asyncio.to_thread(manager._client.get_sandbox, "niuu-readiness-check")
        except grpc.RpcError as exc:
            if exc.code() == grpc.StatusCode.UNAVAILABLE:
                raise VmRuntimeUnavailableError("OpenShell gateway is still starting") from exc
            raise
        finally:
            # Idle expiry can delete this guest without another runtime stop.
            # A standby probe must not retain channels or reverse listeners.
            current = self._managers.pop(str(lease.id), None)
            if current:
                await current[1].close()
            await self._close_tunnel(str(lease.id))

    async def start(self, lease, bootstrap):
        if lease.session_id is None:
            raise ValueError("Cannot start an unbound OpenShell guest")
        files = [
            f.model_dump()
            for f in bootstrap.files
            if f.path.startswith("/etc/niuu/session-secrets/")
        ]
        await self._run(
            [*self._ssh(lease, bootstrap), self._python(_SESSION_FILES)],
            data=json.dumps(files).encode(),
        )
        await self._restore_data(lease, bootstrap)
        process = self._gateway_kwargs["sandbox_policy"]["process"]
        user = process["run_as_user"] + ":" + process["run_as_group"]
        await self._run([*self._ssh(lease, bootstrap), self._python(_STORAGE, self._image, user)])
        session, spec = self._session(bootstrap)
        spec.values.setdefault("volundr", {})["apiUrl"] = (
            f"http://{self._gateway_host}:{self._guest_platform_port}"
        )
        spec.values.setdefault("env", {}).update(
            {
                "HOME": "/sandbox/home",
                "CODEX_HOME": "/sandbox/workspace/.codex",
                "CLAUDE_CONFIG_DIR": "/sandbox/home/.claude",
            }
        )
        manager = await self._manager(lease, bootstrap)
        try:
            await manager.start(session, spec)
        except grpc.RpcError as exc:
            if exc.code() == grpc.StatusCode.UNAVAILABLE:
                raise VmRuntimeUnavailableError("OpenShell gateway is still starting") from exc
            raise

    async def target(self, lease, bootstrap):
        session, _ = self._session(bootstrap)
        manager = await self._manager(lease, bootstrap)
        sandbox = await asyncio.to_thread(
            manager._client.get_sandbox, manager._sandbox_name(session)
        )
        if sandbox is None or not sandbox.ready:
            raise VmRuntimeUnavailableError("OpenShell sandbox is not ready")
        return await asyncio.to_thread(
            manager._tcp_proxy_target, str(session.id), sandbox.id, manager._service_port
        )

    async def ready(self, lease, bootstrap):
        try:
            target = await self.target(lease, bootstrap)
            async with httpx.AsyncClient(timeout=self._health_timeout) as client:
                response = await client.get(
                    f"http://{target.connect_host}:{target.connect_port}/health"
                )
        except (httpx.TransportError, VmRuntimeUnavailableError):
            return False
        return response.status_code == 200

    async def stop(self, lease, bootstrap):
        result = await self._run(
            [*self._ssh(lease, bootstrap), self._python(_CHECK_STORAGE, str(lease.id))],
            expected_exit_codes=(0, 42),
        )
        if result == 0:
            session, _ = self._session(bootstrap)
            manager = await self._manager(lease, bootstrap)
            await manager.stop(session)
            # Do not release the VM until a complete, fsynced archive exists.
            await self._archive_data(lease, bootstrap)
        manager = self._managers.pop(str(lease.id), None)
        if manager:
            await manager[1].close()
        await self._close_tunnel(str(lease.id))

    @property
    def supports_reuse(self):
        return True

    async def recycle(self, lease, bootstrap):
        session, _ = self._session(bootstrap)
        manager = await self._manager(lease, bootstrap)
        # Native sandbox deletion removes the container, services and owned volumes.
        await manager._cleanup_resources(manager._sandbox_name(session), ())
        await self._run(
            [
                *self._ssh(lease, bootstrap),
                self._python(
                    "import pathlib,shutil\n"
                    "for name in ('/var/lib/niuu/session','/etc/niuu/session-secrets'):\n"
                    "    path=pathlib.Path(name)\n"
                    "    if path.exists(): shutil.rmtree(path)\n"
                ),
            ]
        )
        self._managers.pop(str(lease.id), None)
        await manager.close()
        await self._close_tunnel(str(lease.id))

    async def close(self):
        for _, manager in self._managers.values():
            await manager.close()
        self._managers.clear()
        await super().close()
