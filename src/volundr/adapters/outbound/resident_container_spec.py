"""Shared materialization contract for container-hosted resident runtimes."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from pathlib import PurePosixPath
from typing import Any

import yaml

from volundr.domain.models import ResidentEngine, ResidentRuntime

PLATFORM_ACCESS_TOKEN_ENV = "NIUU_VOLUNDR_ACCESS_TOKEN"
HERMES_API_SERVER_KEY_ENV = "API_SERVER_KEY"
HERMES_API_SERVER_DEFAULT_PORT = 8642
SANDBOX_HOME = "/sandbox"
SANDBOX_WORKSPACE = "/sandbox/workspace"
PROCESS_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass(frozen=True)
class ResidentContainerProcess:
    """One named process hosted inside a resident container or sandbox."""

    name: str
    command: tuple[str, ...]
    env: dict[str, str]
    files: dict[str, bytes]
    log_path: str


@dataclass(frozen=True)
class ResidentContainerSpec:
    """Backend-neutral image, process and service declaration for one resident.

    ``image`` is empty for runtimes that run the processes without an image.
    """

    image: str
    service_name: str
    service_port: int
    environment: dict[str, str]
    files: dict[str, bytes]
    processes: tuple[ResidentContainerProcess, ...]


def resident_flock_labels(runtime: ResidentRuntime, *, prefix: str) -> dict[str, str]:
    """Return common labels for one resident's optional flock membership."""
    if runtime.flock_id is None:
        return {}
    labels = {f"{prefix}/flock-id": str(runtime.flock_id)}
    if runtime.flock_member_id is not None:
        labels[f"{prefix}/flock-member-id"] = str(runtime.flock_member_id)
    if runtime.flock_role:
        labels[f"{prefix}/flock-role"] = runtime.flock_role
    if runtime.flock_peer_id:
        labels[f"{prefix}/flock-peer-id"] = runtime.flock_peer_id
    return labels


def resident_flock_environment(runtime: ResidentRuntime) -> dict[str, str]:
    """Return common runtime identity variables for a flock member."""
    if runtime.flock_id is None:
        return {}
    environment = {"NIUU_FLOCK_ID": str(runtime.flock_id)}
    if runtime.flock_member_id is not None:
        environment["NIUU_FLOCK_MEMBER_ID"] = str(runtime.flock_member_id)
    if runtime.flock_role:
        environment["NIUU_FLOCK_ROLE"] = runtime.flock_role
    if runtime.flock_peer_id:
        environment["NIUU_FLOCK_PEER_ID"] = runtime.flock_peer_id
    return environment


def resident_profile_values(profile_id: str, deployment: dict[str, Any]) -> dict[str, Any]:
    values = deployment.get("values")
    if not isinstance(values, dict):
        raise RuntimeError(f"Resident profile {profile_id!r} requires deployment.values")
    return values


def resident_runtime_section(values: dict[str, Any]) -> dict[str, Any]:
    """Return the shared runtime section, accepting the deployed OpenShell spelling."""
    runtime = values.get("runtime")
    if isinstance(runtime, dict):
        return runtime
    openshell = values.get("openshell")
    if isinstance(openshell, dict):
        return openshell
    return {}


def image_from_values(values: dict[str, Any], *, default: str = "") -> str:
    configured = values.get("image")
    if isinstance(configured, str):
        return configured or default
    if not isinstance(configured, dict):
        return default
    repository = str(configured.get("repository") or "").rstrip(":")
    tag = str(configured.get("tag") or "")
    if not repository:
        return default
    return f"{repository}:{tag}" if tag else repository


def resident_service(
    values: dict[str, Any],
    default_name: str,
    default_port: int,
) -> tuple[str, int]:
    service = resident_runtime_section(values).get("service")
    if not isinstance(service, dict):
        return default_name, default_port
    name = str(service.get("name") or default_name).strip()
    port = int(service.get("port") or default_port)
    if not name or port < 1 or port > 65535:
        raise RuntimeError("Resident service configuration is invalid")
    return name, port


def runtime_processes_from_values(
    values: dict[str, Any],
) -> tuple[ResidentContainerProcess, ...]:
    raw_processes = resident_runtime_section(values).get("processes")
    if not isinstance(raw_processes, list):
        return ()

    processes: list[ResidentContainerProcess] = []
    names: set[str] = set()
    for raw in raw_processes:
        if not isinstance(raw, dict):
            raise RuntimeError("Resident runtime process entries must be objects")
        name = str(raw.get("name") or "").strip()
        if not PROCESS_NAME_PATTERN.fullmatch(name) or name in names:
            raise RuntimeError(f"Resident runtime process has invalid name {name!r}")
        command = raw.get("command")
        if not isinstance(command, list) or not command:
            raise RuntimeError(f"Resident runtime process {name!r} has no command")
        command_parts = tuple(str(part) for part in command)
        if any(not part or "\x00" in part for part in command_parts):
            raise RuntimeError(f"Resident runtime process {name!r} has an invalid command")
        raw_files = raw.get("files") or {}
        if not isinstance(raw_files, dict):
            raise RuntimeError(f"Resident runtime process {name!r} files must be an object")
        files = {
            _resident_path(str(destination)): str(content).encode("utf-8")
            for destination, content in raw_files.items()
        }
        log_path = _resident_path(
            str(raw.get("logPath") or raw.get("log_path") or resident_process_log_path(name))
        )
        names.add(name)
        processes.append(
            ResidentContainerProcess(
                name=name,
                command=command_parts,
                env=_string_dict(raw.get("env") or {}),
                files=files,
                log_path=log_path,
            )
        )
    return tuple(processes)


def resident_process_log_path(name: str) -> str:
    """Default ``/sandbox`` log file for one named resident process."""
    return f"/sandbox/.volundr/{name}.log"


def resident_attribution_headers(runtime: ResidentRuntime) -> dict[str, str]:
    """Headers understood by Bifrost's authenticated usage tracker."""
    runtime_id = str(runtime.id)
    return {
        "X-Agent-ID": runtime_id,
        "X-Tenant-ID": runtime.tenant_id,
        "X-Session-ID": runtime_id,
    }


def resident_process_files(
    runtime: ResidentRuntime,
    files: dict[str, bytes],
) -> dict[str, bytes]:
    """Materialize engine-owned process files with runtime-specific identity."""
    materialized = dict(files)
    if runtime.engine is not ResidentEngine.OPENCLAW:
        return materialized

    provider_name, separator, _ = runtime.model.partition("/")
    if not separator:
        return materialized
    for path, content in tuple(materialized.items()):
        if not path.endswith("/.openclaw/openclaw.json"):
            continue
        try:
            config = json.loads(content)
            provider = config["models"]["providers"][provider_name]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise RuntimeError(f"OpenClaw resident configuration is invalid: {path!r}") from exc
        if not isinstance(config, dict) or not isinstance(provider, dict):
            raise RuntimeError(f"OpenClaw resident configuration is invalid: {path!r}")
        tools = config.setdefault("tools", {})
        if not isinstance(tools, dict):
            raise RuntimeError(f"OpenClaw tools configuration is invalid: {path!r}")
        exec_config = tools.setdefault("exec", {})
        if not isinstance(exec_config, dict):
            raise RuntimeError(f"OpenClaw exec configuration is invalid: {path!r}")
        exec_config.setdefault("security", "full")
        exec_config.setdefault("ask", "off")
        headers = provider.setdefault("headers", {})
        if not isinstance(headers, dict):
            raise RuntimeError(f"OpenClaw provider headers are invalid: {path!r}")
        headers.update(resident_attribution_headers(runtime))
        materialized[path] = json.dumps(config, indent=2).encode()
    return materialized


def materialize_resident_container(
    runtime: ResidentRuntime,
    values: dict[str, Any],
    *,
    default_image: str,
    default_service_name: str,
    default_service_port: int,
) -> ResidentContainerSpec:
    image = image_from_values(values, default=default_image)
    if not image:
        raise RuntimeError("Resident container image is required")
    spec = materialize_resident_runtime(
        runtime,
        values,
        default_service_name=default_service_name,
        default_service_port=default_service_port,
    )
    return replace(spec, image=image)


def materialize_resident_runtime(
    runtime: ResidentRuntime,
    values: dict[str, Any],
    *,
    default_service_name: str,
    default_service_port: int,
) -> ResidentContainerSpec:
    """Materialize processes, files and environment without selecting an image.

    Paths are expressed in the ``/sandbox`` namespace.
    """
    service_name, service_port = resident_service(
        values,
        default_service_name,
        default_service_port,
    )
    environment = _resident_environment(runtime, values, service_port)
    processes = runtime_processes_from_values(values)
    if resident_runtime_section(values).get("processMode") == "replace" and not processes:
        raise RuntimeError("Replacing resident processes requires at least one process")
    if not processes:
        raise RuntimeError(f"Resident engine {runtime.engine.value!r} requires a process")

    files: dict[str, bytes] = {}
    if runtime.engine is ResidentEngine.HERMES:
        files[f"{SANDBOX_WORKSPACE}/.hermes/config.yaml"] = yaml.safe_dump(
            _resident_hermes_config(runtime, values, service_port),
            sort_keys=False,
        ).encode()
    for process in processes:
        files.update(resident_process_files(runtime, process.files))
    return ResidentContainerSpec(
        image="",
        service_name=service_name,
        service_port=service_port,
        environment=environment,
        files=files,
        processes=processes,
    )


def _resident_environment(
    runtime: ResidentRuntime,
    values: dict[str, Any],
    service_port: int,
) -> dict[str, str]:
    env = {
        "HOME": SANDBOX_HOME,
        "CODEX_HOME": f"{SANDBOX_HOME}/.codex",
        "CLAUDE_CONFIG_DIR": f"{SANDBOX_HOME}/.claude",
        "SKULD__SESSION__ID": str(runtime.id),
        "SKULD__SESSION__NAME": runtime.name,
        "SKULD__SESSION__OWNER_ID": runtime.owner_id,
        "SKULD__SESSION__TENANT_ID": runtime.tenant_id,
        "SKULD__SESSION__MODEL": runtime.model,
        "SKULD__SESSION__WORKSPACE_DIR": SANDBOX_WORKSPACE,
        "SKULD__PERSISTENCE_MOUNT_PATH": SANDBOX_WORKSPACE,
        "SKULD__HOST": "0.0.0.0",
        "SKULD__PORT": str(service_port),
    }
    if runtime.engine is ResidentEngine.HERMES:
        env["HERMES_HOME"] = f"{SANDBOX_WORKSPACE}/.hermes"
    broker = values.get("broker")
    if isinstance(broker, dict):
        env.update(_resident_broker_environment(broker))
    extra_env = values.get("env")
    if isinstance(extra_env, dict):
        env.update(_string_dict(extra_env))
    return env


def _resident_broker_environment(broker: dict[str, Any]) -> dict[str, str]:
    env: dict[str, str] = {}
    fields = {
        "SKULD__CLI_TYPE": broker.get("cliType", broker.get("cli_type")),
        "SKULD__TRANSPORT": broker.get("transport"),
        "SKULD__TRANSPORT_ADAPTER": broker.get("transportAdapter", broker.get("transport_adapter")),
        "SKULD__APPROVAL_POLICY": broker.get("approvalPolicy", broker.get("approval_policy")),
        "SKULD__SANDBOX": broker.get("sandbox"),
    }
    for name, value in fields.items():
        if value is not None and str(value).strip():
            env[name] = str(value)
    if "skipPermissions" in broker or "skip_permissions" in broker:
        value = broker.get("skipPermissions", broker.get("skip_permissions"))
        env["SKULD__SKIP_PERMISSIONS"] = str(bool(value)).lower()
    return env


def _resident_hermes_config(
    runtime: ResidentRuntime,
    values: dict[str, Any],
    service_port: int,
) -> dict[str, Any]:
    from volundr.adapters.outbound.hermes_gateway import normalize_hermes_model_id

    resident = values.get("resident") if isinstance(values.get("resident"), dict) else {}
    llm = resident.get("llm") if isinstance(resident.get("llm"), dict) else {}
    provider = llm.get("provider") if isinstance(llm.get("provider"), dict) else {}
    kwargs = provider.get("kwargs") if isinstance(provider.get("kwargs"), dict) else {}
    base_url = str(kwargs.get("base_url") or kwargs.get("baseUrl") or "").strip()
    if not base_url:
        raise RuntimeError("Hermes residents require resident.llm.provider.kwargs.base_url")
    model_id = normalize_hermes_model_id(runtime.model)
    return {
        "model": {
            "default": model_id,
            "provider": "custom:niuu",
            "base_url": base_url,
            "api_mode": "chat_completions",
            "default_headers": resident_attribution_headers(runtime),
        },
        "custom_providers": [
            {
                "name": "niuu",
                "base_url": base_url,
                "key_env": PLATFORM_ACCESS_TOKEN_ENV,
                "api_mode": "chat_completions",
                "model": model_id,
            }
        ],
        "terminal": {"cwd": SANDBOX_WORKSPACE},
        "approvals": {"mode": "off"},
        "gateway": {
            "platforms": {
                "api_server": {
                    "enabled": True,
                    "extra": {"host": "0.0.0.0", "port": service_port},
                }
            }
        },
    }


def _resident_path(destination: str) -> str:
    path = PurePosixPath(destination.strip())
    normalized = str(path)
    if not path.is_absolute() or ".." in path.parts:
        raise RuntimeError(f"Resident file path is invalid: {destination!r}")
    if normalized != SANDBOX_HOME and not normalized.startswith(f"{SANDBOX_HOME}/"):
        raise RuntimeError(f"Resident file path is outside /sandbox: {destination!r}")
    if normalized == SANDBOX_HOME:
        raise RuntimeError("Resident file path must name a file")
    return normalized


def _string_dict(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): str(item)
        for key, item in value.items()
        if key and item and "\x00" not in str(key) and "\x00" not in str(item)
    }
