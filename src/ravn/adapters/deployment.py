"""Deployment adapters for long-lived Ravn wardens."""

from __future__ import annotations

import asyncio
import os
import subprocess
import threading
from datetime import UTC, datetime
from pathlib import Path

import yaml

from ravn.ports.warden_deployer import (
    WardenDeploymentError,
    WardenDeploymentPort,
    WardenDeploymentResult,
    WardenObservationResult,
)
from ravn.warden.artifacts import (
    error_log_path,
    local_python_executable,
    local_start_command,
    log_path,
    manifest_bundle_path,
    render_k8s_bundle,
    render_launchd_plist,
    render_systemd_unit,
    runtime_config_path,
    service_label,
    write_k8s_bundle,
    write_runtime_config,
)
from ravn.warden.models import (
    WardenObservation,
    WardenObservedField,
    WardenSpec,
    WardenSupervisor,
)


class LaunchdWardenDeploymentAdapter(WardenDeploymentPort):
    """Install and start wardens using per-user launchd agents."""

    def __init__(
        self,
        *,
        launch_agents_dir: str = "~/Library/LaunchAgents",
        launchctl_bin: str = "launchctl",
        user_id: int | None = None,
    ) -> None:
        self._launch_agents_dir = Path(launch_agents_dir).expanduser()
        self._launchctl_bin = launchctl_bin
        self._user_id = os.getuid() if user_id is None else user_id

    def install(
        self,
        spec: WardenSpec,
        *,
        warden_dir: Path,
        workspace_root: Path | None = None,
    ) -> WardenDeploymentResult:
        python_executable = local_python_executable()
        warden_dir.mkdir(parents=True, exist_ok=True)
        config_path = write_runtime_config(
            spec,
            warden_dir=warden_dir,
            workspace_root=workspace_root,
        )
        service_path = self._service_file_path(spec.id)
        service_path.parent.mkdir(parents=True, exist_ok=True)
        service_path.write_text(
            render_launchd_plist(
                spec,
                config_path=config_path,
                working_directory=(workspace_root or Path.cwd()).resolve(),
                stdout_path=log_path(warden_dir),
                stderr_path=error_log_path(warden_dir),
                python_executable=python_executable,
            ),
            encoding="utf-8",
        )

        runtime_state = "idle"
        if spec.autostart:
            self._run(
                self._launchctl_bin,
                "unload",
                "-w",
                str(service_path),
                check=False,
            )
            self._run(self._launchctl_bin, "load", "-w", str(service_path))

        supervisor = WardenSupervisor(
            installed=True,
            service_label=service_label(spec.id),
            service_file=str(service_path),
            config_file=str(config_path),
            start_command=local_start_command(
                spec,
                config_path=config_path,
                python_executable=python_executable,
            ),
            last_install_at=datetime.now(UTC),
        )
        if spec.autostart:
            observed = self.observe(
                spec.model_copy(update={"supervisor": supervisor}),
                warden_dir=warden_dir,
            )
            supervisor = observed.supervisor
            runtime_state = _runtime_state_from_observation(
                observed.supervisor.observation.status
            )
        return WardenDeploymentResult(supervisor=supervisor, runtime_state=runtime_state)

    def start(self, spec: WardenSpec, *, warden_dir: Path) -> WardenDeploymentResult:
        python_executable = local_python_executable()
        config_path = Path(
            spec.supervisor.config_file
            or write_runtime_config(spec, warden_dir=warden_dir)
        )
        service_path = Path(spec.supervisor.service_file or self._service_file_path(spec.id))
        self._run(self._launchctl_bin, "unload", "-w", str(service_path), check=False)
        self._run(self._launchctl_bin, "load", "-w", str(service_path))
        supervisor = spec.supervisor.model_copy(
            update={
                "installed": True,
                "service_label": spec.supervisor.service_label or service_label(spec.id),
                "service_file": str(service_path),
                "config_file": str(config_path),
                "start_command": local_start_command(
                    spec,
                    config_path=config_path,
                    python_executable=python_executable,
                ),
            }
        )
        observed = self.observe(
            spec.model_copy(update={"supervisor": supervisor}),
            warden_dir=warden_dir,
        )
        return WardenDeploymentResult(
            supervisor=observed.supervisor,
            runtime_state=_runtime_state_from_observation(
                observed.supervisor.observation.status
            ),
        )

    def stop(self, spec: WardenSpec, *, warden_dir: Path) -> WardenDeploymentResult:
        del warden_dir
        service_path = Path(spec.supervisor.service_file or self._service_file_path(spec.id))
        self._run(self._launchctl_bin, "unload", "-w", str(service_path), check=False)
        return WardenDeploymentResult(
            supervisor=spec.supervisor.model_copy(update={"installed": True}),
            runtime_state="idle",
        )

    def uninstall(self, spec: WardenSpec, *, warden_dir: Path) -> WardenDeploymentResult:
        service_path = Path(spec.supervisor.service_file or self._service_file_path(spec.id))
        self._run(self._launchctl_bin, "unload", "-w", str(service_path), check=False)
        self._remove_file(service_path)
        self._remove_file(Path(spec.supervisor.config_file or runtime_config_path(warden_dir)))
        return WardenDeploymentResult(
            supervisor=WardenSupervisor(),
            runtime_state="offline",
        )

    def observe(self, spec: WardenSpec, *, warden_dir: Path) -> WardenObservationResult:
        del warden_dir
        service_path = Path(spec.supervisor.service_file or self._service_file_path(spec.id))
        config_path = Path(spec.supervisor.config_file) if spec.supervisor.config_file else None
        result = self._run(
            self._launchctl_bin,
            "print",
            self._domain_label(spec.id),
            check=False,
        )
        output = (result.stdout or result.stderr).strip()
        launchd_state = self._extract_assignment(output, "state")
        pid = self._extract_assignment(output, "pid")
        artifact_exists = service_path.exists()
        fields = [
            _field("artifact present", artifact_exists),
            _field("launchctl known", result.returncode == 0),
        ]
        if launchd_state:
            fields.append(_field("launchctl state", launchd_state))
        if pid:
            fields.append(_field("pid", pid))
        if config_path is not None:
            fields.append(_field("config present", config_path.exists()))

        if result.returncode == 0 and (launchd_state == "running" or pid):
            observation = _now_observation(
                status="running",
                detail="launchd reports the agent as loaded and running",
                source="launchctl",
                fields=fields,
            )
        elif result.returncode == 0:
            observation = _now_observation(
                status="idle",
                detail="launchd knows the agent but it is not currently running",
                source="launchctl",
                fields=fields,
            )
        elif artifact_exists:
            observation = _now_observation(
                status="degraded",
                detail=(
                    output
                    or "launch agent file exists but launchctl does not report it as loaded"
                ),
                source="launchctl",
                fields=fields,
            )
        else:
            observation = _now_observation(
                status="missing",
                detail=output or "launch agent file is missing and launchctl has no record of it",
                source="launchctl",
                fields=fields,
            )

        return WardenObservationResult(
            supervisor=spec.supervisor.model_copy(update={"observation": observation})
        )

    def _service_file_path(self, warden_id: str) -> Path:
        return self._launch_agents_dir / f"{service_label(warden_id)}.plist"

    def _domain(self) -> str:
        return f"gui/{self._user_id}"

    def _domain_label(self, warden_id: str) -> str:
        return f"{self._domain()}/{service_label(warden_id)}"

    @staticmethod
    def _extract_assignment(output: str, key: str) -> str:
        needle = f"{key} = "
        for line in output.splitlines():
            line = line.strip()
            if line.startswith(needle):
                return line.removeprefix(needle).strip()
        return ""

    @staticmethod
    def _run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(args, capture_output=True, text=True, check=False)
        if check and result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or "unknown launchctl error"
            raise WardenDeploymentError(message)
        return result

    @staticmethod
    def _remove_file(path: Path) -> None:
        try:
            path.unlink()
        except FileNotFoundError:
            return


class SystemdUserWardenDeploymentAdapter(WardenDeploymentPort):
    """Install and start wardens using per-user systemd units."""

    def __init__(
        self,
        *,
        unit_dir: str = "~/.config/systemd/user",
        systemctl_bin: str = "systemctl",
    ) -> None:
        self._unit_dir = Path(unit_dir).expanduser()
        self._systemctl_bin = systemctl_bin

    def install(
        self,
        spec: WardenSpec,
        *,
        warden_dir: Path,
        workspace_root: Path | None = None,
    ) -> WardenDeploymentResult:
        python_executable = local_python_executable()
        warden_dir.mkdir(parents=True, exist_ok=True)
        config_path = write_runtime_config(
            spec,
            warden_dir=warden_dir,
            workspace_root=workspace_root,
        )
        service_path = self._service_file_path(spec.id)
        service_path.parent.mkdir(parents=True, exist_ok=True)
        service_path.write_text(
            render_systemd_unit(
                spec,
                config_path=config_path,
                working_directory=(workspace_root or Path.cwd()).resolve(),
                python_executable=python_executable,
            ),
            encoding="utf-8",
        )

        self._run(self._systemctl_bin, "--user", "daemon-reload")
        self._run(self._systemctl_bin, "--user", "enable", service_path.name)

        runtime_state = "idle"
        if spec.autostart:
            self._run(self._systemctl_bin, "--user", "start", service_path.name)
            runtime_state = "active"

        supervisor = WardenSupervisor(
            installed=True,
            service_label=service_label(spec.id),
            service_file=str(service_path),
            config_file=str(config_path),
            start_command=local_start_command(
                spec,
                config_path=config_path,
                python_executable=python_executable,
            ),
            last_install_at=datetime.now(UTC),
        )
        if spec.autostart:
            observed = self.observe(
                spec.model_copy(update={"supervisor": supervisor}),
                warden_dir=warden_dir,
            )
            supervisor = observed.supervisor
            runtime_state = _runtime_state_from_observation(
                observed.supervisor.observation.status
            )
        return WardenDeploymentResult(supervisor=supervisor, runtime_state=runtime_state)

    def start(self, spec: WardenSpec, *, warden_dir: Path) -> WardenDeploymentResult:
        python_executable = local_python_executable()
        config_path = Path(
            spec.supervisor.config_file
            or write_runtime_config(spec, warden_dir=warden_dir)
        )
        unit_name = self._unit_name(spec)
        self._run(self._systemctl_bin, "--user", "start", unit_name)
        supervisor = spec.supervisor.model_copy(
            update={
                "installed": True,
                "service_label": spec.supervisor.service_label or service_label(spec.id),
                "config_file": str(config_path),
                "start_command": local_start_command(
                    spec,
                    config_path=config_path,
                    python_executable=python_executable,
                ),
            }
        )
        observed = self.observe(
            spec.model_copy(update={"supervisor": supervisor}),
            warden_dir=warden_dir,
        )
        return WardenDeploymentResult(
            supervisor=observed.supervisor,
            runtime_state=_runtime_state_from_observation(
                observed.supervisor.observation.status
            ),
        )

    def stop(self, spec: WardenSpec, *, warden_dir: Path) -> WardenDeploymentResult:
        del warden_dir
        unit_name = self._unit_name(spec)
        self._run(self._systemctl_bin, "--user", "stop", unit_name)
        return WardenDeploymentResult(
            supervisor=spec.supervisor.model_copy(update={"installed": True}),
            runtime_state="idle",
        )

    def uninstall(self, spec: WardenSpec, *, warden_dir: Path) -> WardenDeploymentResult:
        unit_name = self._unit_name(spec)
        self._run(self._systemctl_bin, "--user", "stop", unit_name, check=False)
        self._run(self._systemctl_bin, "--user", "disable", unit_name, check=False)
        self._remove_file(Path(spec.supervisor.service_file or self._service_file_path(spec.id)))
        self._remove_file(Path(spec.supervisor.config_file or runtime_config_path(warden_dir)))
        self._run(self._systemctl_bin, "--user", "daemon-reload")
        return WardenDeploymentResult(
            supervisor=WardenSupervisor(),
            runtime_state="offline",
        )

    def observe(self, spec: WardenSpec, *, warden_dir: Path) -> WardenObservationResult:
        del warden_dir
        unit_name = self._unit_name(spec)
        service_path = Path(spec.supervisor.service_file or self._service_file_path(spec.id))
        config_path = Path(spec.supervisor.config_file) if spec.supervisor.config_file else None
        result = self._run(
            self._systemctl_bin,
            "--user",
            "show",
            unit_name,
            "--property=LoadState,ActiveState,SubState,UnitFileState",
            check=False,
        )
        values = self._parse_show_output(result.stdout)
        fields = [
            _field("artifact present", service_path.exists()),
            _field("load state", values.get("LoadState", "")),
            _field("active state", values.get("ActiveState", "")),
            _field("sub state", values.get("SubState", "")),
            _field("unit file state", values.get("UnitFileState", "")),
        ]
        if config_path is not None:
            fields.append(_field("config present", config_path.exists()))

        load_state = values.get("LoadState", "")
        active_state = values.get("ActiveState", "")
        if load_state == "loaded" and active_state == "active":
            observation = _now_observation(
                status="running",
                detail="systemd reports the unit as active",
                source="systemctl --user",
                fields=fields,
            )
        elif load_state == "loaded":
            observation = _now_observation(
                status="idle",
                detail="systemd reports the unit as loaded but inactive",
                source="systemctl --user",
                fields=fields,
            )
        elif service_path.exists():
            observation = _now_observation(
                status="degraded",
                detail=(result.stderr or result.stdout).strip()
                or "unit file exists but systemd does not report it as loaded",
                source="systemctl --user",
                fields=fields,
            )
        else:
            observation = _now_observation(
                status="missing",
                detail=(result.stderr or result.stdout).strip()
                or "unit file is missing and systemd does not know about it",
                source="systemctl --user",
                fields=fields,
            )
        return WardenObservationResult(
            supervisor=spec.supervisor.model_copy(update={"observation": observation})
        )

    def _service_file_path(self, warden_id: str) -> Path:
        return self._unit_dir / f"{service_label(warden_id)}.service"

    def _unit_name(self, spec: WardenSpec) -> str:
        if spec.supervisor.service_file:
            return Path(spec.supervisor.service_file).name
        return self._service_file_path(spec.id).name

    @staticmethod
    def _parse_show_output(output: str) -> dict[str, str]:
        values: dict[str, str] = {}
        for line in output.splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key] = value
        return values

    @staticmethod
    def _run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(args, capture_output=True, text=True, check=False)
        if check and result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or "unknown systemctl error"
            raise WardenDeploymentError(message)
        return result

    @staticmethod
    def _remove_file(path: Path) -> None:
        try:
            path.unlink()
        except FileNotFoundError:
            return


def _run_coro_blocking(awaitable):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)

    result: dict[str, object] = {}
    error: dict[str, BaseException] = {}

    def _runner() -> None:
        try:
            result["value"] = asyncio.run(awaitable)
        except BaseException as exc:  # pragma: no cover - bubbled to caller
            error["value"] = exc

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()
    if "value" in error:
        raise error["value"]
    return result.get("value")


def _field(label: str, value: str | int | bool | None) -> WardenObservedField:
    if value is None:
        text = ""
    elif isinstance(value, bool):
        text = "yes" if value else "no"
    else:
        text = str(value)
    return WardenObservedField(label=label, value=text)


def _now_observation(
    *,
    status: str,
    detail: str,
    source: str,
    fields: list[WardenObservedField],
) -> WardenObservation:
    return WardenObservation(
        status=status,
        detail=detail,
        source=source,
        checked_at=datetime.now(UTC),
        fields=fields,
    )


def _runtime_state_from_observation(status: str) -> str:
    if status == "running":
        return "active"
    if status == "missing":
        return "offline"
    return "idle"


class KubernetesApplyWardenDeploymentAdapter(WardenDeploymentPort):
    """Install and scale wardens directly through the Kubernetes API."""

    def __init__(
        self,
        *,
        namespace: str = "ravn",
        kubeconfig: str = "",
        image: str = "ghcr.io/niuulabs/ravn:latest",
        container_workspace_root: str = "/workspace",
        config_mount_path: str = "/etc/ravn/config.yaml",
        create_namespace: bool = False,
        image_pull_policy: str = "IfNotPresent",
        service_account_name: str = "",
        extra_env: dict[str, str] | None = None,
    ) -> None:
        self._namespace = namespace
        self._kubeconfig = kubeconfig
        self._image = image
        self._container_workspace_root = container_workspace_root
        self._config_mount_path = config_mount_path
        self._create_namespace = create_namespace
        self._image_pull_policy = image_pull_policy
        self._service_account_name = service_account_name
        self._extra_env = extra_env or {}
        self._api_client = None

    def install(
        self,
        spec: WardenSpec,
        *,
        warden_dir: Path,
        workspace_root: Path | None = None,
    ) -> WardenDeploymentResult:
        del workspace_root
        warden_dir.mkdir(parents=True, exist_ok=True)
        config_path = write_runtime_config(
            spec,
            warden_dir=warden_dir,
            workspace_root=Path(self._container_workspace_root),
        )
        config_text = config_path.read_text(encoding="utf-8")
        replicas = 1 if spec.autostart else 0
        bundle_path = write_k8s_bundle(
            spec,
            warden_dir=warden_dir,
            namespace=self._namespace,
            image=self._image,
            replicas=replicas,
            config_text=config_text,
            config_mount_path=self._config_mount_path,
            create_namespace=self._create_namespace,
            image_pull_policy=self._image_pull_policy,
            service_account_name=self._service_account_name,
            extra_env=self._extra_env,
        )
        _run_coro_blocking(self._apply_install(spec, config_text=config_text, replicas=replicas))
        return WardenDeploymentResult(
            supervisor=WardenSupervisor(
                installed=True,
                service_label=self._resource_name(spec),
                service_file=str(bundle_path),
                config_file=str(config_path),
                start_command="k8s-apply",
                last_install_at=datetime.now(UTC),
            ),
            runtime_state="active" if replicas > 0 else "idle",
        )

    def start(self, spec: WardenSpec, *, warden_dir: Path) -> WardenDeploymentResult:
        _run_coro_blocking(self._scale_deployment(spec, replicas=1))
        return WardenDeploymentResult(
            supervisor=spec.supervisor.model_copy(
                update={
                    "installed": True,
                    "service_label": spec.supervisor.service_label or self._resource_name(spec),
                    "service_file": spec.supervisor.service_file
                    or str(manifest_bundle_path(warden_dir)),
                    "start_command": "k8s-apply",
                }
            ),
            runtime_state="active",
        )

    def stop(self, spec: WardenSpec, *, warden_dir: Path) -> WardenDeploymentResult:
        _run_coro_blocking(self._scale_deployment(spec, replicas=0))
        return WardenDeploymentResult(
            supervisor=spec.supervisor.model_copy(
                update={
                    "installed": True,
                    "service_label": spec.supervisor.service_label or self._resource_name(spec),
                    "service_file": spec.supervisor.service_file
                    or str(manifest_bundle_path(warden_dir)),
                    "start_command": "k8s-apply",
                }
            ),
            runtime_state="idle",
        )

    def uninstall(self, spec: WardenSpec, *, warden_dir: Path) -> WardenDeploymentResult:
        _run_coro_blocking(self._delete_install(spec))
        self._remove_file(Path(spec.supervisor.service_file or manifest_bundle_path(warden_dir)))
        self._remove_file(Path(spec.supervisor.config_file or runtime_config_path(warden_dir)))
        return WardenDeploymentResult(
            supervisor=WardenSupervisor(),
            runtime_state="offline",
        )

    def observe(self, spec: WardenSpec, *, warden_dir: Path) -> WardenObservationResult:
        del warden_dir
        observed = _run_coro_blocking(self._observe_install(spec))
        desired_replicas = int(observed.get("desired_replicas", 0))
        available_replicas = int(observed.get("available_replicas", 0))
        fields = [
            _field("namespace", self._namespace),
            _field("deployment present", observed.get("deployment_present", False)),
            _field("config map present", observed.get("config_map_present", False)),
            _field("desired replicas", desired_replicas),
            _field("available replicas", available_replicas),
            _field("ready replicas", observed.get("ready_replicas", 0)),
        ]

        if not observed.get("deployment_present", False):
            status = "missing"
            detail = "deployment resource is missing from the cluster"
        elif desired_replicas > 0 and available_replicas > 0:
            status = "running"
            detail = "deployment is present and serving replicas"
        elif desired_replicas > 0:
            status = "degraded"
            detail = "deployment desires replicas but none are currently available"
        else:
            status = "idle"
            detail = "deployment is present with zero desired replicas"

        if not observed.get("config_map_present", False):
            status = "degraded" if status != "missing" else status
            detail = (
                "deployment exists but the config map is missing"
                if status != "missing"
                else detail
            )

        observation = _now_observation(
            status=status,
            detail=detail,
            source="kubernetes",
            fields=fields,
        )
        return WardenObservationResult(
            supervisor=spec.supervisor.model_copy(update={"observation": observation})
        )

    async def _apply_install(self, spec: WardenSpec, *, config_text: str, replicas: int) -> None:
        core_api, apps_api = await self._load_clients()
        if self._create_namespace:
            await self._ensure_namespace(core_api)

        name = self._resource_name(spec)
        await self._upsert_config_map(
            core_api,
            name=name,
            config_text=config_text,
        )
        await self._upsert_deployment(
            apps_api,
            spec,
            name=name,
            replicas=replicas,
        )

    async def _scale_deployment(self, spec: WardenSpec, *, replicas: int) -> None:
        _, apps_api = await self._load_clients()
        name = self._resource_name(spec)
        await apps_api.patch_namespaced_deployment_scale(
            name=name,
            namespace=self._namespace,
            body={"spec": {"replicas": replicas}},
        )

    async def _delete_install(self, spec: WardenSpec) -> None:
        core_api, apps_api = await self._load_clients()
        name = self._resource_name(spec)
        await self._delete_if_present(
            lambda: apps_api.delete_namespaced_deployment(
                name=name,
                namespace=self._namespace,
            )
        )
        await self._delete_if_present(
            lambda: core_api.delete_namespaced_config_map(
                name=name,
                namespace=self._namespace,
            )
        )

    async def _observe_install(self, spec: WardenSpec) -> dict[str, int | bool]:
        core_api, apps_api = await self._load_clients()
        name = self._resource_name(spec)
        deployment = await self._read_if_present(
            lambda: apps_api.read_namespaced_deployment(name=name, namespace=self._namespace)
        )
        config_map = await self._read_if_present(
            lambda: core_api.read_namespaced_config_map(name=name, namespace=self._namespace)
        )

        desired_replicas = 0
        ready_replicas = 0
        available_replicas = 0
        if deployment is not None:
            desired_replicas = int(getattr(deployment.spec, "replicas", 0) or 0)
            ready_replicas = int(getattr(deployment.status, "ready_replicas", 0) or 0)
            available_replicas = int(getattr(deployment.status, "available_replicas", 0) or 0)

        return {
            "deployment_present": deployment is not None,
            "config_map_present": config_map is not None,
            "desired_replicas": desired_replicas,
            "ready_replicas": ready_replicas,
            "available_replicas": available_replicas,
        }

    async def _load_clients(self):
        from kubernetes_asyncio import client, config

        if self._api_client is None:
            if self._kubeconfig:
                await config.load_kube_config(config_file=self._kubeconfig)
            else:
                try:
                    config.load_incluster_config()
                except config.ConfigException:
                    await config.load_kube_config()
            self._api_client = client.ApiClient()
        return (
            client.CoreV1Api(self._api_client),
            client.AppsV1Api(self._api_client),
        )

    async def _ensure_namespace(self, core_api) -> None:
        from kubernetes_asyncio.client import ApiException, V1Namespace, V1ObjectMeta

        try:
            await core_api.read_namespace(name=self._namespace)
        except ApiException as exc:
            if exc.status != 404:
                raise WardenDeploymentError(str(exc)) from exc
            await core_api.create_namespace(
                V1Namespace(metadata=V1ObjectMeta(name=self._namespace))
            )

    async def _upsert_config_map(self, core_api, *, name: str, config_text: str) -> None:
        body = {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {
                "name": name,
                "namespace": self._namespace,
            },
            "data": {
                "config.yaml": config_text,
            },
        }
        try:
            await core_api.read_namespaced_config_map(name=name, namespace=self._namespace)
        except Exception as exc:
            if "404" in str(exc) or "NotFound" in str(exc):
                await core_api.create_namespaced_config_map(
                    namespace=self._namespace,
                    body=body,
                )
                return
            raise WardenDeploymentError(str(exc)) from exc
        await core_api.patch_namespaced_config_map(
            name=name,
            namespace=self._namespace,
            body=body,
        )

    async def _upsert_deployment(
        self,
        apps_api,
        spec: WardenSpec,
        *,
        name: str,
        replicas: int,
    ) -> None:
        labels = {
            "app.kubernetes.io/name": "ravn-warden",
            "app.kubernetes.io/instance": spec.id,
            "app.kubernetes.io/managed-by": "ravn",
        }
        body = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                "name": name,
                "namespace": self._namespace,
                "labels": labels,
            },
            "spec": {
                "replicas": replicas,
                "selector": {
                    "matchLabels": {
                        "app.kubernetes.io/instance": spec.id,
                    }
                },
                "template": {
                    "metadata": {
                        "labels": labels,
                    },
                    "spec": {
                        **(
                            {"serviceAccountName": self._service_account_name}
                            if self._service_account_name
                            else {}
                        ),
                        "containers": [
                            {
                                "name": "ravn",
                                "image": self._image,
                                "imagePullPolicy": self._image_pull_policy,
                                "command": [
                                    "/usr/bin/env",
                                    "ravn",
                                    "daemon",
                                    "--config",
                                    self._config_mount_path,
                                    "--persona",
                                    spec.persona,
                                ]
                                + (
                                    ["--profile", spec.profile]
                                    if spec.profile
                                    else []
                                ),
                                "env": [
                                    {"name": key, "value": value}
                                    for key, value in sorted(self._extra_env.items())
                                ],
                                "volumeMounts": [
                                    {
                                        "name": "ravn-config",
                                        "mountPath": self._config_mount_path,
                                        "subPath": "config.yaml",
                                        "readOnly": True,
                                    }
                                ],
                            }
                        ],
                        "volumes": [
                            {
                                "name": "ravn-config",
                                "configMap": {
                                    "name": name,
                                },
                            }
                        ],
                    },
                },
            },
        }
        try:
            await apps_api.read_namespaced_deployment(name=name, namespace=self._namespace)
        except Exception as exc:
            if "404" in str(exc) or "NotFound" in str(exc):
                await apps_api.create_namespaced_deployment(
                    namespace=self._namespace,
                    body=body,
                )
                return
            raise WardenDeploymentError(str(exc)) from exc
        await apps_api.patch_namespaced_deployment(
            name=name,
            namespace=self._namespace,
            body=body,
        )

    @staticmethod
    def _resource_name(spec: WardenSpec) -> str:
        return service_label(spec.id).replace(".", "-")

    @staticmethod
    async def _delete_if_present(delete_call) -> None:
        try:
            await delete_call()
        except Exception as exc:
            if "404" in str(exc) or "NotFound" in str(exc):
                return
            raise WardenDeploymentError(str(exc)) from exc

    @staticmethod
    async def _read_if_present(read_call):
        try:
            return await read_call()
        except Exception as exc:
            if "404" in str(exc) or "NotFound" in str(exc):
                return None
            raise WardenDeploymentError(str(exc)) from exc

    @staticmethod
    def _remove_file(path: Path) -> None:
        try:
            path.unlink()
        except FileNotFoundError:
            return


class KubernetesGitOpsWardenDeploymentAdapter(WardenDeploymentPort):
    """Render warden manifests into a GitOps repo and optionally commit/push them."""

    def __init__(
        self,
        *,
        repo_path: str = "",
        manifests_subdir: str = "wardens",
        image: str = "ghcr.io/niuulabs/ravn:latest",
        namespace: str = "ravn",
        container_workspace_root: str = "/workspace",
        config_mount_path: str = "/etc/ravn/config.yaml",
        create_namespace: bool = False,
        image_pull_policy: str = "IfNotPresent",
        service_account_name: str = "",
        auto_commit: bool = False,
        auto_push: bool = False,
        remote_name: str = "origin",
        commit_message_prefix: str = "Deploy warden",
        extra_env: dict[str, str] | None = None,
    ) -> None:
        self._repo_path = Path(repo_path).expanduser() if repo_path else None
        self._manifests_subdir = manifests_subdir.strip().strip("/")
        self._image = image
        self._namespace = namespace
        self._container_workspace_root = container_workspace_root
        self._config_mount_path = config_mount_path
        self._create_namespace = create_namespace
        self._image_pull_policy = image_pull_policy
        self._service_account_name = service_account_name
        self._auto_commit = auto_commit
        self._auto_push = auto_push
        self._remote_name = remote_name
        self._commit_message_prefix = commit_message_prefix
        self._extra_env = extra_env or {}

    def install(
        self,
        spec: WardenSpec,
        *,
        warden_dir: Path,
        workspace_root: Path | None = None,
    ) -> WardenDeploymentResult:
        del workspace_root
        repo_path = self._require_repo_path()
        warden_dir.mkdir(parents=True, exist_ok=True)
        config_path = write_runtime_config(
            spec,
            warden_dir=warden_dir,
            workspace_root=Path(self._container_workspace_root),
        )
        config_text = config_path.read_text(encoding="utf-8")
        manifest_path = self._manifest_path(spec)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            render_k8s_bundle(
                spec,
                namespace=self._namespace,
                image=self._image,
                replicas=1 if spec.autostart else 0,
                config_text=config_text,
                config_mount_path=self._config_mount_path,
                create_namespace=self._create_namespace,
                image_pull_policy=self._image_pull_policy,
                service_account_name=self._service_account_name,
                extra_env=self._extra_env,
            ),
            encoding="utf-8",
        )
        self._stage_and_publish(repo_path, manifest_path, spec, active=spec.autostart)
        return WardenDeploymentResult(
            supervisor=WardenSupervisor(
                installed=True,
                service_label=self._resource_name(spec),
                service_file=str(manifest_path),
                config_file=str(config_path),
                start_command="k8s-gitops",
                last_install_at=datetime.now(UTC),
            ),
            runtime_state="active" if spec.autostart else "idle",
        )

    def start(self, spec: WardenSpec, *, warden_dir: Path) -> WardenDeploymentResult:
        repo_path = self._require_repo_path()
        config_path = Path(
            spec.supervisor.config_file
            or write_runtime_config(
                spec,
                warden_dir=warden_dir,
                workspace_root=Path(self._container_workspace_root),
            )
        )
        config_text = config_path.read_text(encoding="utf-8")
        manifest_path = self._manifest_path(spec)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            render_k8s_bundle(
                spec,
                namespace=self._namespace,
                image=self._image,
                replicas=1,
                config_text=config_text,
                config_mount_path=self._config_mount_path,
                create_namespace=self._create_namespace,
                image_pull_policy=self._image_pull_policy,
                service_account_name=self._service_account_name,
                extra_env=self._extra_env,
            ),
            encoding="utf-8",
        )
        self._stage_and_publish(repo_path, manifest_path, spec, active=True)
        return WardenDeploymentResult(
            supervisor=spec.supervisor.model_copy(
                update={
                    "installed": True,
                    "service_label": spec.supervisor.service_label or self._resource_name(spec),
                    "service_file": str(manifest_path),
                    "config_file": str(config_path),
                    "start_command": "k8s-gitops",
                }
            ),
            runtime_state="active",
        )

    def stop(self, spec: WardenSpec, *, warden_dir: Path) -> WardenDeploymentResult:
        repo_path = self._require_repo_path()
        config_path = Path(
            spec.supervisor.config_file
            or write_runtime_config(
                spec,
                warden_dir=warden_dir,
                workspace_root=Path(self._container_workspace_root),
            )
        )
        config_text = config_path.read_text(encoding="utf-8")
        manifest_path = self._manifest_path(spec)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            render_k8s_bundle(
                spec,
                namespace=self._namespace,
                image=self._image,
                replicas=0,
                config_text=config_text,
                config_mount_path=self._config_mount_path,
                create_namespace=self._create_namespace,
                image_pull_policy=self._image_pull_policy,
                service_account_name=self._service_account_name,
                extra_env=self._extra_env,
            ),
            encoding="utf-8",
        )
        self._stage_and_publish(repo_path, manifest_path, spec, active=False)
        return WardenDeploymentResult(
            supervisor=spec.supervisor.model_copy(
                update={
                    "installed": True,
                    "service_label": spec.supervisor.service_label or self._resource_name(spec),
                    "service_file": str(manifest_path),
                    "config_file": str(config_path),
                    "start_command": "k8s-gitops",
                }
            ),
            runtime_state="idle",
        )

    def uninstall(self, spec: WardenSpec, *, warden_dir: Path) -> WardenDeploymentResult:
        repo_path = self._require_repo_path()
        manifest_path = self._manifest_path(spec)
        try:
            manifest_path.unlink()
        except FileNotFoundError:
            pass
        self._stage_and_publish(repo_path, manifest_path, spec, active=False, removed=True)
        self._remove_file(Path(spec.supervisor.config_file or runtime_config_path(warden_dir)))
        return WardenDeploymentResult(
            supervisor=WardenSupervisor(),
            runtime_state="offline",
        )

    def observe(self, spec: WardenSpec, *, warden_dir: Path) -> WardenObservationResult:
        del warden_dir
        manifest_path = self._manifest_path(spec)
        manifest_exists = manifest_path.exists()
        desired_replicas = self._read_manifest_replicas(manifest_path) if manifest_exists else 0
        fields = [
            _field("repo path", self._require_repo_path()),
            _field("manifest present", manifest_exists),
            _field("namespace", self._namespace),
            _field("desired replicas", desired_replicas),
            _field("manifest path", manifest_path),
        ]

        if manifest_exists and desired_replicas > 0:
            observation = _now_observation(
                status="running",
                detail="GitOps manifest exists and requests active replicas",
                source="gitops",
                fields=fields,
            )
        elif manifest_exists:
            observation = _now_observation(
                status="idle",
                detail="GitOps manifest exists and requests zero replicas",
                source="gitops",
                fields=fields,
            )
        else:
            observation = _now_observation(
                status="missing",
                detail="GitOps manifest is missing from the repository",
                source="gitops",
                fields=fields,
            )

        return WardenObservationResult(
            supervisor=spec.supervisor.model_copy(update={"observation": observation})
        )

    def _manifest_path(self, spec: WardenSpec) -> Path:
        repo_path = self._require_repo_path()
        relative_dir = Path(self._manifests_subdir) if self._manifests_subdir else Path()
        return repo_path / relative_dir / f"{spec.id}.yaml"

    def _require_repo_path(self) -> Path:
        if self._repo_path is None:
            msg = "repo_path is required for k8s-gitops deployment"
            raise WardenDeploymentError(msg)
        return self._repo_path

    @staticmethod
    def _read_manifest_replicas(manifest_path: Path) -> int:
        try:
            documents = list(yaml.safe_load_all(manifest_path.read_text(encoding="utf-8")))
        except Exception as exc:
            msg = f"failed to parse GitOps manifest {manifest_path}: {exc}"
            raise WardenDeploymentError(msg) from exc

        for document in documents:
            if not isinstance(document, dict):
                continue
            if document.get("kind") != "Deployment":
                continue
            spec = document.get("spec")
            if isinstance(spec, dict):
                return int(spec.get("replicas", 0) or 0)
        return 0

    def _stage_and_publish(
        self,
        repo_path: Path,
        manifest_path: Path,
        spec: WardenSpec,
        *,
        active: bool,
        removed: bool = False,
    ) -> None:
        from git import Repo

        repo = Repo(repo_path)
        relative_path = str(manifest_path.relative_to(repo_path))
        if removed:
            repo.index.remove([relative_path], working_tree=True, ignore_unmatch=True)
        else:
            repo.index.add([relative_path])
        if self._auto_commit:
            if repo.is_dirty(index=True, working_tree=True, untracked_files=True):
                repo.index.commit(
                    f"{self._commit_message_prefix} {spec.id} "
                    f"({'removed' if removed else 'active' if active else 'idle'})"
                )
            if self._auto_push:
                remote = repo.remotes[self._remote_name]
                remote.push()

    @staticmethod
    def _resource_name(spec: WardenSpec) -> str:
        return service_label(spec.id).replace(".", "-")

    @staticmethod
    def _remove_file(path: Path) -> None:
        try:
            path.unlink()
        except FileNotFoundError:
            return
