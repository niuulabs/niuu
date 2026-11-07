"""The execution catalog Niuu ships for bring-your-own-machine Forges.

A Forge of registered hosts needs one execution: whatever machine joins, Linux or a Mac,
runs sessions as that machine's platform needs. This catalog is that execution, built in
code from a handful of deployment settings, so no operator writes or mounts one:

* **machine**: the registered-host provider's profile (``profile``);
* **access**: ``SshGuestAccess`` as ``ssh_user`` with ``bootstrap_delivery: registered``
  and ``registered_host_key`` trust: the host's own sshd key, registered at join, is
  pinned, and nothing is trusted on first use. Forge's own key is generated on first
  start under ``data_dir`` unless the access is given one;
* **host recipe**: Nix on every platform (``install-nix.sh``, pinned release), and on
  Linux the slot tools and rootless Podman (``tools``), from scripts shipped with Niuu;
* **runtime**: ``PlatformHostRuntime`` with ``SshHostVmRuntime`` for Linux (slots with
  their own user, namespace and cgroup, no container daemon) and
  ``SshDarwinHostRuntime`` for macOS (one session as a fresh user).

The session images are not catalog content: Forge hands each runtime the release's own
(``compute.session_images``: the Skuld image and bundle, resolved to digests at startup,
and the images a Mac runs natively), so a release or image bump moves them without
changing the plan running sessions, and the hosts' execution, are pinned to.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from volundr.adapters.outbound.ssh_vm_runtime import DEFAULT_CODEX_AUTH_ADAPTER
from volundr.domain.execution_catalog import (
    AccessConfiguration,
    CatalogRef,
    ExecutionCatalogError,
    ExecutionCatalogIntegrityError,
    ExecutionCatalogSnapshot,
    ExecutionProfile,
    HostRecipe,
    MachineProfile,
    RuntimeProfile,
)
from volundr.domain.services.execution_catalog import canonical_digest
from volundr.ports.execution_catalog import ExecutionCatalogPort

#: The host recipe's scripts, shipped with Niuu (``src/cli/build.py`` maps them too).
RECIPES = Path(__file__).resolve().parent / "host_recipes"
EXECUTION_ID = "host"
#: Nix release the recipe installs (install-nix.sh's default) and the runtime requires.
NIX_VERSION = "2.35.4"
#: Recipe scripts by artifact id: check, apply and verify of each stage.
_ARTIFACTS = {
    "nix_check": "nix-check.sh",
    "nix_apply": "install-nix.sh",
    "nix_verify": "nix-verify.sh",
    "tools_check": "tools-check.sh",
    "tools_apply": "tools-apply.sh",
    "tools_verify": "tools-verify.sh",
}
_PLATFORMS = ("linux", "darwin")


class BuiltinHostCatalog(ExecutionCatalogPort):
    """One host execution for every machine that joins, from deployment settings only.

    Every value is a plain setting of this Forge's deployment:

    - ``pool_id`` / ``provider_binding`` / ``profile``: the pool, its provider binding
      and the registered-host provider's profile.
    - ``data_dir``: Forge's own durable directory for SSH pins, its generated host key
      and session archives (a persistent volume on a cluster).
    - ``platform_port``: where Skuld's calls back to Forge arrive through the SSH reverse
      tunnel (the pod's Envoy port in a chart deployment, Forge's own port in mini mode).
    - ``platforms``: which host platforms join (``linux``, ``darwin``; both by default).
    - ``slot_cpu`` / ``slot_memory_mib`` / ``slot_pids``: what one Linux slot may use
      (``null`` for no limit); slot counts are suggested from them.
    - ``default_service_account``, ``preview_router_auth``,
      ``ssh_user``, ``command_timeout_seconds``, ``guest_platform_port``: as the
      runtimes and the access document them.
    """

    def __init__(
        self,
        *,
        pool_id: str,
        provider_binding: str,
        data_dir: str,
        platform_port: int,
        profile: str = "host",
        platforms: list[str] | None = None,
        ssh_user: str = "root",
        command_timeout_seconds: float = 900,
        guest_platform_port: int = 18080,
        default_service_account: str | None = None,
        preview_router_auth: dict[str, Any] | None = None,
        codex_auth_adapter: str = DEFAULT_CODEX_AUTH_ADAPTER,
        slot_cpu: float | None = 2,
        slot_memory_mib: int | None = 8192,
        slot_pids: int | None = 16384,
        slot_uid_base: int = 61000,
        nix: bool = True,
        rootless_containers: bool = True,
    ) -> None:
        chosen = list(platforms) if platforms is not None else list(_PLATFORMS)
        unknown = sorted(set(chosen) - set(_PLATFORMS))
        if unknown or not chosen or len(set(chosen)) != len(chosen):
            raise ExecutionCatalogError(
                f"platforms must list some of {', '.join(_PLATFORMS)} once each, not {chosen}"
            )
        if not 1 <= platform_port <= 65535 or not 1 <= guest_platform_port <= 65535:
            raise ExecutionCatalogError("platform_port and guest_platform_port must be TCP ports")
        self._settings = {
            "pool_id": pool_id,
            "provider_binding": provider_binding,
            "profile": profile,
            "data_dir": str(Path(data_dir).expanduser()),
            "platform_port": platform_port,
            "platforms": chosen,
            "ssh_user": ssh_user,
            "command_timeout_seconds": command_timeout_seconds,
            "guest_platform_port": guest_platform_port,
            "default_service_account": default_service_account,
            "preview_router_auth": preview_router_auth,
            "codex_auth_adapter": codex_auth_adapter,
            "slot_isolation": {
                "uid_base": slot_uid_base,
                "cpu": slot_cpu,
                "memory_mib": slot_memory_mib,
                "pids": slot_pids,
            },
            "nix": nix,
            "rootless_containers": rootless_containers,
        }

    def load(self) -> ExecutionCatalogSnapshot:
        s = self._settings
        artifacts, portable = self._artifacts()
        # One revision per content: a Niuu release that changes the execution (a recipe
        # script, say) makes a new revision instead of silently changing a pinned one.
        revision = "builtin-" + canonical_digest({"settings": s, "artifacts": portable})[:16]
        ref = {"revision": revision}
        machine = {
            "id": "host",
            **ref,
            "provider_profile": s["profile"],
            "host_requirements": {},
        }
        access = {
            "id": "ssh",
            **ref,
            "transport": {
                "binding_id": "ssh",
                "adapter": "volundr.adapters.outbound.ssh_guest_access.SshGuestAccess",
                "kwargs": {
                    "data_dir": s["data_dir"],
                    "ssh_user": s["ssh_user"],
                    "command_timeout_seconds": s["command_timeout_seconds"],
                    # Forge's key reaches the host at join; nothing is delivered at boot.
                    "bootstrap_delivery": "registered",
                },
            },
            "principal": s["ssh_user"],
            "trust": {"mode": "registered_host_key", "kwargs": {}},
        }
        recipe = {
            "id": "host-tools",
            **ref,
            "preparation": {
                "binding_id": "ssh-recipe",
                "adapter": "volundr.adapters.outbound.ssh_host_preparation.SshHostPreparation",
                "kwargs": {},
            },
            "artifacts": portable,
            "stages": [
                {
                    "id": "nix",
                    "principal": "root",
                    # Downloading Nix and the nixpkgs source; on a Mac also its volume.
                    "timeout_seconds": 1800,
                    "check": "nix_check",
                    "apply": "nix_apply",
                    "verify": "nix_verify",
                },
                {
                    "id": "tools",
                    "principal": "root",
                    "timeout_seconds": 900,
                    "check": "tools_check",
                    "apply": "tools_apply",
                    "verify": "tools_verify",
                },
            ],
        }
        runtime = {
            "id": "host",
            **ref,
            "runtime": {
                "binding_id": "platform-host",
                "adapter": "volundr.adapters.outbound.platform_host_runtime.PlatformHostRuntime",
                "kwargs": {"platforms": self._platform_runtimes()},
            },
            "contributor_backend": "vm",
            "storage_mode": "vm",
            "capabilities": ["workspace_archive"],
            "host_requirements": {"nix": NIX_VERSION} if s["nix"] else {},
        }
        execution = {
            "id": EXECUTION_ID,
            **ref,
            "pool_id": s["pool_id"],
            "provider_binding": s["provider_binding"],
            "machine": {"id": "host", **ref},
            "host_recipe": {"id": "host-tools", **ref},
            "access": {"id": "ssh", **ref},
            "runtime": {"id": "host", **ref},
        }
        try:
            return ExecutionCatalogSnapshot(
                default_execution=CatalogRef(id=EXECUTION_ID, revision=revision),
                machines=(MachineProfile.model_validate(_digested(machine)),),
                host_recipes=(
                    HostRecipe.model_validate(
                        {
                            **recipe,
                            "artifacts": artifacts,
                            "digest": canonical_digest(recipe),
                        }
                    ),
                ),
                access_configurations=(AccessConfiguration.model_validate(_digested(access)),),
                runtimes=(RuntimeProfile.model_validate(_digested(runtime)),),
                executions=(ExecutionProfile.model_validate(_digested(execution)),),
            )
        except ValidationError as exc:
            raise ExecutionCatalogError(f"Invalid built-in host catalog settings: {exc}") from exc

    def _platform_runtimes(self) -> dict[str, dict]:
        s = self._settings
        common = {
            "platform_port": s["platform_port"],
            "command_timeout_seconds": s["command_timeout_seconds"],
            "codex_auth_adapter": s["codex_auth_adapter"],
            "nix": s["nix"],
        }
        if s["default_service_account"]:
            common["default_service_account"] = s["default_service_account"]
        if s["preview_router_auth"]:
            common["preview_router_auth"] = s["preview_router_auth"]
        children = {}
        if "linux" in s["platforms"]:
            children["linux"] = {
                "adapter": "volundr.adapters.outbound.ssh_host_vm_runtime.SshHostVmRuntime",
                "kwargs": {
                    **common,
                    "slot_isolation": s["slot_isolation"],
                    "containers": {} if s["rootless_containers"] else None,
                },
            }
        if "darwin" in s["platforms"]:
            children["darwin"] = {
                "adapter": (
                    "volundr.adapters.outbound.ssh_darwin_host_runtime.SshDarwinHostRuntime"
                ),
                "kwargs": {
                    **common,
                    "guest_platform_port": s["guest_platform_port"],
                    "uid_base": s["slot_isolation"]["uid_base"],
                },
            }
        return children

    @staticmethod
    def _artifacts() -> tuple[list[dict], list[dict]]:
        resolved, portable = [], []
        for artifact_id, name in _ARTIFACTS.items():
            path = RECIPES / name
            try:
                content = path.read_bytes()
            except OSError as exc:
                raise ExecutionCatalogIntegrityError(
                    f"The built-in host recipe script {path} is missing from this install"
                ) from exc
            entry = {
                "id": artifact_id,
                "package_path": f"host_recipes/{name}",
                "sha256": hashlib.sha256(content).hexdigest(),
                "media_type": "text/x-shellscript",
            }
            portable.append(entry)
            resolved.append({**entry, "resolved_path": path})
        return resolved, portable


def _digested(entry: dict) -> dict:
    return {**entry, "digest": canonical_digest(entry)}
